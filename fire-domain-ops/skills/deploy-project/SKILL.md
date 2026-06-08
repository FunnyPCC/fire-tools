---
name: deploy-project
description: Use when the user says 提交并推送 / 发布 / 部署(项目/上线) — commit-and-push / release / deploy — while working in a Fire project repo (fire_h5 frontend or fire_admin backend). Runs the full release pipeline: git commit+push to Gitea → trigger Jenkins build (fire_h5_f<NN> frontend / fire_admin_web backend) → deploy on the project's Baota panels (sh deploy_h5.sh f<NN> / sh deploy.sh fadmin) via bt.py, including web1 delete-then-deploy. Do NOT use for a plain git push in non-Fire repos.
---

# Fire 项目发布流水线（提交并推送 → Jenkins → 宝塔部署）

当用户在 **fire_h5**（前台）或 **fire_admin**（后台）仓库里说"**提交并推送**"（或 发布 / 部署 / 上线）时，执行这条完整流水线。把单纯的 `git push` 留给普通场景——这个 skill 只在 Fire 前台/后台仓库里、用户要"发布"时用。

工具：
- Jenkins：`uv run ${CLAUDE_PLUGIN_ROOT}/scripts/jenkins.py`（凭据读 `~/.fire/jenkins.json`，见末尾）
- 宝塔面板查找 + 执行：`uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py`（`find-site` + `exec`，复用同插件）

## 前置检查

1. `~/.fire/jenkins.json` 必须存在（`{url,user,token}`）。不存在时 jenkins.py 会报错并给出建立方法——转告用户先建。
2. 宝塔操作需要 Clash/mihomo 在 `127.0.0.1:7897` 运行（见 bt-panel-ops skill）。

## 识别前台 / 后台 + 项目编号

- **看当前 git 仓库**（`git remote -v` 或目录名）：
  - 含 `fire_h5` → **前台**
  - 含 `fire_admin` → **后台**
  - 都不是 → 这个 skill 不适用，按普通 push 处理。
- **项目编号（前台）**：从当前分支名取 `F<数字>`，转小写 `f<数字>`。
  例：`fire073-F076` → `f076`；`fire-BlackFrom023-F068` → `f068`。
  （`git branch --show-current`，正则 `F(\d+)`，取最后一个匹配。）
- **后台**恒为 `fadmin`，没有按编号的后台 job/站点。

派生量（务必在确认时展示给用户核对）：

| | 前台 (fire_h5) | 后台 (fire_admin) |
|---|---|---|
| Jenkins job | `fire_h5_f<NN>` | `fire_admin_web` |
| 宝塔站点 | `f<NN>_app` | `fadmin` |
| 部署命令 | `cd /root && sh deploy_h5.sh f<NN>` | `cd /root && sh deploy.sh fadmin` |

## 流水线步骤

### 1. 提交并推送到 Gitea
- `git status` 看改动；按**该仓库已有的提交信息风格**拟一条 commit message（如 fire_h5 用 `feat(F076): ...` / `fix(F076): ...`），提交所有改动。
- 若当前在默认分支，先停一下问用户（一般 Fire 项目都在 `fireXXX-FNN` 分支上）。
- `git push`。push 失败若是 `fetch first`（别人推过）→ `git fetch` + rebase 后重推（参考本会话经验；无冲突才自动 rebase，有冲突让用户处理）。

### 2.（仅前台）判断是否动了 web1
- `git show --stat HEAD`（或本次推送范围）看有没有改 `web1-static/`。
- 改了 → **web1 模式**：部署命令前面加 `rm -rf /www/wwwroot/f<NN>_app/web1 &&`。
  原因：`deploy_h5.sh` 会备份并恢复旧 web1，不删就更新不到官网（见 reference）。
- 没改 → 普通模式。
- 拿不准就问用户"这次改动涉及官网(web1)吗？"

### 3. 触发 Jenkins 构建并等待
```
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/jenkins.py build <job> --wait
```
- 退出码 0 = SUCCESS → 继续；非 0（2=构建失败 / 1=错误超时）→ **中止，不部署**，把 `jenkins.py console <job>` 的尾部贴给用户排障。
- ⚠️ 别并发触发多个 fire_h5 job（会 OOM，见 jenkins memory）——一次一个。

### 4. 找目标面板
```
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py find-site <站点>
```
- 前台站点 `f<NN>_app`、后台站点 `fadmin`。
- 结果通常是 **主网关 + 专用面板** 两台物理机（`128.241.233.59` 与 `154.81.136.4` 是同机双 IP，只跑一个）。

### 5. 确认 → 逐面板部署（生产闸）
- 先列给用户看：**目标面板（去重后的 IP）+ 将执行的完整命令**，等用户确认（除非用户明确说过这次不用确认）。
- 确认后，对每台物理机：
```
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py --filter "<ip正则,|分隔>" exec "<部署命令>"
```
  - 前台普通：`cd /root && sh deploy_h5.sh f<NN>`
  - 前台 web1：`rm -rf /www/wwwroot/f<NN>_app/web1 && cd /root && sh deploy_h5.sh f<NN>`
  - 后台：`cd /root && sh deploy.sh fadmin`
- exec 走的是 crontab 任务方式（Baota v11 RunShell 不支持任意 shell），日志里看到 `] Successful` 即成功。

### 6. 验证 + 汇报
- 前台：grep 部署目录确认改动上线（**JS chunk 在 `js/`，不在 `assets/`**；web1 在 `web1/index.html`）。
  例：`bt.py exec "grep -rl <关键串> /www/wwwroot/f<NN>_app"`。
- 按面板汇报 SUCCESS/FAIL + 构建号 + 部署结论。

## 凭据：`~/.fire/jenkins.json`（首次使用要建）
```json
{ "url": "http://<jenkins-host>:9090", "user": "pt", "token": "<Jenkins API token>" }
```
- `chmod 600`，跟 `~/.fire/ops/panels.yml` 一样放 `~/.fire/`，**不进任何 git 仓库**。
- token 在 Jenkins → 用户 → 配置 → API Token 生成。

## 关联
- 部署脚本细节 / web1 保留坑 / JS 在 js/：见用户 memory `reference_h5_deploy_panels`。
- 面板 exec(crontab 方式) / find-site / 同机去重：见 `bt-panel-ops` skill + memory `bt-client-batch-ops`。
- Jenkins job 命名 / 别并发 / 构建诊断：见用户 memory `jenkins-api`。
