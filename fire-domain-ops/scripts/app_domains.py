# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.28"]
# ///
"""
firepikata 域名分配运维 —— 共享脚本（流程1 加域名 / 流程2 查空域名 / 流程3 分配）。

鉴权复用 huozhong 的 fire_login（子进程 `uv run fire_login.py` 取 token），
故本脚本只依赖 requests；所有请求带 X-Access-Token。

子命令：
  spare [--tld com] [--list] [--limit N] [--all-ages]          查待分配空域名;默认按 TLD 计数,--list 列具体域名
  add [--project P] [--ip IP] [--cf ACC] [--apply] DOMAIN...   给项目/备用加域名（batchAddDomains）
  allocate --project P --count N [--cf ACC] [--all-ages] [--apply]   给项目分配 N 个空域名（优先 .com）
  del --project P [--keep-cf] [--cf ACC] [--apply] DOMAIN...   把域名从项目整体移除（add 的逆操作：删 CF zone + 后端落库）

del 说明：后端 deleteBatch 是纯 DB 删除、不动 Cloudflare，故本命令默认**连带删 CF zone**
  （--keep-cf 可跳过）。CF 密钥走 lib/op_secrets.py 取（1Password「Cloudflare <账号>」）。
  宝塔层用 `bt.py del-domain` 单独删（与 add 流程一致：app_domains 管 CF+后端，bt.py 管宝塔）。
  幂等：某层已无该域名则自动跳过、不报错；跨项目保护：域名属于别的项目则不删其 CF zone。

域名分配铁律(spare/allocate 默认生效)：
  ① 只取**创建 ≤6 个月**的空域名（更老的可能临近过期/被风控）；
  ② 同窗口内**优先取老的**（createTime 升序）——把临近过期的先用掉。
  需要更老的(应急)时显式加 --all-ages。

默认 dry-run；加 --apply 才真正提交。写操作带日志(见 lib/runlog.py)，可 tail -F。
"""
import argparse
import re
import subprocess
import sys
try: sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception: pass
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
    _RETRY = Retry(total=6, connect=6, read=3, backoff_factor=0.6,
                   status_forcelist=[500, 502, 503, 504],
                   allowed_methods=frozenset(["GET", "POST"]))
except Exception:
    _RETRY = None

SESSION = requests.Session()
if _RETRY is not None:
    _adapter = HTTPAdapter(max_retries=_RETRY)
    SESSION.mount("https://", _adapter)
    SESSION.mount("http://", _adapter)

BASE = "https://firepikatacommon.huozhongtech.org"
UA = "fire-domain-ops/app_domains"
SPARE_IP = "128.241.233.59"          # 备用域名默认 IP
DEFAULT_CF = "hualee887@gmail.com"   # 默认 Cloudflare 账号
SPARE_MAX_AGE_MONTHS = 6             # 域名分配铁律:只取「创建≤6个月」的空域名(更老的可能临近过期/被风控)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
from runlog import RunLog  # noqa: E402
from op_secrets import get_secret  # noqa: E402

# ---- Cloudflare 直连（del 删 zone 用；add 的 zone 是后端建的，这里只负责删） ----
CF_API = "https://api.cloudflare.com/client/v4"
# CF 账号 → 1Password item（取 username / "API key"）。新账号在此登记即可。
CF_OP_ITEMS = {
    "hualee887@gmail.com": "5mmk2gypev7hf4l6furemc2nca",  # Cloudflare hualee887
    "puti666666@gmail.com": "py4du3mbx7ajm2zdzpnz25w23i",  # Cloudflare puti666666（注意 1P 字段是 "API Key" 大写K，get_secret 缓存优先可绕过）
}


def cf_creds(account):
    """取某 CF 账号的 (email, global_api_key)。优先环境变量/本地缓存，回退 1Password。"""
    item = CF_OP_ITEMS.get(account)
    if not item:
        sys.exit(f"❌ 未登记 CF 账号 {account} 的 1Password item（见 app_domains.py CF_OP_ITEMS）")
    email = get_secret(f"cf_email::{account}", op_item=item, op_field="username")
    key = get_secret(f"cf_apikey::{account}", op_item=item, op_field="API key")
    return email, key


def cf_headers(email, key):
    return {"X-Auth-Email": email, "X-Auth-Key": key, "Content-Type": "application/json"}


