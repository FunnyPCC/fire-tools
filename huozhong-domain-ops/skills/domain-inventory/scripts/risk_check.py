#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.28"]
# ///
"""
批量检测域名是否被 Google 判定为风险站点(欺骗性网页/恶意软件/有害软件)。

数据源:Google Safe Browsing API v4(GSC「安全问题」同源;官方 Search Console API
不暴露安全问题)。key 经 op_secrets 解析(SB_API_KEY > 本地缓存 sb_api_key >
1Password「Google Maps API」字段「API Key」,GCP 项目 324812159513 需启用 Safe Browsing API)。

默认只查「域名状态=正常」的域名;也可 `... a.com b.com` 只查指定域名。
输出 ./域名维护/域名风险.{md,csv}(目录可用 DOMAIN_DOC_DIR 覆盖,请在项目根运行)。
"""
import csv
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from op_secrets import get_secret, refresh_secret  # noqa: E402

DOC_DIR = Path(os.environ.get("DOMAIN_DOC_DIR", "域名维护"))
RAW_DOMAINS = DOC_DIR / "raw_domains.json"
RAW_APPS = DOC_DIR / "raw_apps.json"
CSV_IN = DOC_DIR / "域名项目关联.csv"
OUT_CSV = DOC_DIR / "域名风险.csv"
OUT_MD = DOC_DIR / "域名风险.md"

OP_ITEM = "Google Maps API"
OP_FIELD = "API Key"
SB_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
THREAT_TYPES = ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE",
                "POTENTIALLY_HARMFUL_APPLICATION"]
ZH = {"SOCIAL_ENGINEERING": "欺骗性网页", "MALWARE": "恶意软件",
      "UNWANTED_SOFTWARE": "有害软件", "POTENTIALLY_HARMFUL_APPLICATION": "有害应用"}


def get_key(force=False) -> str:
    if force:
        return refresh_secret("sb_api_key", OP_ITEM, OP_FIELD)
    return get_secret("sb_api_key", OP_ITEM, OP_FIELD, env="SB_API_KEY")


def load_domains():
    """[(项目编号, 域名)]。命令行参数 > raw_domains.json(仅域名状态正常)> 主表 CSV。"""
    args = [a.strip().lower() for a in sys.argv[1:] if a.strip()]
    if args:
        return [("", d) for d in args]
    if RAW_DOMAINS.exists():
        app_code = {}
        if RAW_APPS.exists():
            for a in json.load(open(RAW_APPS, encoding="utf-8")):
                app_code[str(a["id"])] = a.get("projectCode") or a.get("name") or ""
        rows = []
        for d in json.load(open(RAW_DOMAINS, encoding="utf-8")):
            dom = (d.get("domain") or "").strip().lower()
            if not dom or (d.get("status_dictText") or "") != "正常":
                continue
            rows.append((app_code.get(str(d.get("appId") or ""), ""), dom))
        return rows
    rows = []
    with open(CSV_IN, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = (r.get("域名") or "").strip().lower()
            if d:
                rows.append(((r.get("项目编号") or "").strip(), d))
    return rows


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def query(urls, key):
    body = {"client": {"clientId": "huozhong-risk-check", "clientVersion": "1.0"},
            "threatInfo": {"threatTypes": THREAT_TYPES, "platformTypes": ["ANY_PLATFORM"],
                           "threatEntryTypes": ["URL"],
                           "threatEntries": [{"url": u} for u in urls]}}
    return requests.post(SB_URL, params={"key": key}, json=body, timeout=60)


def main():
    key = get_key()
    pairs = load_domains()
    code_of = {}
    for code, d in pairs:
        code_of.setdefault(d, code)
    domains = list(code_of)
    print(f"待检测域名:{len(domains)} 个")

    url_to_dom = {}
    for d in domains:
        for u in (f"http://{d}/", f"https://{d}/"):
            url_to_dom[u] = d
    urls = list(url_to_dom)

    hits, hit_url, refreshed = {}, {}, False
    for batch in chunks(urls, 500):
        resp = query(batch, key)
        if resp.status_code in (401, 403) and not refreshed:   # key 失效/受限 → 回 1P 重取一次
            print("  ↻ key 被拒,尝试从 1Password 重取…")
            key = get_key(force=True)
            refreshed = True
            resp = query(batch, key)
        if resp.status_code != 200:
            sys.exit(f"  接口错误 HTTP {resp.status_code}: {resp.text[:300]}")
        for m in resp.json().get("matches", []):
            u = m["threat"]["url"]
            d = url_to_dom.get(u) or url_to_dom.get(u.rstrip("/") + "/")
            if d:
                hits.setdefault(d, set()).add(m["threatType"])
                hit_url.setdefault(d, u)

    DOC_DIR.mkdir(parents=True, exist_ok=True)
    risky = []
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["项目编号", "域名", "风险", "威胁类型", "命中URL"])
        for d in sorted(domains, key=lambda x: (x not in hits, code_of[x], x)):
            ts = hits.get(d)
            if ts:
                zh = "、".join(ZH.get(t, t) for t in sorted(ts))
                w.writerow([code_of[d], d, "⚠️风险", zh, hit_url.get(d, "")])
                risky.append((code_of[d], d, zh))
            else:
                w.writerow([code_of[d], d, "正常", "", ""])

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("# 域名风险检测(Google Safe Browsing)\n\n")
        f.write("> 数据源:Safe Browsing API v4(与 GSC「安全问题」同一威胁数据)\n")
        f.write(f"> 检测域名:**{len(domains)}**  ｜  ⚠️ 命中风险:**{len(risky)}**\n\n")
        if risky:
            f.write("## 风险域名\n\n| 项目编号 | 域名 | 威胁类型 |\n|---|---|---|\n")
            for code, d, zh in risky:
                f.write(f"| {code} | {d} | {zh} |\n")
        else:
            f.write("✅ 未发现被 Safe Browsing 标记的域名。\n")

    print(f"⚠️ 风险域名:{len(risky)} / {len(domains)}")
    for code, d, zh in risky[:50]:
        print(f"   [{code or '-'}] {d}  →  {zh}")
    if len(risky) > 50:
        print(f"   …… 其余 {len(risky) - 50} 个见 {OUT_CSV.name}")
    print(f"\n输出:{OUT_CSV}\n      {OUT_MD}")


if __name__ == "__main__":
    main()
