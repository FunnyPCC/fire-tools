---
name: find-site
description: Search the local sites.csv cache for matching sites by pattern (网站名 OR 根目录 contains). Use when invoking `/fire-bt-ops:find-site` or when the user says "查 NNN 在哪些面板 / NNN 部署在哪 / 找 X 关键字的站点". Offline lookup, no API calls.
argument-hint: <pattern>
allowed-tools: Bash
---

## Run

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py find-site <pattern>
```

Pattern is a free-text substring — matches both 网站名 column and 根目录 column (case-insensitive).

Examples:
- `f065_app` → all panels hosting the 065 frontend site
- `huozhongtech.com` → all huozhongtech-prefix sites
- `tonyhood` → 007 (whose primary domain is tonyhood.vip)

## Note

This uses `~/.fire/ops/sites.csv` only — if you suspect cache is stale (just after sync-panels apply), suggest the user run `/fire-bt-ops:list-sites` to refresh first.
