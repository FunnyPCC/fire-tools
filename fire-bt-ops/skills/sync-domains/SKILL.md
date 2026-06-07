---
name: sync-domains
description: Backfill domain drift between mirror panels for a Fire 项目 fNNN_app site (takes union of domain sets across machines and adds whatever's missing on each). Use when invoking `/fire-bt-ops:sync-domains` or when the user says "把 NNN 的域名对齐 / 同步 / 补齐漂移".
argument-hint: <branch-or-pattern> [--exclude-host IP1,IP2]
allowed-tools: Bash, AskUserQuestion
---

## Parse args

- Plain number like `065` → pattern `f065_app`
- Already a pattern like `f065_app` → use as-is
- `tonyhood.vip` etc. → also valid (matches via 网站名)

If unsure, ask the user which branch.

## Step 1 — Dry-run

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sync-domains <pattern>
```

Output shows:
- All matched canonical machines + their current domain count
- The union of all domains
- Per-machine list of "what's missing" that would get added

If a matched panel name contains `暂时没用` / `废` / `停` / `镜像`, ask whether to exclude before applying.

## Step 2 — Confirm + apply

`AskUserQuestion`: "确认补齐 X 条漂移？" (yes/no/exclude-some-host)

On yes:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sync-domains <pattern> [--exclude-host IPs] --apply
```

## Step 3 — Report

Show the result. No code-block output here — sync-domains backfills are not "new domains", just consistency fixes. The audit log records them as `SYNC-OK`.
