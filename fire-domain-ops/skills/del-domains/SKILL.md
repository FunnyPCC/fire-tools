---
name: del-domains
description: 把一批域名从某个 Fire 项目整体移除（add-domains 的逆操作）——删 Cloudflare zone + firepikata 后端落库 + 宝塔站点域名。Use when the user says "把 X 从 073 删掉"、"删除 073 的这些域名"、"移除项目域名"、"退订/退款的域名清掉"、remove/delete domains from a project。破坏性操作：先 dry-run 给用户看，确认后才 --apply。
argument-hint: <项目号> <域名...>
allowed-tools: Bash, AskUserQuestion
---

把域名从项目**整体移除**（add-domains 的逆操作）。三层：① CF zone ② firepikata 后端落库 ③ 宝塔站点。
**破坏性操作**：每层都先 dry-run 展示，**经用户确认后**再 `--apply`。

## 1. 解析参数
- 必须有「项目号 + 域名...」。项目号给数字即可（007→f007，脚本模糊匹配）。
- 只给域名没项目号 → 用 AskUserQuestion 问属于哪个项目（删除必须限定项目，避免误删）。

## 2. CF zone + 后端（app_domains.py del，先 dry-run）
```bash
# dry-run 看计划（后端待删几条、CF zone 待删几个、是否有跨项目保护跳过）：
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/app_domains.py del --project <数字> <域名...>
# 用户确认后 apply：
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/app_domains.py del --project <数字> <域名...> --apply
```
- 默认**连带删 CF zone**；只想删后端落库、保留 CF zone 时加 `--keep-cf`。
- 幂等：某域名后端已无 / CF 无 zone → 自动跳过不报错。
- 跨项目保护：域名其实属于别的项目 → 不删其 CF zone，dry-run 会标「⚠️跨项目跳过」。
- 匹配到多个项目 → 用 AskUserQuestion 让用户选完整 projectCode 再重跑。

## 3. 宝塔站点（bt.py del-domain，先 dry-run）
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py del-domain <projectCode>_app <域名...>
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py del-domain <projectCode>_app <域名...> --apply
```
- **坑：站点匹配按「网站名 OR 根目录」**，某些测试机站点根目录恰好叫 `/www/wwwroot/fNNN_app` 会被误命中。
  dry-run 若看到不属于该项目的测试机（如 `207.56.18.232 香港火苗测试`），用 `--exclude-host 207.56.18.232` 跳过它。
- 主域名不可删（自动 SKIP）；某机已无该域名 → 自动 SKIP。需 Clash 代理。

## 4. 反馈
汇总：CF zone（✅/❌ 每个）+ 后端 deleteBatch 结果 + 宝塔各物理机删除结果（code-block）。
凡 dry-run 与 apply 数量不一致、或有失败/跨项目跳过，明确写进反馈。
