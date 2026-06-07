# 设计文档：Fire 域名运维插件合并 + 域名分配自动化（fire-domain-ops）

- 日期：2026-06-08
- 状态：已批准（2026-06-08）
- 涉及仓库：
  - `funnypcc`（本仓库）— 把 `huozhong-domain-ops` + `fire-bt-ops` **合并为新插件 `fire-domain-ops`**，并新增 3 个 skill + 共享脚本 + 日志/进度能力
  - `fire-common`（后端，分支 `new_master`）— 新增 1 个按 ID 精准重指 IP 的接口

## 1. 背景与目标

公共运维后台（线上 `https://firepikatacommon.huozhongtech.org`，源码 `fire-common`，JeecgBoot）是域名数据源。要把三件目前靠人在「应用域名管理」页面手工点的事做成对话式自动化，并与宝塔面板联动：

1. **流程1** 批量给项目加新域名（弹窗「批量添加域名到Cloudflare」）。
2. **流程2** 统计「待分配空域名」数量（按后缀分组）。
3. **流程3** 给项目分配若干空域名（勾选 → 批量编辑 → 设项目+IP）。

同时解决两个工程诉求：
- **合并插件**：`huozhong-domain-ops` 与 `fire-bt-ops` 合并为 `fire-domain-ops`，让流程1/3 的"加完后台→加到宝塔"变成插件内部调用。
- **长任务可观测**：原本要手动在终端跑的脚本（如 GSC）改为由 Claude 执行，但保留实时进度——脚本写日志（可 `tail -f`），Claude 同时在对话里汇报。

### 关键事实（已核实源码）

- 鉴权：`POST /api/sys/login` 拿 JWT；调用必须用请求头 `X-Access-Token`（用 `Authorization` 会 401）。复用 `fire_login.get_token()`。
- `app_domain_manager`：`id`(=CF zoneId)、`domain`、`appId`(项目)、`ip`、`status`(1 正常/-1 废弃/-2 维护)、`dnsId`、`cloudFlareManagerId`、`remark`。
- `app_manager`：`id`、`projectCode`(如 `f007`)、`appIp`(应用IP)。**`appIp` = 宝塔面板 IP**（用户确认）。
- `app_cloud_flare_manager`：`account`(如 `hualee887@gmail.com`)、`apiKey`、`id`。
- `POST /api/app/appDomainManager/batchAddDomains {cloudFlareManagerId, domains[], targetIp, appId?}`：逐域名 建Zone→清DNS→加A记录(→targetIp)→SSL Flexible→落库（**异步**，立即返回）。对**已存在**域名只更新 ip+DNS，不写 appId。
- `POST /api/app/appDomainManager/editBatch {ids[], ...字段}`：循环 `updateById`，**只改库、不动 CF**。
- `POST /api/app/appManager/updateAppIp/{id}`（同步）：遍历该项目**所有 status=1** 域名逐个 `updateIp`（原地改 A 记录，不 deleteAllDns；CF 失败会把该域名标 `status=-1`）。→ 因会波及全项目老域名，**不采用**，改用下面的新接口。
- `fire-bt-ops`：`python bt.py add-domain <分支3位> <域名...> [--apply]`，dry-run 探漂移，需 Clash 代理，apply 后输出 code-block。

## 2. 范围

### In scope
- 插件合并：`huozhong-domain-ops` + `fire-bt-ops` → `fire-domain-ops`，更新 `marketplace.json`，合并 hooks。
- 后端新增按 ID 精准重指接口 `batchUpdateIp{ids[], ip}`。
- 新增 3 个 skill（`add-domains` / `spare-domains` / `allocate-domains`）+ 共享脚本 `app_domains.py`。
- 流程1/3 与 `fire-bt-ops add-domain` 的插件内联动。
- 长任务日志+进度能力，覆盖新流程**并改造现有 `batch-add-gsc`**。

### Out of scope
- 不改前端 Vue 页面；不自动部署后端（部署由用户执行）。
- 不改 `fire-bt-ops` 既有业务逻辑（仅迁移 + 被新流程调用）；**唯一例外**：bt.py `add-domain`/`sync-domains` 放开写死的 `f` 前缀以支持完整站点名(如 `ptn007_app`)，纯数字旧用法保持兼容。
- 不处理多级 TLD（co.uk 之类）特殊拆分。

