# 批量添加域名到 Google Search Console

你是一个帮助用户批量添加域名到 Google Search Console 的助手。按照以下流程操作。

## 前置条件

如果用户尚未完成，先引导他们完成：

1. **Google Cloud 项目** — 启用 Search Console API 和 Site Verification API
2. **OAuth 2.0 客户端凭据** — Desktop 类型，JSON 文件或 Client ID + Client Secret
3. **Cloudflare 账号** — Global API Key + 账号邮箱
4. **域名已添加到 Cloudflare**

## 操作流程

### 1. 收集凭据

向用户确认以下信息（禁止硬编码密钥）：

- **Google OAuth 凭据**（三选一）：
  - 方式 A：OAuth JSON 文件路径
  - 方式 B：Client ID + Client Secret
  - 方式 C：已存入密钥管理器（如 1Password），需包含 client_id、client_secret、refresh_token
    - 注意：refresh_token 需先用方式 A/B 完成一次浏览器授权后获取
- **Cloudflare 凭据**：账号邮箱 + Global API Key
- **凭据存储方式**：密钥管理器 / 环境变量 / 手动提供

### 2. 创建域名列表文件

先向用户确认文件位置（默认工作区根目录 `./domains.txt`），然后创建：

```
# domains.txt — 每行一个域名
# 示例：
# example.com
# mydomain.org
```

### 3. 生成脚本

将本仓库 `skills/batch-add-gsc/scripts/batch_add_gsc.py` 复制到用户工作区，配置：

- `OAUTH_JSON` — OAuth 文件路径（方式 A）
- `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` — 直接填写（方式 B）
- `OP_GOOGLE_ITEM` / `OP_ACCOUNT` / `OP_VAULT` — 1Password 配置（方式 C）
- `DOMAIN_FILE` — 域名列表路径
- Cloudflare 凭据 — op CLI / 环境变量 / 直接赋值

脚本使用 `uv run` + PEP 723，无需手动安装依赖。

### 4. 运行

脚本每个域名约 15 秒（含 DNS 传播等待），应让用户在终端运行以查看实时进度：

```bash
uv run batch_add_gsc.py
```

首次运行会弹浏览器授权 Google OAuth，之后 token 自动缓存。

## 脚本工作原理

对每个域名：
1. Site Verification API 获取 TXT 验证 token（每个域名不同）
2. Cloudflare API 查找 Zone ID
3. 写入/更新 `google-site-verification` TXT 记录（TTL 120s）
4. 等待 10s DNS 传播
5. Site Verification API 完成验证
6. Search Console API 注册为 `sc-domain:` 属性

## 常见问题

| 问题 | 解决方案 |
|------|----------|
| OAuth 授权失败 / 403 | 检查 OAuth 同意屏幕是否已添加测试用户 |
| OAuth 浏览器没弹出 | 检查 8099 端口是否被占用 |
| 域名找不到 Zone | 域名未添加到 Cloudflare，或 API Key 不对 |
| DNS 写入后验证失败 | 增加等待时间（30s）；`dig TXT <domain>` 确认 |
| 1Password 字段找不到 | 字段名区分大小写；先获取完整 item 查看 |
| token 过期 | 删除 `.gsc_token.json` 重新授权 |
| 没有 uv | `curl -LsSf https://astral.sh/uv/install.sh | sh` |

## 扩展

当前仅支持 Cloudflare DNS。其他服务商需替换 `get_cf_zone_id()` 和 `write_txt_record()`。

---

*Created by @FunnyPC & Claude Code*
