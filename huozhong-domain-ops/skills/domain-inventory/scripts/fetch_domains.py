#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.28"]
# ///
"""
拉取 firepikata 后台「域名」「项目」全量 → ./域名维护/raw_domains.json、raw_apps.json
(输出目录相对当前工作目录,可用 DOMAIN_DOC_DIR 覆盖。请在项目根运行。)

鉴权:必须用请求头 X-Access-Token(用 authorization 会 401)。token 是临时 JWT,
经 FIRE_TOKEN 环境变量传入(rebuild_domains.sh 会先用 fire_login.py 自动登录获取)。
"""
import json
import os
import sys
from pathlib import Path

import requests

BASE = "https://firepikatacommon.huozhongtech.org"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DOC_DIR = Path(os.environ.get("DOMAIN_DOC_DIR", "域名维护"))

ENDPOINTS = {
    "raw_domains.json": ("/api/app/appDomainManager/list", 2000),
    "raw_apps.json": ("/api/app/appManager/list", 500),
}


def fetch(path, page_size, token):
    params = {"column": "createTime", "order": "desc",
              "pageNo": 1, "pageSize": page_size, "_t": 1}
    r = requests.get(f"{BASE}{path}", params=params,
                     headers={"X-Access-Token": token, "User-Agent": UA}, timeout=60)
    data = r.json()
    if not data.get("success"):
        msg = data.get("message", "")
        if data.get("code") == 401:
            sys.exit(f"❌ Token 失效({msg})。请重新获取 FIRE_TOKEN(fire_login.py)。")
        sys.exit(f"❌ 接口错误:{msg}")
    res = data["result"]
    return res["records"], res.get("total")


def main():
    token = (os.environ.get("FIRE_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()
    if not token:
        sys.exit("❌ 缺少 token。设 FIRE_TOKEN 或先跑 fire_login.py。")

    DOC_DIR.mkdir(parents=True, exist_ok=True)
    for fname, (path, size) in ENDPOINTS.items():
        records, total = fetch(path, size, token)
        with open(DOC_DIR / fname, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=1)
        warn = "" if len(records) >= (total or 0) else f"  ⚠️ 未取全(total={total})"
        print(f"  {fname}: {len(records)} 条{warn}")
    print(f"✅ 抓取完成 → {DOC_DIR}/raw_domains.json, raw_apps.json")


if __name__ == "__main__":
    main()
