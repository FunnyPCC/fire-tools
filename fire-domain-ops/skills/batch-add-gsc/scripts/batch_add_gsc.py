#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-api-python-client>=2.0",
#     "google-auth-oauthlib>=1.0",
#     "requests>=2.28",
# ]
# ///
"""
Batch add domains to Google Search Console.

Flow per domain:
  1. Site Verification API → get TXT token
  2. Cloudflare API → write DNS TXT record
  3. Wait for DNS propagation
  4. Site Verification API → verify ownership
  5. Search Console API → add as property

Usage:
  uv run batch_add_gsc.py
  uv run batch_add_gsc.py --reauth

Credentials:
  - Google OAuth: prefer reading client_id/client_secret/refresh_token from 1Password
  - If the stored Google authorization is invalid or expired, re-authorize in browser
    and sync the latest refresh_token back to 1Password
  - Cloudflare: configure via get_cf_credentials() — supports 1Password op CLI,
    environment variables, or direct assignment
"""

import argparse
import json
import os
import subprocess
import sys
try: sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception: pass
import time
from datetime import datetime
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Configuration — EDIT THESE ────────────────────────
SCRIPT_DIR = Path(__file__).parent
OAUTH_JSON = SCRIPT_DIR / "OAuth.json"         # Path to Google OAuth client credentials JSON
DOMAIN_FILE = Path("./gsc/domains.txt")         # 域名清单(CWD 相对; --domains 会自动写入)
LOG_DIR = Path("./gsc/logs")                    # 运行日志目录(可 tail -F latest.log)
TOKEN_FILE = SCRIPT_DIR / ".gsc_token.json"     # Cached OAuth token (auto-generated)
DNS_WAIT_SECONDS = 10                           # Seconds to wait for DNS propagation
OAUTH_PORT = 8099                               # Local port for OAuth redirect

# Google OAuth — Method B: direct Client ID + Secret (if no JSON file)
OAUTH_CLIENT_ID = ""       # e.g. "123456-xxx.apps.googleusercontent.com"
OAUTH_CLIENT_SECRET = ""   # e.g. "GOCSPX-xxx"

# 1Password 引用 ID（账号/vault/CF item/Google item）不硬编码进插件（仓库可能公开）。
# 按优先级读取： 环境变量  >  ~/.fire/gsc.json（GSC_CONFIG 可覆盖路径）  >  空
# ~/.fire/gsc.json 示例：
#   {"op_account":"<account-id>","op_vault":"<vault>",
#    "op_cf_item":"<cloudflare item id>","op_google_item":"<google oauth item id>"}
# 对应 1Password item 字段：CF → username + "API key"；Google → client_id/client_secret/refresh_token
def _load_gsc_op_config():
    cfg = {}
    p = Path(os.environ.get("GSC_CONFIG") or os.path.expanduser("~/.fire/gsc.json"))
    try:
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    pick = lambda env, key: (os.environ.get(env) or cfg.get(key) or "")
    return (
        pick("GSC_OP_ACCOUNT", "op_account"),
        pick("GSC_OP_VAULT", "op_vault"),
        pick("GSC_OP_CF_ITEM", "op_cf_item"),
        pick("GSC_OP_GOOGLE_ITEM", "op_google_item"),
    )

OP_ACCOUNT, OP_VAULT, OP_ITEM, OP_GOOGLE_ITEM = _load_gsc_op_config()
# Cloudflare 也可用环境变量 CF_EMAIL / CF_API_KEY（get_cf_credentials 内回退）
# ──────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/siteverification",
    "https://www.googleapis.com/auth/webmasters",
]

CF_API = "https://api.cloudflare.com/client/v4"


