---
name: add-domain
description: Add domains to a Fire 项目 fNNN_app site across all mirror panels. Triggers when the user invokes `/fire-bt-ops:add-domain` or says "把 X 加到 NNN" / "给 NNN 加 X 域名". Runs dry-run by default, requires explicit user confirmation before --apply, outputs the mandatory code-block summary on success.
argument-hint: <branch> <domain1> [domain2 ...]
allowed-tools: Bash, AskUserQuestion
---

Follow the full bt-panel-ops workflow rules. Don't skip the confirmation step.

## Parse args

`$ARGUMENTS` is `<branch> <domain1> [domain2...]`:
- `<branch>`: 3-digit number (e.g. `065`) — bt.py will match `fNNN_app` sites
- `<domain1>...`: one or more domains (no `https://` prefix; bt.py adds host alone)

If args are missing, ask:
- "要加哪个分支？例如 065" + "要加哪些域名？空格分隔"

## Step 1 — Dry-run

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py add-domain <branch> <domain1> <domain2> ...
```

Show the user the output verbatim (matches, drift, plan). Key things to flag from the output:
- If a matched panel name contains `暂时没用` / `镜像` / `废` / `停`: ask the user whether to exclude it via `--exclude-host <IP>`
- If drift detected: ask the user whether to also `sync-domains` after the add

## Step 2 — Wait for confirmation

Use `AskUserQuestion` with options like:
- "小步验证 (推荐首次): 先 1 域 + 1 机, 过了再批量" — only relevant if API format unverified
- "全部 N 个一起加" — normal default after first run validated
- "取消"

Plus a second question on drift if any:
- "顺手补漂移" / "不动漂移"

## Step 3 — Apply

Use the choices to assemble:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py add-domain <branch> <domains> [--exclude-host IPs] --apply
```

If user said sync drift:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sync-domains f<NNN>_app [--exclude-host IPs] --apply
```

## Step 4 — Code-block summary (MANDATORY)

Send a separate message with **only** this code block, no other text in this message:

````
```
f<NNN> 新增域名
https://<domain1>
https://<domain2>
```
````

- Only the domains the user originally requested
- Original input order, not sorted
- All prefixed `https://`
- Triple-backtick code block (IDE copy button)
- Exclude drift-sync backfill domains (those are sync, not new)

## Step 5 — 接着加到 Google GSC(链式,默认要做)

加完宝塔域名,**一般紧接着要把同一批新域名加到 Google Search Console**(这是固定下游)。

1. 用 Step 4 的同一批新域名(`https://` 列表可直接用 —— GSC 的 `batch_add_gsc.py` 有 `normalize_domain()` 会自动剥 `https://`/路径转裸域名)。
2. 触发 `batch-add-gsc` 流程:把这批域名写入其 `domains.txt`,跑批量加 GSC(Cloudflare 写 TXT 验证 → 注册 sc-domain 属性)。
3. **GSC/Cloudflare DNS 这步用户已长期授权,无需再确认,宝塔 apply 成功后直接接着跑**(宝塔 add-domain 自己的 dry-run→确认→apply 门照常)。

> 跨阶段链:`domain-inventory`(挑可用域名)→ `add-domain`(加宝塔)→ `batch-add-gsc`(加 GSC)。三者同属 funnypcc 工具箱。
