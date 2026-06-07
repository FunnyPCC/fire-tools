---
name: sync-panels
description: Refresh ~/.fire/ops/panels.yml from bt-client SQLite, surfacing any panel-side changes (renames, new/removed panels, token rotation, group moves). Use when invoking `/fire-bt-ops:sync-panels`, when the user says "刚重新导入了 / 我同步了配置 / 面板有变化了", or as the first step before any other panel work in a fresh session. The SessionStart hook also runs this automatically.
argument-hint: [--apply]
allowed-tools: Bash, AskUserQuestion
---

## Default flow (dry-run)

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sync
```

Output:
- `✓ panels.yml 与 bt-client SQLite 一致` → no action, tell user "面板侧无变化"
- `⚠️ 面板侧有 N 项变化` → show the diff to user, then ask whether to apply

## If diff exists

Use `AskUserQuestion`:
- "刷新 panels.yml + 重跑 sites.csv (推荐)"
- "只刷新 panels.yml，不动 sites.csv"
- "都不动，我先看看"

On apply:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sync --apply
```

If user chose to also refresh sites (recommended after panels.yml changes):
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sites
```

## Important

- Token rotations / IP changes in the diff are normal when the other maintainer rebuilds a panel — apply directly
- New panels in unused groups (新材料 etc.) are filtered out by setup.py — won't appear in diff
- `host_aliases:` section in panels.yml is **not** overwritten by `sync --apply` (user-maintained)