# ── Terminal colors ───────────────────────────────────
# Auto-disabled when stdout isn't a TTY (piped/redirected) or NO_COLOR is set.
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _paint(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def green(t):  return _paint("32", t)
def red(t):    return _paint("31", t)
def yellow(t): return _paint("33", t)
def cyan(t):   return _paint("36", t)
def bold(t):   return _paint("1", t)
def dim(t):    return _paint("2", t)


def get_cf_credentials():
    """
    Get Cloudflare email and Global API Key.
    Uncomment the method that matches your setup.
    """
    # ── Method A: 1Password ──
    if OP_ACCOUNT and OP_ITEM:
        print("  Reading Cloudflare credentials from 1Password...")
        try:
            op_base = ["op", "item", "get", OP_ITEM, "--account", OP_ACCOUNT, "--reveal"]
            if OP_VAULT:
                op_base += ["--vault", OP_VAULT]
            email = subprocess.check_output(op_base + ["--fields", "username"], text=True).strip()
            api_key = subprocess.check_output(op_base + ["--fields", "API key"], text=True).strip()
            return email, api_key
        except subprocess.CalledProcessError as e:
            print(f"  1Password error: {e}")
            print("  Make sure you've run: op signin --account " + OP_ACCOUNT)
            sys.exit(1)

    # ── Method B: Environment variables ──
    email = os.environ.get("CF_EMAIL")
    api_key = os.environ.get("CF_API_KEY")
    if email and api_key:
        return email, api_key

    # ── Method C: Direct (uncomment and fill in above) ──
    # if CF_EMAIL_DIRECT and CF_API_KEY_DIRECT:
    #     return CF_EMAIL_DIRECT, CF_API_KEY_DIRECT

    print("ERROR: No Cloudflare credentials configured.")
    print("Edit get_cf_credentials() in this script — see comments for options.")
    sys.exit(1)


def _build_oauth_flow():
    """Build OAuth flow from JSON file or direct Client ID/Secret."""
    if OAUTH_JSON.exists():
        return InstalledAppFlow.from_client_secrets_file(str(OAUTH_JSON), SCOPES)

    if OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET:
        client_config = {
            "installed": {
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        return InstalledAppFlow.from_client_config(client_config, SCOPES)

    print("ERROR: No Google OAuth credentials configured.")
    print("Either set OAUTH_JSON path or fill in OAUTH_CLIENT_ID + OAUTH_CLIENT_SECRET.")
    sys.exit(1)


def _op_base(item):
    base = ["op", "item", "get", item, "--account", OP_ACCOUNT, "--reveal"]
    if OP_VAULT:
        base += ["--vault", OP_VAULT]
    return base



def _google_auth_fields_from_1password():
    """Read Google OAuth client credentials and refresh_token from 1Password."""
    if not (OP_GOOGLE_ITEM and OP_ACCOUNT):
        return None
    print("  Reading Google OAuth credentials from 1Password...")
    try:
        op_base = _op_base(OP_GOOGLE_ITEM)
        client_id = subprocess.check_output(op_base + ["--fields", "client_id"], text=True).strip()
        client_secret = subprocess.check_output(op_base + ["--fields", "client_secret"], text=True).strip()
        refresh_token = subprocess.check_output(op_base + ["--fields", "refresh_token"], text=True).strip()
        return client_id, client_secret, refresh_token
    except subprocess.CalledProcessError as e:
        print(f"  1Password error: {e}")
        return None



def _sync_refresh_token_to_1password(refresh_token):
    """Try to update the latest refresh_token back into 1Password."""
    if not (OP_GOOGLE_ITEM and OP_ACCOUNT and refresh_token):
        return
    try:
        subprocess.check_call(
            ["op", "item", "edit", OP_GOOGLE_ITEM, f"refresh_token={refresh_token}", "--account", OP_ACCOUNT]
            + (["--vault", OP_VAULT] if OP_VAULT else []),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("  Synced latest refresh_token back to 1Password")
    except subprocess.CalledProcessError:
        print("  Warning: failed to sync refresh_token back to 1Password; local cache still updated")


def google_auth(force_reauth=False):
    """Google OAuth 2.0 — prefer 1Password, then re-authorize in browser if needed."""
    from google.auth.transport.requests import Request

    op_fields = _google_auth_fields_from_1password()
    creds = None

    if op_fields:
        client_id, client_secret, refresh_token = op_fields
        if not force_reauth:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES,
            )
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"  Google authorization in 1Password is invalid or expired: {e}")
                creds = None
        if force_reauth or not creds:
            print("  Opening browser for Google re-authorization...")
            flow = InstalledAppFlow.from_client_config(
                {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["http://localhost"],
                    }
                },
                SCOPES,
            )
            creds = flow.run_local_server(port=OAUTH_PORT, prompt="consent")
            _sync_refresh_token_to_1password(creds.refresh_token)
    else:
        print("  No Google OAuth credentials in 1Password; falling back to local OAuth config...")
        flow = _build_oauth_flow()
        creds = flow.run_local_server(port=OAUTH_PORT, prompt="consent")

    TOKEN_FILE.write_text(creds.to_json())
    print("  Google auth ready")

    return creds


def normalize_domain(raw):
    """Reduce a raw line to a bare hostname for INET_DOMAIN.

    Strips scheme (https://), any path/query, surrounding whitespace and
    trailing dots, then lowercases. The Site Verification API rejects
    'https://Example.com/' — it wants 'example.com'.
    """
    d = raw.strip()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split("?", 1)[0]   # drop path / query
    d = d.strip().rstrip(".")
    return d.lower()


def cf_headers(email, api_key):
    return {
        "X-Auth-Email": email,
        "X-Auth-Key": api_key,
        "Content-Type": "application/json",
    }


def get_verification_token(sv_service, domain):
    """Get the DNS TXT verification token for a domain."""
    resp = sv_service.webResource().getToken(body={
        "site": {"type": "INET_DOMAIN", "identifier": domain},
        "verificationMethod": "DNS_TXT",
    }).execute()
    return resp["token"]


def get_cf_zone_id(domain, email, api_key):
    """Look up the Cloudflare Zone ID for a domain."""
    resp = requests.get(
        f"{CF_API}/zones",
        headers=cf_headers(email, api_key),
        params={"name": domain, "status": "active"},
    )
    data = resp.json()
    if not data.get("success") or not data.get("result"):
        return None
    return data["result"][0]["id"]


def write_txt_record(zone_id, domain, token, email, api_key):
    """Add or update the google-site-verification TXT record."""
    headers = cf_headers(email, api_key)

    # Check for existing google-site-verification record
    resp = requests.get(
        f"{CF_API}/zones/{zone_id}/dns_records",
        headers=headers,
        params={"type": "TXT", "name": domain},
    )
    for rec in resp.json().get("result", []):
        if "google-site-verification" in rec.get("content", ""):
            requests.put(
                f"{CF_API}/zones/{zone_id}/dns_records/{rec['id']}",
                headers=headers,
                json={"type": "TXT", "name": domain, "content": token, "ttl": 120},
            )
            return "updated"

    # Create new record
    resp = requests.post(
        f"{CF_API}/zones/{zone_id}/dns_records",
        headers=headers,
        json={"type": "TXT", "name": domain, "content": token, "ttl": 120},
    )
    return "created" if resp.json().get("success") else f"failed: {resp.json().get('errors')}"


def verify_domain(sv_service, domain):
    """Complete domain ownership verification."""
    return sv_service.webResource().insert(
        body={
            "site": {"type": "INET_DOMAIN", "identifier": domain},
            "verificationMethod": "DNS_TXT",
        },
        verificationMethod="DNS_TXT",
    ).execute()


def add_to_search_console(sc_service, domain):
    """Register domain as a Search Console property."""
    try:
        sc_service.sites().add(siteUrl=f"sc-domain:{domain}").execute()
        return True
    except Exception as e:
        return "already exists" in str(e).lower()


class _Tee:
    """把 stdout 同时写到终端与日志文件（便于 tail -F + 后台运行留痕）。"""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def _setup_log(log_path=None):
    if log_path:
        p = Path(log_path)
    else:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        p = LOG_DIR / f"batch-add-gsc-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    logf = open(p, "a", buffering=1, encoding="utf-8")
    latest = p.parent / "latest.log"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(p.name)
    except OSError:
        pass
    sys.stdout = _Tee(sys.__stdout__, logf)
    return p


def main():
    ap = argparse.ArgumentParser(description="批量加域名到 Google Search Console")
    ap.add_argument("--domains", nargs="+", help="直接给域名（空格分隔），会自动写入 domains.txt")
    ap.add_argument("--from", dest="from_file", help="从指定文件读域名（每行一个）")
    ap.add_argument("--reauth", action="store_true", help="强制浏览器重新授权 Google")
    ap.add_argument("--log", dest="log_path", help="日志文件路径（默认 ./gsc/logs/...）")
    args = ap.parse_args()
    force_reauth = args.reauth

    log_path = _setup_log(args.log_path)

    # Validate Google OAuth config
    has_oauth = (
        OAUTH_JSON.exists()
        or (OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET)
        or (OP_GOOGLE_ITEM and OP_ACCOUNT)
        or TOKEN_FILE.exists()
    )
    if not has_oauth:
        print("ERROR: No Google OAuth credentials configured.")
        print("Set OAUTH_JSON, OAUTH_CLIENT_ID+SECRET, or OP_GOOGLE_ITEM.")
        sys.exit(1)

    # 域名来源优先级：--domains > --from > domains.txt（自动建/写）
    if args.domains:
        raw_lines = list(args.domains)
        DOMAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        DOMAIN_FILE.write_text("\n".join(args.domains) + "\n", encoding="utf-8")
        src = f"--domains（已写入 {DOMAIN_FILE}）"
    elif args.from_file:
        fp = Path(args.from_file)
        if not fp.exists():
            print(f"ERROR: --from 文件不存在: {fp}")
            sys.exit(1)
        raw_lines = fp.read_text(encoding="utf-8").splitlines()
        src = str(fp)
    else:
        if not DOMAIN_FILE.exists():
            print(f"ERROR: 无域名输入。用 --domains a.com b.com，或 --from <文件>，"
                  f"或在 {DOMAIN_FILE} 填域名（每行一个）。")
            sys.exit(1)
        raw_lines = DOMAIN_FILE.read_text(encoding="utf-8").splitlines()
        src = str(DOMAIN_FILE)

    seen, domains = set(), []
    for line in raw_lines:
        if not line.strip() or line.strip().startswith("#"):
            continue
        d = normalize_domain(line)
        if d and d not in seen:
            seen.add(d)
            domains.append(d)
    if not domains:
        print("ERROR: 没有有效域名")
        sys.exit(1)

    print(bold("=== Batch Add to Google Search Console ==="))
    print(dim(f"    来源: {src}"))
    print(dim(f"    {len(domains)} domains to process"))
    print(dim(f'    日志: {log_path}  (实时: tail -F "{(log_path.parent / "latest.log").resolve()}")\n'))

    # Credentials
    print(cyan("[1/2] Getting credentials..."))
    cf_email, cf_api_key = get_cf_credentials()
    creds = google_auth(force_reauth=force_reauth)

    # Build API services
    print(cyan("[2/2] Connecting to Google APIs...\n"))
    sv_service = build("siteVerification", "v1", credentials=creds)
    sc_service = build("searchconsole", "v1", credentials=creds)

    ok, failed, no_zone = [], [], []

    for i, domain in enumerate(domains, 1):
        print(bold(f"[{i}/{len(domains)}] {domain}"))

        zone_id = get_cf_zone_id(domain, cf_email, cf_api_key)
        if not zone_id:
            print(yellow("  ⏭️  skip — not found in Cloudflare"))
            no_zone.append(domain)
            continue

        try:
            token = get_verification_token(sv_service, domain)
            print(dim(f"  🔑 token   {token[:42]}…"))
        except Exception as e:
            print(red(f"  ❌ get token — {e}"))
            failed.append((domain, str(e)))
            continue

        status = write_txt_record(zone_id, domain, token, cf_email, cf_api_key)
        print(dim(f"  📝 DNS TXT  {status}"))

        print(dim(f"  ⏳ waiting {DNS_WAIT_SECONDS}s for DNS propagation…"))
        time.sleep(DNS_WAIT_SECONDS)

        try:
            verify_domain(sv_service, domain)
            print(green("  ✅ verified"))
        except Exception as e:
            if "already verified" in str(e).lower():
                print(green("  ✅ already verified"))
            else:
                print(red(f"  ❌ verify — {e}"))
                failed.append((domain, str(e)))
                continue

        if add_to_search_console(sc_service, domain):
            print(green("  ✅ added to Search Console"))
            ok.append(domain)
        else:
            print(yellow("  ⚠️  could not add to Search Console"))
            failed.append((domain, "add to SC failed"))

    # Summary
    print("\n" + "═" * 50)
    print(
        "RESULTS:  "
        + green(f"✅ {len(ok)} ok")
        + "   "
        + red(f"❌ {len(failed)} failed")
        + "   "
        + yellow(f"⏭️  {len(no_zone)} no zone")
    )
    if failed:
        print(red("\n❌ Failed:"))
        for d, r in failed:
            print(red(f"   {d}: {r}"))
    if no_zone:
        print(yellow("\n⏭️  Not in Cloudflare:"))
        for d in no_zone:
            print(yellow(f"   {d}"))


if __name__ == "__main__":
    main()