## 3. 决策记录（用户已拍板）

| 项 | 决定 |
|----|------|
| 插件 | 合并为 `fire-domain-ops` |
| CF 账号 | 默认 `hualee887@gmail.com`（`--cf` 覆盖） |
| 项目模糊匹配 | 用户给数字部分(通常 3 位如 007)，用它对 `projectCode` 做**子串模糊**搜索，前缀不限(可能 f007 / ptn007)；命中多个或拿不准→**停下问用户**；0 命中→报错列近似。不做"加 f/补零"硬转换 |
| 应用IP | `app_manager.appIp`（= 宝塔面板 IP） |
| 空域名口径 | 项目空 + 备注空 + **status=1** |
| 流程3 .com 不够 N | **停下来问用户**，不自动用其他后缀 |
| 流程3 CF 重指 | **方案 B**：后端加 `batchUpdateIp`，只动新分配这批，零误伤 |
| 流程1/3 执行 | **不确认、直接执行、执行后给完整反馈** |
| 宝塔站点命名 | 站点名 = 解析出的**完整 projectCode + `_app`**（f007→f007_app，ptn007→ptn007_app），统一加到 `_app` 前端站点 |
| 流程1/3 联动宝塔 | 自动调 `bt.py add-domain <projectCode>_app <域名...> --apply`，漂移写进反馈不拦截 |
| bt.py 改造 | 放开写死的 `f` 前缀：参数为纯数字时仍按 `f<NNN>_app` 兼容旧用法；参数已是字母开头的完整站点名(如 `ptn007_app`)则原样作 pattern |
| 长任务进度 | 后台执行 + 日志文件(可 `tail -f`) + Claude 对话汇报，**两者都要** |
| GSC | `batch-add-gsc` 一起改造为日志+进度模式 |

## 4. 架构

```
对话 / slash 触发
   │
   ▼
fire-domain-ops（合并后单插件）
   ├─ 新 skill: add-domains / spare-domains / allocate-domains
   │     └─ scripts/app_domains.py ──(X-Access-Token)──► firepikata 后台 REST
   │            复用 lib/op_secrets.py + skills/domain-inventory/scripts/fire_login.py
   │            └─(流程1/3 完成后) ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py add-domain --apply ──► 宝塔
   ├─ 既有 skill: domain-inventory / batch-add-gsc（改造日志+进度）
   └─ 既有 skill: add-domain / bt-panel-ops / domains / find-site / list-sites / sync-domains / sync-panels / setup
          + SessionStart 钩子（面板自动 sync）
```

## 5. 插件合并迁移（实现第一步）

目标目录 `funnypcc/fire-domain-ops/`：
```
fire-domain-ops/
  .claude-plugin/plugin.json      # name: fire-domain-ops，合并 hooks
  lib/op_secrets.py               # 来自 huozhong
  lib/runlog.py                   # 新增：统一日志/进度工具（见 §8）
  hooks/                          # 来自 fire-bt-ops（SessionStart → session_sync）
  scripts/
    bt.py  setup.py  session_sync.py   # 来自 fire-bt-ops
    app_domains.py                      # 新增（共享）
  skills/
    domain-inventory/  batch-add-gsc/                 # 来自 huozhong（含各自 scripts/、fire_login.py）
    add-domains/  spare-domains/  allocate-domains/    # 新增
    add-domain/ bt-panel-ops/ domains/ find-site/ list-sites/ sync-domains/ sync-panels/ setup/  # 来自 fire-bt-ops
```

