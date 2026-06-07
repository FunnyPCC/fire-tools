---
name: batch-add-gsc
description: Use when the user wants to batch-add domains to Google Search Console, automate DNS TXT verification for multiple domains, or bulk register site properties in GSC. Also trigger when the user mentions GSC bulk operations, Search Console batch setup, domain ownership verification at scale, or site verification automation via Cloudflare DNS.
---

# 批量添加域名到 Google Search Console

通过 Site Verification API 获取验证 token，自动写入 DNS TXT 记录，完成域名所有权验证后添加到 GSC 属性列表。

## 前置条件

如果用户尚未完成以下准备，先引导他们完成：

### 1. Google Cloud 项目配置
1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建项目
2. 启用以下 API：
   - **Search Console API**
   - **Site Verification API**
3. 创建 **OAuth 2.0 客户端 ID**（Desktop 类型）：
   - 需先配置 OAuth 同意屏幕（Consent Screen）
   - 应用类型选"桌面应用"
   - **重要：** 添加测试用户（用户自己的 Gmail），否则无法完成授权
4. 获取凭据：下载客户端 JSON 文件，或记录 Client ID + Client Secret

### 2. DNS 服务商
- 域名需托管在支持 API 的 DNS 服务商（目前脚本模板支持 **Cloudflare**）
- 需要：账号邮箱 + Global API Key（或 API Token）
- 域名必须已作为 zone 添加到 Cloudflare

## 操作流程

### 第一步：优先确认 1Password 凭据

禁止硬编码密钥，始终优先从 1Password 或其他密钥管理器获取。

默认按这个顺序处理 Google 授权：

1. **先检查 1Password 里的 Google OAuth item**
   - item 需包含：`client_id`、`client_secret`、`refresh_token`
   - 如果这三个字段齐全，默认直接用它，不要先向用户索要 JSON 或明文 Client ID/Secret

2. **如果 1Password 中的授权失效**
   - 引导用户在本地终端运行脚本并打开浏览器重新授权
   - 授权成功后，把最新 `refresh_token` 同步回 1Password，并在本地保留 `.gsc_token.json` 作为缓存备份

3. **只有在 1Password 里还没有 Google OAuth 凭据时**，才退回到初始化收集：
   - 方式 A：OAuth JSON 文件（从 Google Cloud Console 下载），路径是？
   - 方式 B：直接提供 Client ID 和 Client Secret
   - 然后先完成一次浏览器授权，再把生成的 `client_id`、`client_secret`、`refresh_token` 存入 1Password

还需要向用户确认以下信息：

```
需要准备以下信息：

1. **Google OAuth 凭据**：
   - 优先：是否已存入 1Password？item 是哪个？
   - 如果已存入，我会先走 1Password；只有授权失效时才引导你去浏览器重新授权
   - 如果未存入，才需要补 OAuth JSON 或 Client ID + Client Secret 做首次初始化

2. **Cloudflare 凭据**：
   - 账号邮箱
   - Global API Key（Cloudflare Dashboard → My Profile → API Tokens → Global API Key）
   
   这些存在密码管理器里（如 1Password）、环境变量里、还是手动提供？

3. **域名列表** — 我会创建一个空白文件供你填写。
   默认放在工作区根目录 `./domains.txt`，放这里可以吗？
```

**1Password 集成：** 用户若用 1Password 存储凭据，通过 `op` CLI 获取：
```bash
# 查找 item
op item list --vault "<vault>" --account "<account_id>" | grep -i cloudflare

# 获取字段（名称匹配失败时改用 UUID）
op item get "<item-id>" --vault "<vault>" --account "<account_id>" --fields "username" --reveal
op item get "<item-id>" --vault "<vault>" --account "<account_id>" --fields "API key" --reveal
```

**注意：** 1Password 字段名区分大小写（`API key` 和 `API Key` 是不同字段）。查询失败时先获取完整 item 查看实际字段名。

### 第二步：创建域名列表文件

**先向用户确认文件位置**，然后创建带注释的空白文件：