def cf_zone_id(email, key, domain):
    """查域名的 CF zone id；无则 None。"""
    r = SESSION.get(f"{CF_API}/zones", params={"name": domain},
                    headers=cf_headers(email, key), timeout=30)
    res = (r.json() or {}).get("result") or []
    return res[0]["id"] if res else None


def cf_delete_zone(email, key, zone_id):
    """删 CF zone，返回 (ok, msg)。"""
    r = SESSION.delete(f"{CF_API}/zones/{zone_id}", headers=cf_headers(email, key), timeout=60)
    try:
        j = r.json()
    except Exception:
        return False, r.text[:120]
    return bool(j.get("success")), (j.get("errors") or "ok")


# ---------- 鉴权 + API ----------
def get_token(tries=3):
    fl = PLUGIN_ROOT / "skills" / "domain-inventory" / "scripts" / "fire_login.py"
    last = ""
    for attempt in range(tries):
        try:
            r = subprocess.run(["uv", "run", str(fl)], capture_output=True, text=True, timeout=180)
        except FileNotFoundError:
            sys.exit("❌ 未找到 uv，请先安装 uv")
        tok = (r.stdout or "").strip()
        if r.returncode == 0 and tok:
            return tok
        last = (r.stderr or "").strip()
        if attempt < tries - 1:
            time.sleep(2)
    sys.exit(f"❌ 获取 token 失败（重试 {tries} 次）：{last[-400:]}")


def _headers(token):
    return {"X-Access-Token": token, "User-Agent": UA, "Content-Type": "application/json"}


def api_get(token, path, params=None):
    r = SESSION.get(BASE + path, params=params or {}, headers=_headers(token), timeout=60)
    try:
        j = r.json()
    except Exception:
        sys.exit(f"❌ GET {path} 返回非 JSON（HTTP {r.status_code}）：{r.text[:200]}")
    if not j.get("success"):
        sys.exit(f"❌ GET {path} 失败：{j.get('message') or j}")
    return j.get("result")


def api_post(token, path, body):
    """返回 (http_status, json)。不在此处退出，交调用方判断（用于探测 404 等）。"""
    r = SESSION.post(BASE + path, json=body, headers=_headers(token), timeout=120)
    try:
        j = r.json()
    except Exception:
        j = {"raw": r.text[:300]}
    return r.status_code, j


def api_delete(token, path, params=None):
    """DELETE（ids 等走 query 参数，对应后端 @RequestParam）。返回 (http_status, json)。"""
    r = SESSION.delete(BASE + path, params=params or {}, headers=_headers(token), timeout=120)
    try:
        j = r.json()
    except Exception:
        j = {"raw": r.text[:300]}
    return r.status_code, j


def fetch_list(token, path, page_size=500, params=None):
    out, page = [], 1
    while True:
        p = {"pageNo": page, "pageSize": page_size, "_t": 1}
        if params:
            p.update(params)
        res = api_get(token, path, p) or {}
        recs = res.get("records") or []
        out.extend(recs)
        total = res.get("total") or 0
        if not recs or len(out) >= total:
            break
        page += 1
    return out


# ---------- 解析 ----------
def resolve_cf(token, account=DEFAULT_CF):
    rows = fetch_list(token, "/api/app/appCloudFlareManager/list")
    for r in rows:
        if (r.get("account") or "").strip().lower() == account.strip().lower():
            return r["id"], r["account"]
    avail = ", ".join(filter(None, (r.get("account") for r in rows)))
    sys.exit(f"❌ 未找到 CF 账号 {account}；可选：{avail}")


def resolve_project(token, num):
    """解析项目：优先「字母前缀+数字」精确匹配(^[a-z]*<num>$，如 007→f007，不会误命中 f007-mn)，
    无精确命中再回退子串模糊。命中多个(如 f007 与 ptn007)则停下让用户指明。"""
    rows = fetch_list(token, "/api/app/appManager/list")
    num = str(num).strip()
    pat = re.compile(r"^[a-zA-Z]*" + re.escape(num) + r"$")
    matches = [r for r in rows if pat.match(r.get("projectCode") or "")]
    if not matches:  # 回退：子串模糊
        matches = [r for r in rows if num in (r.get("projectCode") or "")]
    if not matches:
        sys.exit(f"❌ 没找到含 '{num}' 的项目")
    if len(matches) > 1:
        opts = "、".join(f"{r.get('projectCode')}({r.get('name')})" for r in matches)
        sys.exit(f"⚠️ '{num}' 匹配到多个项目，请指明完整编号：{opts}")
    m = matches[0]
    appip = (m.get("appIp") or "").strip()
    if not appip:
        sys.exit(f"❌ 项目 {m.get('projectCode')} 的 appIp 为空，无法确定目标IP；请用 --ip 指定")
    return {"appId": m["id"], "projectCode": m.get("projectCode"), "appIp": appip, "name": m.get("name")}


