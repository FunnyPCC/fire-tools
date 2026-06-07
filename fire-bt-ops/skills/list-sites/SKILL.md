---
name: list-sites
description: Pull live site catalog from all reachable Baota panels and regenerate ~/.fire/ops/sites.csv. Use when invoking `/fire-bt-ops:list-sites`, when the user says "刷新 sites / 重新拉站点列表 / sites.csv 过期了", or right after `sync-panels --apply` when the panel set changed. Takes ~5 seconds via Clash proxy.
allowed-tools: Bash
---

## Run

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sites
```

Output reports:
- Total sites + panel count
- Per-panel summary (站点数 + 运行/停止 count)
- API 不可达 panels (those with stale tokens or API IP allowlist gaps)

## After

Report:
- How many sites total
- Note any panels in "不可达" list — user knows this is normal (Clash IP allowlist gaps), not a bug to fix immediately
- Both `sites.csv` and `sites_unreachable.csv` get refreshed at `~/.fire/ops/`

## Important

This intentionally does NOT pull bound domain lists per site (would be 466+ extra API calls + data stays stale fast). Use `/fire-bt-ops:domains <pattern>` for live domain queries instead.