迁移要点：
- 保持 huozhong skill 内部相对层级不变，确保 `fire_login.py` 里 `parents[3]/lib` 仍指向插件根 `lib/`。
- `app_domains.py` 在 `scripts/`：用 `parents[1]/lib`(op_secrets)、`parents[1]/skills/domain-inventory/scripts`(fire_login) 注入 `sys.path`。
- `fire-bt-ops` 各 skill 用 `${CLAUDE_PLUGIN_ROOT}/scripts/bt.py`，迁移后路径仍有效。
- `marketplace.json`：两条插件项替换为一条 `fire-domain-ops`。
- `plugin.json`：合并 fire-bt-ops 的 SessionStart hook；版本起 `1.0.0`（待定）。
- 数据目录不受影响：`~/.fire/ops/`（panels.yml）、项目内 `./域名维护/`、`./gsc/.secrets.json` 都在插件外，迁移后照常。
- **用户重装一次**：卸载旧两个、从 marketplace 安装 `fire-domain-ops`（或更新 `installed_plugins.json`）。git 历史可回滚。
- 验收：迁移后旧能力（domain-inventory / batch-add-gsc / bt 各命令 / SessionStart）仍可用，再叠加新流程。

## 6. 共享脚本 `scripts/app_domains.py`

`uv run`，PEP723 依赖 `requests`；`BASE=https://firepikatacommon.huozhongtech.org`，请求带 `X-Access-Token`。

公共函数：
- `get_token()`（复用 fire_login）。
- `api_get/api_post`（带 token、超时、非 success 抛带 message 异常）。
- `resolve_cf(account="hualee887@gmail.com") -> cloudFlareManagerId`。
- `resolve_project(num) -> {appId, projectCode, appIp, name}`：用 num 作**子串模糊**匹配 `projectCode`（不限前缀，可能 f007 / ptn007）；命中 1→用；命中多个→列出让用户选；0→报错列近似；`appIp` 空→报错要 `--ip`。
- `fetch_all_domains()`（`appDomainManager/list?pageSize=2000`，必要时翻页）。
- `spare_pool(rows)` = `appId` 空 且 `remark` 空 且 `status==1`。
- `clean_domains(args)`（去 `https://`/路径/空白、转小写、去重、丢空）。
- `tld(domain)`（取最后一段）。

子命令：`spare`、`add --project <p|空> [--ip] [--cf] <domains...>`、`allocate --project <p> --count N [--cf]`。所有写操作子命令支持 `--log <file>` 与进度输出（见 §8）。

## 7. 后端改动（fire-common，分支 new_master）

新增按 ID 精准重指接口，照搬 `updateAppIp` 但只循环传入 ids：
- 接口：`POST /api/app/appDomainManager/batchUpdateIp`，入参 `{ "ids":[...], "ip":"..." }`，权限 `@RequiresPermissions("app:app_domain_manager:edit")`。
- 行为（同步）：对每个 id 调已存在的 `updateIp(id, ip)`（改库 + `cloudflare.updateDnsRecord` 原地改 A 记录）；**单条失败不中断**其余，收集成功/失败，返回汇总。
- 改动：`AppDomainManagerController` 加 `@PostMapping("/batchUpdateIp")`；`IAppDomainManagerService` + `Impl` 加 `batchUpdateIp(List<String> ids, String ip)`。
- 提交规范：`feat(app): 域名管理新增按ID批量重指IP接口`。
- **部署由用户做**；插件调用前探测，404 则提示"先部署后端 batchUpdateIp 接口"并中止流程3 的重指步骤。

## 8. 长任务日志 + 实时进度（lib/runlog.py）

统一工具：
- `RunLog(action, log_path=None)`：默认日志 `./域名维护/logs/<action>-<时间>.log`（GSC 用 `./gsc/logs/...`）。每行带时间戳；同时写文件与 stdout；行缓冲/`flush`（脚本以 `python -u` 跑）。
- 约定输出：开头 `total=N`；每步 `[i/N] <对象> <结果> (<耗时>)`；结尾 `汇总 成功X 失败Y`，失败列清单。失败照记不静默。

- 稳定入口：每次运行除了写带时间戳的 `<action>-<时间>.log`，再维护一个固定软链 `logs/latest.log` 指向当前运行日志，用户始终可 `tail -f logs/latest.log` 看最新一次。