# ---------- 工具 ----------
def spare_pool(domains):
    """项目(appId)空 + 备注(remark)空 + 状态正常(1)。"""
    pool = []
    for d in domains:
        if (not d.get("appId")) and (not d.get("remark")) and d.get("status") in (1, "1"):
            pool.append(d)
    return pool


def _created_dt(d):
    """解析 createTime('YYYY-MM-DD HH:MM:SS')→datetime;解析失败返回 None。"""
    s = (d.get("createTime") or "").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def eligible_spare(domains, months=SPARE_MAX_AGE_MONTHS, all_ages=False, tld_filter=None):
    """空域名分配候选 —— 应用「域名分配铁律」(见 README-domain.md):
      ① 只取**创建 ≤ months 个月**的(更老的可能临近过期/被风控);`all_ages=True` 关闭此限。
      ② 同窗口内**优先取老的**(createTime 升序)——把临近过期的先用掉。
    无 createTime 的:限龄时排除(无法确认年龄,保守),不限龄时垫底。
    `tld_filter` 可只留某后缀(如 'com')。返回已按①②筛序的列表。
    """
    pool = spare_pool(domains)
    if tld_filter:
        suf = tld_filter.lower().lstrip(".")
        pool = [d for d in pool if tld(d.get("domain", "")) == suf]
    if not all_ages:
        cutoff = datetime.now() - timedelta(days=months * 30)
        pool = [d for d in pool if (_created_dt(d) or datetime.min) >= cutoff]
    pool.sort(key=lambda d: _created_dt(d) or datetime.max)  # 优先取老的
    return pool


def clean_domains(args):
    seen, out = set(), []
    for a in args:
        d = a.strip().lower()
        if "://" in d:
            d = d.split("://", 1)[1]
        d = d.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].strip().strip(".")
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def tld(domain):
    return domain.rsplit(".", 1)[-1] if "." in domain else domain


def verify_landed(token, domains, rounds=12, gap=5):
    """轮询 appDomainManager/list 核对落库（status=1）。返回 {domain: bool}。

    注意：后端 list 接口带 ?domain= 过滤参数时会 500（"操作失败，null"），
    故这里改为不带过滤的全量分页拉取后在本地匹配，避开该坑。"""
    pending = set(domains)
    landed = {}
    for _ in range(rounds):
        rows = fetch_list(token, "/api/app/appDomainManager/list", 2000)
        status_by_domain = {r.get("domain"): r.get("status")
                            for r in rows if r.get("domain")}
        for d in list(pending):
            if status_by_domain.get(d) in (1, "1"):
                landed[d] = True
                pending.discard(d)
        if not pending:
            break
        time.sleep(gap)
    for d in pending:
        landed[d] = False
    return landed


# ---------- 流程2：spare ----------
def cmd_spare(args):
    token = get_token()
    domains = fetch_list(token, "/api/app/appDomainManager/list", 2000)
    all_ages = getattr(args, "all_ages", False)
    tldf = getattr(args, "tld", None)
    total = len(spare_pool(domains))
    pool = eligible_spare(domains, all_ages=all_ages, tld_filter=tldf)  # 已按「≤6月+优先取老」筛序
    scope = "全部年龄" if all_ages else f"创建≤{SPARE_MAX_AGE_MONTHS}个月"
    head = f"待分配空域名（项目空+备注空+状态正常 | {scope}"
    if tldf:
        head += f" | .{tldf.lower().lstrip('.')}"
    head += f"）：{len(pool)} 个  (空池总计 {total} 个)"
    print(head)
    if getattr(args, "list", False):
        lim = getattr(args, "limit", None) or len(pool)
        print(f"  ↓ 优先取老的(createTime 升序)，前 {min(lim, len(pool))} 个：")
        for d in pool[:lim]:
            print(f"  {d.get('domain')}  | id={d.get('id')} | 建={d.get('createTime')} | ip={d.get('ip')}")
        if not pool:
            print("  （无符合条件的空域名）")
    else:
        c = Counter(tld(d["domain"]) for d in pool if d.get("domain"))
        for suf, n in sorted(c.items(), key=lambda x: -x[1]):
            print(f"  {suf}: {n} 个")
        if not pool:
            print("  （无符合条件的空域名）")
        print("  提示：加 --list [--tld com] [--limit N] 看具体域名（按规则优先取老）；--all-ages 放宽年龄。")


