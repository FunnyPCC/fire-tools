---
name: setup
description: First-time install — decrypt bt-client local SQLite and generate ~/.fire/ops/panels.yml + sites.csv. Use when invoking `/fire-bt-ops:setup`, when the user says "第一次装 / 初始化 / setup", or when ~/.fire/ops/panels.yml is missing/empty. Requires bt-client (堡塔多机管理) installed and pycryptodome (`pip install pycryptodome`).
allowed-tools: Bash
---

## Preflight checks

Verify before running:
1. **bt-client installed**: `%APPDATA%\bt-client\data\data.db` must exist
2. **pycryptodome available**: `python -c "from Crypto.Cipher import AES"` works
3. **Clash Verge running**: `curl --max-time 3 -x http://127.0.0.1:7897 https://api.ipify.org` returns an IP

If any fails, tell user what to install:
- bt-client: download from https://www.bt.cn/
- pycryptodome: `pip install pycryptodome`
- Clash: required for panel connectivity (the user already has this)

## Step 1 — Generate panels.yml

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/setup.py
```

Output: `✓ 写入 ~/.fire/ops/panels.yml (43 面板)` + host_aliases count

This script:
- Reads `%APPDATA%\bt-client\data\config.json` for `password_hash`
- Derives AES-128-ECB key (every-other-char of password_hash)
- Decrypts each panel's `api_token` field
- Filters to 4 Fire groups (公共/ptn项目/网关/东京网关)
- Preserves any existing `host_aliases:` section (manual config)

## Step 2 — Pull sites catalog

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sites
```

Generates `~/.fire/ops/sites.csv` and `sites_unreachable.csv`.

## Step 3 — Verify

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py ping
```

Should show OK for 60%+ panels. Failures are expected for panels whose API IP allowlist doesn't include the Clash exit IP — not a setup bug.

## Done

Tell the user:
- `~/.fire/ops/panels.yml` has N panels (chmod 600)
- `sites.csv` has N sites
- Next: try `/fire-bt-ops:find-site f068_app` to verify the workflow
