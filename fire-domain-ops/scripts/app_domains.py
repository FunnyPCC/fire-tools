# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.28"]
# ///
"""
firepikata 域名分配运维 —— 共享脚本（流程1 加域名 / 流程2 查空域名 / 流程3 分配）。

鉴权复用 huozhong 的 fire_login（子进程 `uv run fire_login.py` 取 token），
故本脚本只依赖 requests；所有请求带 X-Access-Token。

子命令：
  spare                                  统计待分配空域名（项目空+备注空+状态正常），按 TLD 分组
  add [--project P] [--ip IP] [--cf ACC] [--apply] DOMAIN...   给项目/备用加域名（batchAddDomains）
  allocate --project P --count N [--cf ACC] [--apply]          给项目分配 N 个空域名（优先 .com）

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

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
from runlog import RunLog  # noqa: E402


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
    """轮询 appDomainManager/list 按域名核对落库（status=1）。返回 {domain: bool}。"""
    pending = set(domains)
    landed = {}
    for _ in range(rounds):
        for d in list(pending):
            res = api_get(token, "/api/app/appDomainManager/list",
                          {"domain": d, "pageNo": 1, "pageSize": 5, "_t": 1}) or {}
            recs = res.get("records") or []
            if any((x.get("domain") == d and x.get("status") in (1, "1")) for x in recs):
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
    pool = spare_pool(domains)
    c = Counter(tld(d["domain"]) for d in pool if d.get("domain"))
    print(f"待分配空域名（项目空 + 备注空 + 状态正常）：共 {len(pool)} 个")
    for suf, n in sorted(c.items(), key=lambda x: -x[1]):
        print(f"  {suf}: {n} 个")
    if not pool:
        print("  （当前没有空域名）")


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
    pool = spare_pool(domains)
    com = [d for d in pool if tld(d.get("domain", "")) == "com"]
    n = args.count
    if len(com) < n:
        sys.exit(f"⛔ .com 空域名只有 {len(com)} 个，不足 {n} 个。"
                 f"（空域名池共 {len(pool)} 个）请减少数量或确认是否用其他后缀。")
    chosen = com[:n]
    ids = [d["id"] for d in chosen]
    names = [d["domain"] for d in chosen]

    rl = RunLog("allocate-domains")
    rl.header(f"流程3 分配 {n} 个空域名 {'[APPLY]' if args.apply else '[DRY-RUN]'}")
    rl.log(f"项目: {proj['projectCode']}（{proj['name']}, appId={app_id}）  目标IP(appIp): {app_ip}")
    rl.log(f"选中(.com {n}): {', '.join(names)}")
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


def main():
    ap = argparse.ArgumentParser(description="firepikata 域名分配运维")
    sub = ap.add_subparsers(dest="subcmd", required=True)

    sp = sub.add_parser("spare", help="统计待分配空域名（按 TLD）")
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
    al.add_argument("--apply", action="store_true", help="真提交（默认 dry-run）")
    al.set_defaults(func=cmd_allocate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