# ---------- 流程1：add ----------
def cmd_add(args):
    token = get_token()
    domains = clean_domains(args.domains)
    if not domains:
        sys.exit("❌ 没有有效域名")
    cf_id, cf_acc = resolve_cf(token, args.cf)
    if args.project:
        proj = resolve_project(token, args.project)
        app_id, target_ip = proj["appId"], (args.ip or proj["appIp"])
        proj_desc = f"{proj['projectCode']}（{proj['name']}, appId={app_id}）"
    else:
        proj, app_id = None, None
        target_ip = args.ip or SPARE_IP
        proj_desc = "备用域名（未指定项目）"

    rl = RunLog("add-domains")
    rl.header(f"流程1 加域名 {'[APPLY]' if args.apply else '[DRY-RUN]'}")
    rl.log(f"项目: {proj_desc}")
    rl.log(f"目标IP: {target_ip}  CF账号: {cf_acc}")
    rl.log(f"域名({len(domains)}): {', '.join(domains)}")
    if not args.apply:
        rl.log("→ DRY-RUN：未提交。确认后加 --apply 执行。")
        print("\n" + rl.tail_cmd())
        rl.close()
        return

    body = {"cloudFlareManagerId": cf_id, "domains": domains, "targetIp": target_ip}
    if app_id:
        body["appId"] = app_id
    code, j = api_post(token, "/api/app/appDomainManager/batchAddDomains", body)
    rl.log(f"提交 batchAddDomains: HTTP {code} / {j.get('message') or j}")
    if code != 200 or not j.get("success"):
        rl.summary(0, len(domains), domains)
        rl.close()
        sys.exit(1)
    rl.log("后端异步处理中，轮询回查落库…")
    landed = verify_landed(token, domains)
    ok = [d for d in domains if landed.get(d)]
    fail = [d for d in domains if not landed.get(d)]
    for d in domains:
        rl.step(domains.index(d) + 1, len(domains), d, "✅ 已落库" if landed.get(d) else "⏳/❌ 未见")
    rl.summary(len(ok), len(fail), fail)
    print("\n" + rl.tail_cmd())
    rl.close()