```
# domains.txt — 每行一个域名
# 示例：
# example.com
# mydomain.org
```

告知用户：
> 已创建 `domains.txt`，路径：`<path>`。请填入域名（每行一个），填好后告诉我。

### 第三步：生成脚本

从本 skill 的 `scripts/batch_add_gsc.py` 模板复制到用户工作区，配置以下变量：

- `OP_GOOGLE_ITEM` — 首选；1Password item，需包含 `client_id`、`client_secret`、`refresh_token`
- `TOKEN_FILE` — 本地 OAuth 缓存路径（默认 `.gsc_token.json`），作为浏览器授权后的备份
- `OAUTH_JSON` — 仅在首次初始化且 1Password 中还没有 Google OAuth 凭据时使用
- `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` — 仅在首次初始化且没有 JSON 文件时使用
- `DOMAIN_FILE` — 域名列表路径
- Cloudflare 凭据 — 根据用户选择配置 `op` CLI / 环境变量 / 直接赋值

脚本使用 `uv run` + PEP 723 内联依赖声明，无需手动安装包。

### 第四步：运行

**重要：** 脚本每个域名约需 15 秒（含 10s DNS 传播等待），40 个域名约 10 分钟。必须让用户在自己的终端运行以查看实时进度：

```bash
uv run batch_add_gsc.py
```

默认会先尝试使用 1Password 中已有的 Google OAuth 授权；只有当授权失效、refresh_token 不可用，或用户明确要求重登时，才引导浏览器重新授权。

如需强制重新走浏览器授权，可运行：

```bash
uv run batch_add_gsc.py --reauth
```

浏览器授权成功后，应把最新 token 同步回 1Password，并在本地缓存到 `.gsc_token.json`。

**不要通过 Claude 的 Bash 工具运行长耗时脚本** — 用户看不到中间输出，会以为卡住了。

## 脚本工作原理

对每个域名依次执行：

1. **获取验证 token** — Site Verification API，`INET_DOMAIN` + `DNS_TXT` 方式。每个域名的 token 不同，不能复用
2. **查找 Zone ID** — Cloudflare API `GET /zones?name=<domain>`
3. **写入 TXT 记录** — 新增或更新 `google-site-verification` TXT 记录，TTL 120s
4. **等待 DNS 传播** — 10 秒（Cloudflare 足够；其他服务商可能需要更长，最多数分钟到数小时）
5. **验证所有权** — Site Verification API 完成验证
6. **添加到 GSC** — Search Console API 注册为 `sc-domain:` 属性
7. **记录状态** — 每个域名打印成功/失败，最终输出汇总报告

## 常见问题

| 问题 | 解决方案 |
|------|----------|
| OAuth 授权失败 / 403 | 检查 OAuth 同意屏幕是否已添加测试用户（自己的 Gmail） |
| OAuth 浏览器没弹出 | 检查 8099 端口是否被占用，换个端口试试 |
| 取 token 报 `INET_DOMAIN is invalid` | 域名列表写成了带 `https://` 的网址；脚本已自动清洗为纯域名（去 scheme/路径、转小写），无需手动改 |
| 域名找不到 Zone | 域名未添加到 Cloudflare，或 API Key 对应的账号不对 |
| DNS 写入后验证失败 | 增加等待时间（改为 30s）；用 `dig TXT <domain>` 确认记录 |
| 1Password 字段找不到 | 字段名区分大小写；先 `op item get` 查看完整 item |
| 重新运行时 token 过期 | 不要先索要新凭据；先引导用户走浏览器重新授权，并把最新 token 同步回 1Password |
| 没有 `uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

## 扩展其他 DNS 服务商

当前脚本模板仅支持 Cloudflare。若用户使用其他 DNS 服务商（阿里云 DNS、AWS Route53 等），需要：
1. 替换 `get_cf_zone_id()` 和 `write_txt_record()` 为对应服务商的 API 调用
2. 调整 DNS 传播等待时间（非 Cloudflare 可能需要更长）
3. 配置对应服务商的 API 凭据

---

*Created by @FunnyPC & Claude Code*
