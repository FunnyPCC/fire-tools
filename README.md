# batch-add-gsc

Claude Code plugin for batch-adding domains to Google Search Console.

Automates the entire flow: get DNS verification tokens → write Cloudflare TXT records → verify domain ownership → register as GSC properties.

## Install

```bash
claude plugin add --from github:FunnyPCC/batch-add-gsc
```

## Prerequisites

1. **Google Cloud project** with Search Console API and Site Verification API enabled
2. **OAuth 2.0 credentials** (Desktop type) — JSON file or Client ID + Secret
3. **Cloudflare account** with API access for your domains
4. **[uv](https://docs.astral.sh/uv/)** — Python package runner (auto-installs dependencies)

## Usage

Once installed, just tell Claude Code:

> "帮我批量添加域名到 Google Search Console"

or

> "batch add my domains to GSC"

The skill will guide you through:
1. Collecting credentials (supports 1Password, env vars, or manual input)
2. Creating a domain list file
3. Generating and configuring the script
4. Running the batch process

## Credentials

The script supports multiple credential sources:

| Source | Google OAuth | Cloudflare |
|--------|-------------|------------|
| 1Password `op` CLI | client_id + client_secret + refresh_token | username + API key |
| Environment variables | — | CF_EMAIL + CF_API_KEY |
| JSON file | OAuth JSON from Google Cloud Console | — |
| Direct input | Client ID + Client Secret | — |

## License

MIT

---

*Created by @FunnyPCC & Claude Code*