# ---------- 流程3：allocate ----------
def cmd_allocate(args):
    token = get_token()
    proj = resolve_project(token, args.project)
    app_id, app_ip = proj["appId"], proj["appIp"]
    cf_id, cf_acc = resolve_cf(token, args.cf)  # 解析以备校验（editBatch 不需要，但确认账号存在）

    domains = fetch_list(token, "/api/app/appDomainManager/list", 2000)
    all_ages = getattr(args, "all_ages", False)
    com = eligible_spare(domains, all_ages=all_ages, tld_filter="com")  # 「≤6月+优先取老」筛序
    n = args.count
    if len(com) < n:
        scope = "全部年龄" if all_ages else f"创建≤{SPARE_MAX_AGE_MONTHS}个月"
        sys.exit(f"⛔ 符合条件的 .com 空域名只有 {len(com)} 个（{scope}），不足 {n} 个。"
                 f" 可加 --all-ages 放宽年龄，或减少数量。")
    chosen = com[:n]
    ids = [d["id"] for d in chosen]
    names = [d["domain"] for d in chosen]

    rl = RunLog("allocate-domains")
    rl.header(f"流程3 分配 {n} 个空域名 {'[APPLY]' if args.apply else '[DRY-RUN]'}")
    rl.log(f"项目: {proj['projectCode']}（{proj['name']}, appId={app_id}）  目标IP(appIp): {app_ip}")
    rl.log(f"选中(.com {n} | ≤{SPARE_MAX_AGE_MONTHS}月+优先取老): {', '.join(names)}")
    if not args.apply:
        rl.log("→ DRY-RUN：未提交。确认后加 --apply 执行。")
        print("\n" + rl.tail_cmd())
        rl.close()
        return

    # 1) editBatch 绑项目
    code, j = api_post(token, "/api/app/appDomainManager/editBatch", {"ids": ids, "appId": app_id})
    rl.log(f"editBatch 绑项目: HTTP {code} / {j.get('message') or j}")
    if code != 200 or not j.get("success"):
        rl.summary(0, n, names)
        rl.close()
        sys.exit(1)

    # 2) batchUpdateIp 原地重指 CF（需后端已部署该接口）
    code, j = api_post(token, "/api/app/appDomainManager/batchUpdateIp", {"ids": ids, "ip": app_ip})
    if code == 404:
        rl.log("⚠️ batchUpdateIp 返回 404 —— 后端尚未部署该接口！项目已绑定(editBatch 成功)，"
               "但 CF A 记录未重指。请部署 batchUpdateIp 后重跑重指，或暂用后台『应用IP更新』。")
        rl.summary(n, 0)
        print("\n" + rl.tail_cmd())
        rl.close()
        sys.exit(2)
    rl.log(f"batchUpdateIp 重指: HTTP {code} / {j.get('message') or j}")
    if code != 200 or not j.get("success"):
        rl.summary(n, 0)  # 项目已绑，IP 重指失败
        rl.close()
        sys.exit(1)

    rl.log("回查落库…")
    landed = verify_landed(token, names)
    for i, d in enumerate(names, 1):
        rl.step(i, n, d, "✅ 已分配" if landed.get(d) else "⏳/❌ 未见")
    okn = sum(1 for d in names if landed.get(d))
    rl.summary(okn, n - okn, [d for d in names if not landed.get(d)])
    print("\n" + rl.tail_cmd())
    rl.close()


# ---------- 流程：del（add 的逆操作：删 CF zone + 后端落库） ----------
def cmd_del(args):
    token = get_token()
    domains = clean_domains(args.domains)
    if not domains:
        sys.exit("❌ 没有有效域名")
    proj = resolve_project(token, args.project)
    app_id = str(proj["appId"])

    # 后端：全量拉取，本地按 appId 建 域名→id 映射（list 带 ?domain= 会 500，故全量本地匹配）
    rows = fetch_list(token, "/api/app/appDomainManager/list", 2000)
    by_dom_here = {}      # 本项目下 域名→记录id
    appid_of = {}         # 全局 域名→appId（跨项目保护用）
    for r in rows:
        dom = r.get("domain")
        if not dom:
            continue
        appid_of.setdefault(dom, str(r.get("appId") or ""))
        if str(r.get("appId") or "") == app_id:
            by_dom_here.setdefault(dom, r.get("id"))
    in_backend = [(d, by_dom_here[d]) for d in domains if d in by_dom_here]
    not_backend = [d for d in domains if d not in by_dom_here]

    # CF：默认连带删 zone；跨项目保护（域名属于别的项目 → 不删其 zone）
    do_cf = not args.keep_cf
    cf_email = cf_key = None
    cf_zone = {}          # 域名→zone_id（仅可删的）
    cf_foreign = []       # 属于别项目、跳过删 zone 的
    if do_cf:
        cf_email, cf_key = cf_creds(args.cf)
        for d in domains:
            owner = appid_of.get(d)
            if owner and owner != app_id:
                cf_foreign.append(d)
                continue
            zid = cf_zone_id(cf_email, cf_key, d)
            if zid:
                cf_zone[d] = zid

    rl = RunLog("del-domains")
    rl.header(f"流程 删域名 {'[APPLY]' if args.apply else '[DRY-RUN]'}")
    rl.log(f"项目: {proj['projectCode']}（{proj['name']}, appId={app_id}）")
    rl.log(f"域名({len(domains)}): {', '.join(domains)}")
    rl.log(f"后端待删 {len(in_backend)} | 后端无(已删/未绑) {len(not_backend)}"
           + (f"：{', '.join(not_backend)}" if not_backend else ""))
    if do_cf:
        rl.log(f"CF zone 待删 {len(cf_zone)} | CF 无 {len(domains)-len(cf_zone)-len(cf_foreign)}"
               + (f" | ⚠️跨项目跳过 {len(cf_foreign)}：{', '.join(cf_foreign)}" if cf_foreign else ""))
    else:
        rl.log("CF：--keep-cf，跳过删 zone")
    rl.log("宝塔层请另跑：bt.py del-domain <项目号> <域名...> --apply（建议 --exclude-host 跳测试机）")

    if not args.apply:
        rl.log("→ DRY-RUN：未执行。确认后加 --apply。")
        print("\n" + rl.tail_cmd())
        rl.close()
        return

    # 执行：先删 CF zone，再删后端记录
    cf_ok = cf_fail = 0
    if do_cf:
        for d in domains:
            zid = cf_zone.get(d)
            if not zid:
                continue
            ok, msg = cf_delete_zone(cf_email, cf_key, zid)
            cf_ok += ok
            cf_fail += (not ok)
            rl.log(f"  CF {'✓' if ok else '✗'} {d}" + ("" if ok else f"  {msg}"))
        rl.log(f"CF zone：删除 {cf_ok} | 失败 {cf_fail}")

    if in_backend:
        ids = ",".join(str(i) for _, i in in_backend)
        code, j = api_delete(token, "/api/app/appDomainManager/deleteBatch", {"ids": ids})
        ok = code == 200 and j.get("success")
        rl.log(f"后端 deleteBatch {len(in_backend)} 条：HTTP {code} / {j.get('message') or j}")
        rl.summary(len(in_backend) if ok else 0, 0 if ok else len(in_backend),
                   [] if ok else [d for d, _ in in_backend])
    else:
        rl.log("后端无待删记录。")
    print("\n" + rl.tail_cmd())
    rl.close()


