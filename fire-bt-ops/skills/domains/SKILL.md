---
name: domains
description: Live-query the full domain list (primary + all bound) for a Fire 项目 site across all matching panels. Use when invoking `/fire-bt-ops:domains` or when the user says "查 NNN 有哪些域名 / NNN 的域名列表 / NNN 现在绑了什么". This calls Baota OpenAPI live (does NOT use sites.csv cache).
argument-hint: <branch-or-pattern>
allowed-tools: Bash
---

## Parse args

- `065` → pattern `f065_app` (frontend default)
- `065 --backend` or "查 065 后台" → pattern `f065`
- Already a pattern → use as-is

## Query

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py domains <pattern>
```

The output groups by host, lists primary (`*`) + all bound (`+`) domains per panel.

## Drift hint

If domain counts differ across panels (look for the `共 N 个域名` lines), point out which panel has more/fewer. Suggest `/fire-bt-ops:sync-domains <pattern>` if user wants to align.

## Note

Don't try to "save" this output anywhere. It's a live read — sites.csv intentionally does NOT cache domain lists (would be 466+ API calls per refresh and goes stale fast).
