# huozhong-domain-ops

火种域名运维工具箱(Claude Code 插件)。把「域名运维」整套工作流的各阶段封装成独立 skill,共享一套凭证策略。

## 包含的 skill

| skill | 阶段 | 触发 |
|---|---|---|
| `domain-inventory` | firepikata 域名×项目库存盘点 + Google 风险(Safe Browsing)/ 存活检测 / 可用域名 | 自动 |
| `batch-add-gsc` | 批量加域名到 Google Search Console(Cloudflare DNS TXT 验证) | 自动 |
| `baota-site-mgmt` | 宝塔面板批量站点 / 域名管理(*规划中,从 fire-bt-ops 并入*) | — |

## 凭证策略(全工具箱通用)

**1Password 是源,本地是缓存。** `lib/op_secrets.py`:环境变量 > 本地缓存 `./gsc/.secrets.json` > 1Password(取出后写回缓存);失效/过期自动 `refresh` 回 1P。**插件 repo 不含任何密钥** —— 密钥只在用户本地缓存(gitignore)。解决「每次用都要 op 授权」的痛点。

## 数据位置

各 skill 的产出数据放在**当前项目**目录(如 domain-inventory 写 `./域名维护/`),与插件代码隔离,便于插件重装/版本控制。

## 依赖

- [uv](https://docs.astral.sh/uv/) — Python 运行器(自动装依赖)
- 1Password CLI `op`(仅本地缓存缺失/失效时才需要桌面端解锁)

## 跨平台

仓库含 `AGENTS.md`,供 Codex 等其他平台复用同一工作流。

## 许可证

MIT

---

*Created by @FunnyPC & Claude Code*