def main():
    ap = argparse.ArgumentParser(description="firepikata 域名分配运维")
    sub = ap.add_subparsers(dest="subcmd", required=True)

    sp = sub.add_parser("spare", help="查待分配空域名(默认按 TLD 计数;--list 列具体域名,优先取老)")
    sp.add_argument("--list", action="store_true", help="列出具体域名(createTime 升序,优先取老)而非只计数")
    sp.add_argument("--tld", help="只看某后缀(如 com)")
    sp.add_argument("--limit", type=int, help="--list 时最多列出多少个")
    sp.add_argument("--all-ages", action="store_true", help=f"放宽:含创建>{SPARE_MAX_AGE_MONTHS}个月的(默认只取≤{SPARE_MAX_AGE_MONTHS}月)")
    sp.set_defaults(func=cmd_spare)

    ad = sub.add_parser("add", help="给项目/备用加域名")
    ad.add_argument("domains", nargs="+")
    ad.add_argument("--project", help="项目号（数字，模糊匹配 projectCode）；不填=备用域名")
    ad.add_argument("--ip", help="目标IP覆盖（默认：项目 appIp / 备用 128.241.233.59）")
    ad.add_argument("--cf", default=DEFAULT_CF, help=f"CF账号（默认 {DEFAULT_CF}）")
    ad.add_argument("--apply", action="store_true", help="真提交（默认 dry-run）")
    ad.set_defaults(func=cmd_add)

    al = sub.add_parser("allocate", help="给项目分配 N 个空域名（优先 .com）")
    al.add_argument("--project", required=True, help="项目号（数字，模糊匹配）")
    al.add_argument("--count", type=int, required=True, help="分配数量 N")
    al.add_argument("--cf", default=DEFAULT_CF, help=f"CF账号（默认 {DEFAULT_CF}）")
    al.add_argument("--all-ages", action="store_true", help=f"放宽:含创建>{SPARE_MAX_AGE_MONTHS}个月的(默认只取≤{SPARE_MAX_AGE_MONTHS}月+优先取老)")
    al.add_argument("--apply", action="store_true", help="真提交（默认 dry-run）")
    al.set_defaults(func=cmd_allocate)

    dl = sub.add_parser("del", help="把域名从项目整体移除（删 CF zone + 后端落库；add 的逆操作）")
    dl.add_argument("domains", nargs="+")
    dl.add_argument("--project", required=True, help="项目号（数字，模糊匹配 projectCode）")
    dl.add_argument("--cf", default=DEFAULT_CF, help=f"CF账号（默认 {DEFAULT_CF}）")
    dl.add_argument("--keep-cf", action="store_true", help="不删 CF zone（只删后端落库）")
    dl.add_argument("--apply", action="store_true", help="真执行（默认 dry-run）")
    dl.set_defaults(func=cmd_del)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
