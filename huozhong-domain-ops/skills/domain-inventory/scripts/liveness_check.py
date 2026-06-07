#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.28"]
# ///
"""
域名存活检测:判断域名能否打开(HTTP 是否有响应)。与 Google风险 无关。

- 依次尝试 https:// 再 http://,跟随重定向,带超时;有任意 HTTP 状态=可访问,超时/连不上=打不开
- 判死的域名会自动降并发、放宽超时复测一遍,消除代理拥塞造成的假死
- 走本机网络(开了 Clash/Surge 则与浏览器一致)

用法(项目根运行):
  uv run <plugin>/.../liveness_check.py            # 检测 raw_domains.json 中域名状态正常的,写 ./域名维护/域名存活.csv
  uv run <plugin>/.../liveness_check.py a.com b.com # 只测指定域名(仅打印,不写文件)
"""
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOC_DIR = Path(os.environ.get("DOMAIN_DOC_DIR", "域名维护"))
RAW_DOMAINS = DOC_DIR / "raw_domains.json"
OUT_CSV = DOC_DIR / "域名存活.csv"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = (6, 8)
WORKERS = 24
RETRY_TIMEOUT = (10, 15)
RETRY_WORKERS = 10


def reg_domain(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def check(domain: str, timeout=TIMEOUT) -> dict:
    domain = domain.strip().lower()
    last_err = ""
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}/"
        for verify in (True, False):
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout,
                                 allow_redirects=True, verify=verify)
                final = r.url
                try:
                    fhost = requests.utils.urlparse(final).hostname or ""
                except Exception:
                    fhost = ""
                jumped = reg_domain(fhost) != reg_domain(domain) if fhost else False
                note = "TLS警告" if verify is False else ""
                if jumped:
                    note = (note + " 跳转外站").strip()
                return {"domain": domain, "alive": True, "code": r.status_code,
                        "final": final, "note": note}
            except requests.exceptions.SSLError:
                last_err = "SSL"
                continue
            except requests.exceptions.ConnectTimeout:
                last_err = "连接超时"
            except requests.exceptions.ReadTimeout:
                last_err = "读取超时"
            except requests.exceptions.ConnectionError:
                last_err = "连接失败"
            except requests.exceptions.RequestException as e:
                last_err = type(e).__name__
            break
    return {"domain": domain, "alive": False, "code": "", "final": "", "note": last_err}


def load_all() -> list:
    """默认只测「域名状态=正常」的域名(其他状态无意义)。"""
    doms = []
    for d in json.load(open(RAW_DOMAINS, encoding="utf-8")):
        dom = (d.get("domain") or "").strip().lower()
        if dom and (d.get("status_dictText") or "") == "正常":
            doms.append(dom)
    return doms


def main():
    args = [a.strip().lower() for a in sys.argv[1:] if a.strip()]
    write_file = not args
    domains = args or load_all()
    print(f"存活检测:{len(domains)} 个域名(并发 {WORKERS},超时 {TIMEOUT[1]}s)…")

    by_dom = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for r in ex.map(check, domains):
            by_dom[r["domain"]] = r

    retry = [d for d, r in by_dom.items() if not r["alive"]]
    if retry:
        print(f"  ↻ 复测 {len(retry)} 个判死域名(并发 {RETRY_WORKERS},超时 {RETRY_TIMEOUT[1]}s)…")
        with ThreadPoolExecutor(max_workers=RETRY_WORKERS) as ex:
            for r in ex.map(lambda d: check(d, RETRY_TIMEOUT), retry):
                if r["alive"]:
                    by_dom[r["domain"]] = r

    results = list(by_dom.values())
    dead = [r for r in results if not r["alive"]]
    alive = [r for r in results if r["alive"]]
    print(f"✅ 可访问 {len(alive)}   ❌ 打不开 {len(dead)}")
    if dead:
        print("\n打不开的域名:")
        for r in sorted(dead, key=lambda x: x["domain"]):
            print(f"   ❌ {r['domain']:30} {r['note']}")

    if write_file:
        DOC_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["域名", "可访问", "状态码", "最终URL", "备注"])
            for r in sorted(results, key=lambda x: x["domain"]):
                w.writerow([r["domain"], "✅" if r["alive"] else "❌打不开",
                            r["code"], r["final"], r["note"]])
        print(f"\n输出:{OUT_CSV}")


if __name__ == "__main__":
    main()
