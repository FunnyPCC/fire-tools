#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
由 raw_domains.json / raw_apps.json(+ 域名风险.csv、域名存活.csv,均可选)生成主关联表:
    ./域名维护/域名项目关联.md / .csv

列:项目编号 | 域名 | 项目状态 | 域名状态 | Google风险 | 可访问 | 可用状态 | 备注
- 项目状态/域名状态:数据源原始状态(「启用」只在项目级;域名级是正常/已关闭/维护中)
- Google风险:域名风险.csv;可访问:域名存活.csv;无则「—」
- 可用状态/备注:留空供人工维护;若主表已存在,自动按域名沿用旧值
- 关联:域名.appId == 项目.id → projectCode;取不到标(未绑定项目)/(已删除项目·<id>)

输出目录可用 DOMAIN_DOC_DIR 覆盖,请在项目根运行。
"""
import csv
import datetime
import json
import os
from collections import Counter
from pathlib import Path

DOC_DIR = Path(os.environ.get("DOMAIN_DOC_DIR", "域名维护"))
CSV_OUT = DOC_DIR / "域名项目关联.csv"
MD_OUT = DOC_DIR / "域名项目关联.md"
RISK_CSV = DOC_DIR / "域名风险.csv"
LIVE_CSV = DOC_DIR / "域名存活.csv"
DATE = datetime.date.today().isoformat()

COLS = ["项目编号", "域名", "项目状态", "域名状态", "Google风险", "可访问", "可用状态", "备注"]


def load_json(name):
    return json.load(open(DOC_DIR / name, encoding="utf-8"))


def esc(s):
    return str(s).replace("|", "\\|").strip()


def main():
    domains = load_json("raw_domains.json")
    apps = load_json("raw_apps.json")
    app_by_id = {str(a["id"]): a for a in apps}

    risk = {}
    if RISK_CSV.exists():
        for r in csv.DictReader(open(RISK_CSV, encoding="utf-8-sig")):
            risk[r["域名"].strip().lower()] = (r["风险"].startswith("⚠"), r["威胁类型"].strip())

    live = {}
    if LIVE_CSV.exists():
        for r in csv.DictReader(open(LIVE_CSV, encoding="utf-8-sig")):
            live[r["域名"].strip().lower()] = r.get("可访问", "")

    manual = {}
    if CSV_OUT.exists():
        for r in csv.DictReader(open(CSV_OUT, encoding="utf-8-sig")):
            manual[r["域名"].strip().lower()] = (r.get("可用状态", "") or "", r.get("备注", "") or "")

    def code_of(d):
        aid = str(d.get("appId") or "")
        if not aid:
            return "(未绑定项目)", 2, ""
        a = app_by_id.get(aid)
        if a:
            return (a.get("projectCode") or a.get("name") or aid), 0, (a.get("status_dictText") or "")
        return f"(已删除项目·{aid})", 1, ""

    def risk_cell(dom):
        info = risk.get(dom)
        if not info:
            return "—"
        return ("⚠️ " + (info[1] or "风险")) if info[0] else "✅ 正常"

    rows = []
    for d in domains:
        code, grp, pstatus = code_of(d)
        dom = (d.get("domain") or "").strip().lower()
        st, rk = manual.get(dom, ("", ""))
        rows.append({"code": code, "grp": grp, "domain": dom, "appId": str(d.get("appId") or ""),
                     "pstatus": pstatus, "dstatus": d.get("status_dictText") or "",
                     "risk": risk_cell(dom), "is_risk": bool(risk.get(dom, (False,))[0]),
                     "live": live.get(dom, "—"), "status": st, "remark": rk})
    rows.sort(key=lambda r: (r["grp"], r["code"], r["domain"]))

    cnt = Counter(r["code"] for r in rows)
    rcnt = Counter(r["code"] for r in rows if r["is_risk"])
    total_risk = sum(1 for r in rows if r["is_risk"])
    g = Counter(r["grp"] for r in rows)

    DOC_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in rows:
            w.writerow([r["code"], r["domain"], r["pstatus"], r["dstatus"],
                        r["risk"], r["live"], r["status"], r["remark"]])

    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("# 域名 × 项目 关联表\n\n")
        f.write("> 数据来源:`firepikatacommon.huozhongtech.org`(appDomainManager / appManager)；风险=Safe Browsing；可访问=存活检测\n")
        f.write(f"> 生成时间:{DATE}\n")
        f.write(f"> 域名总数:**{len(rows)}**  ｜  项目总数:**{len(apps)}**  ｜  ⚠️ Google 风险域名:**{total_risk}**\n>\n")
        f.write(f"> 关联情况:✅ 正常关联 **{g[0]}** ｜ ⚠️ 项目已删除/不可查 **{g[1]}** ｜ ⚠️ 未绑定项目 **{g[2]}**\n\n")
        f.write("> 状态来自数据源:项目状态(启用/关闭/维护中)、域名状态(正常/已关闭/维护中)。可用=域名状态正常+Google正常+可访问。\n\n")

        f.write("## 一、项目汇总(正常关联,按风险数→域名数)\n\n")
        f.write("| 项目编号 | 项目名 | 项目状态 | 后台域名 | 项目IP | 域名数 | ⚠️风险数 |\n|---|---|---|---|---|---:|---:|\n")
        seen, proj = set(), []
        for r in rows:
            if r["grp"] != 0 or r["code"] in seen:
                continue
            seen.add(r["code"])
            proj.append((r["code"], app_by_id.get(r["appId"])))
        proj.sort(key=lambda x: (-rcnt[x[0]], -cnt[x[0]], x[0]))
        for code, a in proj:
            rc = rcnt[code]
            f.write(f"| {esc(code)} | {esc(a.get('name') or '')} | {esc(a.get('status_dictText') or '')} | {esc(a.get('adminDomain_dictText') or '')} | {esc(a.get('appIp') or a.get('ip') or '')} | {cnt[code]} | {('**' + str(rc) + '**') if rc else '0'} |\n")

        f.write("\n## 二、域名明细\n\n")
        f.write("| 项目编号 | 域名 | 项目状态 | 域名状态 | Google风险 | 可访问 | 可用状态 | 备注 |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {esc(r['code'])} | {esc(r['domain'])} | {esc(r['pstatus'])} | {esc(r['dstatus'])} | {esc(r['risk'])} | {esc(r['live'])} | {esc(r['status'])} | {esc(r['remark'])} |\n")

    note = []
    if not risk:
        note.append("无 域名风险.csv")
    if not live:
        note.append("无 域名存活.csv")
    print(f"✅ 生成:域名 {len(rows)}，风险 {total_risk}，受影响项目 {len(rcnt)}"
          f"{'（' + '、'.join(note) + '）' if note else ''}")


if __name__ == "__main__":
    main()
