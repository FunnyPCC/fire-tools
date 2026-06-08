# deploy-project —— Fire 项目发布流水线

在 **fire_h5**（前台）或 **fire_admin**（后台）仓库里说"**提交并推送 / 发布 / 部署**"，自动跑完整发布：
git 提交+推送 → Jenkins 构建 → 宝塔对应面板执行更新命令。

由 `skills/deploy-project/SKILL.md` 编排，用两个脚本：
- `scripts/jenkins.py` —— 触发 Jenkins 构建并等待（读 `~/.fire/jenkins.json`）
- `scripts/bt.py` —— 宝塔面板 `find-site` 找站点 + `exec` 跑部署命令（复用）

## 一次性配置：`~/.fire/jenkins.json`

跟 `~/.fire/ops/panels.yml` 一样放 `~/.fire/`，**不进任何 git 仓库**：

```json
{ "url": "http://<jenkins-host>:9090", "user": "pt", "token": "<Jenkins API token>" }
```

- token：Jenkins → 用户 → 配置 → API Token 生成
- 建议 `chmod 600`（Windows 上 `~/.fire` 本身就是用户私有目录）
- 可用环境变量 `FIRE_JENKINS_CONF` 覆盖路径

## 前台 vs 后台对照

| | 前台 (fire_h5) | 后台 (fire_admin) |
|---|---|---|
| 项目编号 | 分支名取 `F<NN>` → `f<NN>` | 恒为 `fadmin` |
| Jenkins job | `fire_h5_f<NN>` | `fire_admin_web` |
| 宝塔站点 | `f<NN>_app` | `fadmin` |
| 部署命令 | `cd /root && sh deploy_h5.sh f<NN>` | `cd /root && sh deploy.sh fadmin` |

## 流程

1. 提交+推送到 Gitea（按仓库提交风格拟 message；push 被别人抢先则 fetch+rebase 重推）
2.（前台）查本次是否改了 `web1-static/` → 改了就**先删 web1 目录再部署**（`deploy_h5.sh` 会保留旧 web1，不删更新不到官网）
3. `uv run scripts/jenkins.py build <job> --wait` —— 失败则中止不部署
4. `uv run scripts/bt.py find-site <站点>` —— 主网关 + 专用面板（同机双 IP 去重）
5. 列面板+命令**确认一次** → `bt.py exec` 逐面板部署
6. 验证（前台 JS chunk 在 `js/` 不在 `assets/`；web1 在 `web1/index.html`）+ 汇报

## jenkins.py 速查

```bash
uv run scripts/jenkins.py build fire_h5_f076 --wait   # 触发+等待 (退出码 0=SUCCESS,2=失败,1=错误/超时)
uv run scripts/jenkins.py status fire_admin_web        # 最近一次构建状态
uv run scripts/jenkins.py console fire_h5_f076         # 拉 console 排障
```

## 坑（来自实战）

- **别并发触发多个 `fire_h5_*` job** —— 同机两个 `npm/vite build` 会 OOM 假死，一个一个来。
- **官网(web1) 不会被 `deploy_h5.sh` 更新** —— 它备份并恢复旧 web1，必须先 `rm -rf .../web1` 再部署。
- **宝塔 v11 不支持签名 API 跑任意 shell** —— bt.py exec 走临时 crontab 任务绕过（已封装）。

---

*Created by @FunnyPC & Claude Code*
