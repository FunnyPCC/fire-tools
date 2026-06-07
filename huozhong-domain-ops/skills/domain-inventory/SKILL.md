---
name: domain-inventory
description: Use when the user wants to pull/refresh the firepikata (火种/huozhong) domain × project inventory, check whether domains are risky (Google Safe Browsing / 欺骗性网页 / phishing), check whether domains are reachable (存活/可访问), or list a project's usable domains. Also trigger on mentions of firepikata domain backend, 域名维护, 可用域名, 域名风险, 域名存活, project domain availability, or huozhong domain ops.
---

# 域名库存盘点 · 风险/存活/可用(firepikata)

从火种后台(firepikata)拉全部「项目 × 域名」,检测 Google 风险与域名存活,维护成一张主表。是「火种域名运维」工作流的**盘点+体检**阶段(其余阶段:`batch-add-gsc` 加域名到 GSC;`baota-site-mgmt` 宝塔站点)。

## 数据与输出位置

全部产出在**当前项目**的 `./域名维护/`(相对运行目录;可用 `DOMAIN_DOC_DIR` 覆盖):
- `域名项目关联.{md,csv}` — 主表,8 列:项目编号/域名/项目状态/域名状态/Google风险/可访问/可用状态/备注
- `域名风险.{md,csv}`、`域名存活.csv`、`raw_domains.json`、`raw_apps.json`
- **务必在项目根运行**脚本,否则输出到别处。

## 凭证策略(全工具箱通用)

**1Password 是源,本地是缓存。** 经 `lib/op_secrets.py` 解析:环境变量 > 本地缓存 `./gsc/.secrets.json` > 1Password(取出后写回缓存)。失效/过期时脚本会自动 `refresh` 回 1P 重取。**插件不含密钥**;密钥只在用户本地 `./gsc/.secrets.json`(gitignore)。涉及:`fire_username`/`fire_password`(item「火苗-公共运维后管」)、`sb_api_key`(item「Google Maps API」字段「API Key」)。op 需 1Password 桌面端解锁;有本地缓存时通常不触发 op。

## 工作规则(重要)

- **懒触发刷新,无定时**:用户提域名相关任务、且 `域名维护/raw_domains.json` 修改时间 ≥1 天前 → 先 `rebuild_domains.sh` 再答(`stat -f %m` 比对);没相关任务就不更新。
- **可访问性按需**:不随重拉跑;仅当用户要检测、或要列某项目「可用域名」时,对相关域名跑 `liveness_check.py`。
- **只查域名状态=正常**:risk/liveness 全量模式只测 `status_dictText==正常` 的;已关闭/维护中不检查。
- **可用口径** = 域名状态正常 + Google无风险 + 能打开(列「可用域名」时按此过滤,带 `https://` 前缀)。
- **关联**:`域名.appId == 项目.id` → projectCode;取不到标(未绑定项目)/(已删除项目·<id>)。

## 命令(在项目根运行)

```bash
PLUGIN="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/plugins/marketplaces/funnypcc/huozhong-domain-ops}"
# 一键重建(自动登录→抓取→风险→合并;不跑存活)
bash "$PLUGIN/skills/domain-inventory/scripts/rebuild_domains.sh"
# 只取一个新 token
uv run "$PLUGIN/skills/domain-inventory/scripts/fire_login.py"
# 风险 / 存活:不带参=全量(仅域名状态正常);带域名=只测这些
uv run "$PLUGIN/skills/domain-inventory/scripts/risk_check.py" a.com b.com
uv run "$PLUGIN/skills/domain-inventory/scripts/liveness_check.py" a.com b.com
```
> 本项目里也有 1 行壳 `gsc/rebuild_domains.sh` → 调上面的脚本,保持 `bash gsc/rebuild_domains.sh` 可用。

## 列某项目「可用域名」的标准流程

1. 必要时先按懒触发规则重拉。
2. 从 `域名项目关联.csv` 取该项目「域名状态=正常 且 Google风险=正常」的候选。
3. 对候选跑 `liveness_check.py`(现查存活)。
4. 输出「域名正常 + 无风险 + 能打开」的,带 `https://`。

## 关键事实

- **GSC 官方 API 查不到「安全问题」**(已实测,URL Inspection 无安全字段)→ 改用 **Safe Browsing API v4**(同源威胁数据)。前提:GCP 项目 `324812159513` 已启用 Safe Browsing API 且 key 放行。
- **firepikata token 约 3 天过期**,且登录带图形验证码 → `fire_login.py` 用 `ddddocr` 自动过码换 token。
- **后台「正常」≠ Google 安全 ≠ 能打开**:三者独立,主表分三列,别用一个推另一个。
- 存活检测走本机网络(开了 Clash/Surge 与浏览器一致);判死域名会降并发复测一遍除假死。
