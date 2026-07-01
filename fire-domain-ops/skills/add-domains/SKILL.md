---
name: add-domains
description: 【全新域名上线的默认入口】给某个 Fire 项目批量加新域名——firepikata 后端建 Cloudflare zone + 绑项目 + 联动宝塔（+ 下游 GSC）。Use when the user says "给 007 加 a.com b.com"、"把 a.com 加到 007"、"给项目加域名"、"加备用域名 a.com"、batch add domains to a project. 指定项目→绑项目且 IP=项目 appIp；未指定项目→备用域名 IP=128.241.233.59。**"把 X 加到 NNN" 默认走这个**（会建 CF zone，全新域名才能通到 GSC）；只想给宝塔站点补 nginx 别名、且域名 CF zone 已存在（漂移补齐/重挂面板）才用 `baota-add-domain`。
argument-hint: [项目号] <域名...>（或 "加备用 <域名...>"）
allowed-tools: Bash, AskUserQuestion
---

流程1：给项目/备用加域名。**不需确认、直接执行**，执行后给完整反馈。

## 1. 解析参数
- 「项目号 + 域名」→ 加到该项目；只给域名 / 说"备用" → 备用域名。
- 项目号给数字即可（如 `007`），脚本会模糊匹配 projectCode（007→f007）。

## 2. 执行后端（后台 + 日志）
后台启动脚本，并**立刻把 `tail -F` 命令给用户**看实时进度（脚本结尾也会打印）：

```bash
# 加到项目：
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/app_domains.py add --project <数字> <域名...> --apply
# 备用域名（不指定项目）：
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/app_domains.py add <域名...> --apply
```

- 若脚本报「匹配到多个项目」→ 用 AskUserQuestion 让用户选完整 projectCode，再以它当 `--project` 重跑。
- 脚本会自动：解析 CF 账号(默认 hualee887@gmail.com)、目标 IP（项目 appIp / 备用 128.241.233.59）、清洗去重域名，POST batchAddDomains，再轮询回查落库。

## 3. 联动宝塔（仅当指定了项目）
后端落库后，把同一批域名加到对应宝塔站点 `<projectCode>_app`（先 dry-run 看匹配+漂移，再直接 apply）：

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py add-domain <projectCode>_app <域名...>
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py add-domain <projectCode>_app <域名...> --apply
```

漂移情况写进反馈、不拦截。**备用域名（无项目）不联动宝塔。** 需 Clash 代理；不通由 bt.py 自行报告。

## 4. 反馈
汇总：后端落库结果（✅/❌ 每个域名）+ 宝塔 code-block（新增域名）+ 漂移提示。

## 5. 下游 GSC（等 zone 转 active 再跑）
后端建的 Cloudflare zone 刚落库时是 **`status=pending`**（CF 已分配 NS，但注册商 nameserver 还没指过来）。`batch_add_gsc.py` **只认 `status=active` 的 zone**，pending 会报 `no zone / not found in Cloudflare` 跳过——这不是 bug，是 DNS 委派没到位（Google 也解析不到 TXT）。正确顺序：

1. 落库后查 zone 状态（同一套 CF 缓存凭据 `~/.fire/.gsc_cf_cache.json`）：
   ```bash
   # GET https://api.cloudflare.com/client/v4/zones?name=<域名>  → result[0].status
   ```
2. `pending` → 等注册商把 NS 切到 CF 给的那对（如 `chan.ns.cloudflare.com`/`johnny.ns.cloudflare.com`），几分钟~几小时后自动转 `active`。切 NS 不在本工具箱范围（无注册商接口）。
3. `active` → 跑 `batch-add-gsc`：
   ```bash
   uv run ${CLAUDE_PLUGIN_ROOT}/skills/batch-add-gsc/scripts/batch_add_gsc.py --domains <域名...>
   ```
   （CF/Google 已长期授权，无需再确认。Google token 若过期报 `invalid_grant`，用 `--reauth` 走一次浏览器重授权，会自动同步回 1Password + 本地缓存。）

> 完整链：`add-domains`（后端建 zone+绑项目+联动宝塔）→ 等 zone `active` → `batch-add-gsc`（写 TXT 验证+注册 sc-domain）。
> 反过来只补宝塔别名（zone 已存在）用 `baota-add-domain`。
