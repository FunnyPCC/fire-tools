---
name: baota-add-domain
description: ⚠️ 仅把域名加到 Fire 项目 fNNN_app 宝塔站点的 nginx 别名（前提：该域名的 Cloudflare zone 已存在 / 已在别处托管）。适用：漂移补齐、把已有域名重新挂到某台面板、域名 DNS 不归本套后台管。**全新域名不要用这个**——会卡在 GSC no-zone；全新域名走 `add-domains`（它建 CF zone + 绑 firepikata 后端 + 联动宝塔 + GSC）。Triggers: `/fire-bt-ops:baota-add-domain`、"把已有域名 X 补到 NNN 宝塔"、"NNN 某台面板缺 X 补一下宝塔别名"。Runs dry-run by default, requires explicit user confirmation before --apply, outputs the mandatory code-block summary on success.
argument-hint: <branch> <domain1> [domain2 ...]
allowed-tools: Bash, AskUserQuestion
---

> **只加宝塔 nginx 别名，不建 CF zone、不绑后端。** 如果这是一个全新买的/后台还没登记的域名，停，改用 `add-domains`（完整上线流程）。判断法：firepikata 后台 / `app_domains.py` 查不到该域名，或 CF 无 active zone → 属于全新域名，不该走本 skill。

Follow the full bt-panel-ops workflow rules. Don't skip the confirmation step.

## Parse args

`$ARGUMENTS` is `<branch> <domain1> [domain2...]`:
- `<branch>`: 3-digit number (e.g. `065`) — bt.py will match `fNNN_app` sites
- `<domain1>...`: one or more domains (no `https://` prefix; bt.py adds host alone)

If args are missing, ask:
- "要加哪个分支？例如 065" + "要加哪些域名？空格分隔"

## Step 1 — Dry-run

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py add-domain <branch> <domain1> <domain2> ...
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
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py add-domain <branch> <domains> [--exclude-host IPs] --apply
```

If user said sync drift:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sync-domains f<NNN>_app [--exclude-host IPs] --apply
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

## Step 5 — 接着加到 Google GSC(仅当 zone 已 active)

补完宝塔别名后,**若该域名 CF zone 已 active**,可紧接着加 Google Search Console:

1. 用 Step 4 的同一批域名(`https://` 列表可直接用 —— GSC 的 `batch_add_gsc.py` 有 `normalize_domain()` 会自动剥 `https://`/路径转裸域名)。
2. 触发 `batch-add-gsc` 流程:把这批域名写入其 `domains.txt`,跑批量加 GSC(Cloudflare 写 TXT 验证 → 注册 sc-domain 属性)。
3. **GSC/Cloudflare DNS 这步用户已长期授权,无需再确认**。
4. ⚠️ 若 GSC 报 `no zone` / `not found in Cloudflare` → 该域名根本没在 CF 建 zone(或还 pending),说明它是**全新域名、走错 skill 了**,应回到 `add-domains` 建 zone。`batch_add_gsc.py` 只认 `status=active` 的 zone。

> 跨阶段链(全新域名完整路径):`add-domains`(建 CF zone+绑后端+联动宝塔)→ 等 zone 转 active → `batch-add-gsc`(加 GSC)。
> **本 skill 只覆盖"宝塔补别名"这一环**,不建 zone;纯补漂移/重挂面板时用。三者同属 funnypcc 工具箱。
