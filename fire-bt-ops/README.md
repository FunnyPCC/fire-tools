# fire-bt-ops

Fire 项目 43+ 台宝塔面板批量管理的 Claude Code 插件。把 bt-client 的本地 SQLite 解出来，通过 Clash 代理调宝塔 OpenAPI 做批量加域名 / 漂移检测 / 站点查询。

## 解决什么问题

- 面板太多（43+）逐台点点点不现实
- 多人维护，配置经常变（站点名/IP/token）
- 同一个 fNNN_app 站点散落在 2-3 台机器（主网关镜像 + 专用网关），加域名容易漏
- 漂移：长期人工维护下，镜像之间域名列表不一致
- 加完要把"新增域名"复制给下游

## 架构

```
~/.claude/plugins/fire-bt-ops/   ← 插件代码 (这里)
├── .claude-plugin/plugin.json
├── skills/
│   ├── bt-panel-ops/             ← 知识 skill: 自动触发, 编码所有工作流规则
│   ├── add-domain/               ← /fire-bt-ops:add-domain
│   ├── sync-panels/              ← /fire-bt-ops:sync-panels
│   ├── sync-domains/             ← /fire-bt-ops:sync-domains
│   ├── domains/                  ← /fire-bt-ops:domains
│   ├── find-site/                ← /fire-bt-ops:find-site
│   ├── list-sites/               ← /fire-bt-ops:list-sites
│   └── setup/                    ← /fire-bt-ops:setup
├── hooks/hooks.json              ← SessionStart 自动 sync 检查
└── scripts/
    ├── bt.py                     ← 主 CLI
    ├── setup.py                  ← 首次安装解密 SQLite
    └── session_sync.py           ← SessionStart hook 脚本

~/.fire/ops/                     ← 数据 (跟代码隔离)
├── panels.yml                    ← 43 面板 + 解密 sk + host_aliases (setup 生成, .gitignore)
├── sites.csv                     ← 466 站点缓存
├── sites_unreachable.csv         ← 17 台 API 不通
└── domain_add.log                ← 加域名审计日志
```

数据和代码分离的原因：插件可以重装/版本控制，但 sk 不能进 git。

## 依赖

| 依赖 | 必需 | 安装 |
|---|---|---|
| Python 3.10+ | ✓ | 系统装 |
| pycryptodome | ✓ | `pip install pycryptodome` |
| 堡塔多机管理 (bt-client) | ✓ | https://www.bt.cn/ — 用过它的导入，本地 SQLite 才有数据 |
| Clash Verge / mihomo | ✓ | 必须运行在 `127.0.0.1:7897`，面板侧有 IP 白名单 |

## 首次安装

1. 装好上面所有依赖
2. 在 bt-client 里把所有面板配好（或导入备份），确认能看到 panel 列表
3. 在 Claude Code 里跑 `/fire-bt-ops:setup`
   - 解密 SQLite → `~/.fire/ops/panels.yml`（含 43 面板 + sk）
   - 拉站点列表 → `~/.fire/ops/sites.csv`
4. 跑 `/fire-bt-ops:find-site f068_app` 验证

## 日常用法

| 你说 | 插件触发 | 干啥 |
|---|---|---|
| "查 065 的域名" | `bt-panel-ops` + `domains` skill | 实时查 f065_app 在所有匹配面板上的域名列表 |
| "把 X 加到 NNN" | `bt-panel-ops` + `add-domain` skill | dry-run → 给你看匹配 + 漂移 → 等你 ok → apply → 返回代码块格式 |
| "刚导了配置" | `sync-panels` skill | 重读 SQLite，diff 后告诉你哪里变了 |
| "把 065 的域名对齐" | `sync-domains` skill | 取并集，把缺的补到对应机器 |
| "f068 在哪几个面板" | `find-site` skill | 离线查 sites.csv |

会话开始 SessionStart hook 自动跑一次 `bt.py sync`，有 diff 就在系统上下文里提示。

## 关键工作流规则

详见 `skills/bt-panel-ops/SKILL.md`。摘要：

1. **会话开始先 sync** — 防被并行维护者打架
2. **加域名前给你看匹配** — 别瞎加
3. **匹配按 fNNN_app** （前台）或 `fNNN`（后台），不靠面板名
4. **按物理机去重** —— host_aliases 标记同机多 IP（已知 `[128.241.233.59, 154.81.136.4]`）
5. **每次加完自动检漂移** — 但不擅自补，问你
6. **疑似废弃面板（名字带"暂时没用/镜像"）问你跳不跳**
7. **加完输出固定代码块格式** —— 复制给下游

## 已知限制

- **只覆盖 4 个分组**: 公共 / ptn项目 / 网关 / 东京网关（新材料 5 台内网未支持）
- **17/43 面板 API 不通**: 面板侧 IP 白名单没加 Clash 出口 `47.237.51.86`，逐台加白后可用
- **TLS 不校验**: 宝塔自签证书 + Clash 链路 + 已知 IP 锁定 = 没有 PKI 信任链。安全模型靠 Clash 出口
- **仅 Windows 已测**: setup.py 读 `%APPDATA%\bt-client\`，mac/linux 路径需调整

## 安全

`~/.fire/ops/panels.yml` 含明文 sk（解密后的 Baota API 密钥），chmod 600，并 `.gitignore`。

不要把 `~/.fire/ops/` 加进任何 git 仓库。插件本身（`~/.claude/plugins/fire-bt-ops/`）只有代码、不含密钥，可以独立 git 化。

## 审计

每次 `add-domain --apply` 和 `sync-domains --apply` 都 append 一行到 `~/.fire/ops/domain_add.log`：

```
2026-06-08T00:37:09 OK 128.241.233.59 tonyhood.vip domain=robinhoodx-otc.com msg=添加成功
2026-06-08T00:37:17 SYNC-OK 128.241.233.59 tonyhood.vip domain=xavryntheqol.online msg=添加成功
```

## 故障排查

| 症状 | 检查 |
|---|---|
| `sync` 报 `panels.yml 不存在` | 跑 `/fire-bt-ops:setup` |
| `add-domain` 全部 fail / SSL EOF | Clash Verge 没运行 或 没监听 `127.0.0.1:7897` |
| 单台面板长期不通 | 那台面板 `面板设置 → API 接口 → 限制IP` 加白 `47.237.51.86` |
| SessionStart 提示有变化但你没动 | 是另一个维护者改的 —— 跑 `/fire-bt-ops:sync-panels --apply` |

## 一切的源头

- 解密算法来自 bt-client 的 `app.asar` 内嵌 Go 源码（offset ~6514103）
- AES-128-ECB 是 bt-client 的存储格式，我们配合解密、不是我们设计的（安全审计同行：你们对，ECB 不好，但这不是我们能改的层）