执行与观测：
- Claude 用**后台任务**启动脚本（不阻塞、避开 10 分钟前台超时）。
- **行为规则（强制）**：每次启动后台长任务，Claude 必须立刻在对话里给出查看实时进度的办法——① 可直接复制的 `tail -F` 命令（含本次日志绝对路径，以及稳定的 `logs/latest.log`）；② 说明在 VSCode 集成终端（Cmd+`）里跑该命令即可看实时（VSCode 扩展无后台面板，仅状态栏提示；CLI 版可用 `/bashes`、`Ctrl+T`）；③ 说明 Claude 也会定期汇报进度与最终汇总。
- 用户可 `tail -f <日志>` 看实时；Claude 定期拉日志在对话里汇报关键进度 + 结束汇总。
- 后端异步动作（`batchAddDomains`）无脚本级进度 → Claude 轮询 `appDomainManager/list` 回查 "X/N 已落库"。

改造 `batch-add-gsc`：接 `RunLog`，每域名打印 `[i/N] domain 取TXT/写CF/校验/注册 ✅|❌`，默认后台+日志运行；保留手动运行兼容。

## 9. 三个 skill 流程

### 流程1 — `add-domains`
触发：`/fire-domain-ops:add-domains`、"给 007 加 a.com b.com"、"加备用域名 a.com b.com"
1. 解析项目(可选)+域名：指定项目→`appId=项目id`、`targetIp=appIp`；未指定→备用，`appId`空、`targetIp=128.241.233.59`(`--ip`覆盖)。
2. `resolve_cf()` 拿 `cloudFlareManagerId`。
3. 打印解析结果 → **直接** `POST batchAddDomains`。
4. 轮询 `appDomainManager/list` 回查（最多约 60s）。
5. **联动**（仅指定项目）：`bt.py add-domain <projectCode>_app <域名...> --apply`（内部 dry-run 探漂移，apply，漂移写反馈）。
6. 完整反馈：后端结果 + 宝塔 code-block + 漂移提示。

### 流程2 — `spare-domains`
触发：`/fire-domain-ops:spare-domains`、"还有多少空域名待分配？"
- `spare_pool()` → 按 TLD 分组计数 → 「com XX 个、info XX 个 …（按量排序+总计）」。纯读。

### 流程3 — `allocate-domains`
触发：`/fire-domain-ops:allocate-domains`、"给 007 分配 3 个新域名"
1. `resolve_project` → `appId`、`appIp`。
2. `spare_pool()` 选 N 个，**优先 .com**；**.com 不足 N → 停下来问用户**。
3. 打印清单 → **直接**执行：`POST editBatch{ids,appId}` → 探测并 `POST batchUpdateIp{ids, ip:appIp}`。
4. 轮询回查（appId/ip/status）。
5. **联动**：`bt.py add-domain <projectCode>_app <这批域名> --apply`。
6. 完整反馈。

## 10. 凭据
- firepikata 登录沿用「火苗-公共运维后管」(env `FIRE_USER`/`FIRE_PASS` > 缓存 > 1Password)，与 git 仓库凭据无关。
- CF apiKey 在后台侧，由后台调 Cloudflare；插件不直接接触。
- 宝塔凭据由 `fire-bt-ops` 部分自管（`~/.fire/ops/panels.yml`）。

## 11. 错误处理与边界
- token 失败：fire_login 自处理（缓存过期回 1P 重取）。
- 项目 0/多命中、`appIp` 空、CF 账号未命中、域名清洗后为空：硬错误，停止报告（必要的输入澄清，非"确认"）。
- `batchAddDomains` 异步：以回查为准，不轻信"任务已启动"。
- `batchUpdateIp` 未部署(404)：提示先部署，中止流程3 重指步骤。
- 宝塔联动：Clash 未开/面板不可达由 `fire-bt-ops` 报告；漂移写反馈不拦截。
- 备用域名（流程1 未指定项目）：不触发宝塔联动。
- 长任务：失败照记日志不静默；脚本结尾给非零退出码标识失败。

## 12. 待确认 / 假设

- 合并插件版本号 `1.0.0`（暂定）。

### 已确认（不再是假设）

- 项目解析：用户给数字 → 对 `projectCode` 子串模糊匹配（前缀不限 f/ptn…）→ 多个或拿不准则问用户。
- 宝塔站点 = 完整 projectCode + `_app`；统一 `_app` 前端站点。
- bt.py 放开 f 前缀写死，支持完整站点名 pattern。
