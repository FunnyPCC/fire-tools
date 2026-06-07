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

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Configuration — EDIT THESE ────────────────────────
SCRIPT_DIR = Path(__file__).parent
OAUTH_JSON = SCRIPT_DIR / "OAuth.json"         # Path to Google OAuth client credentials JSON
DOMAIN_FILE = SCRIPT_DIR / "domains.txt"        # One domain per line
TOKEN_FILE = SCRIPT_DIR / ".gsc_token.json"     # Cached OAuth token (auto-generated)
DNS_WAIT_SECONDS = 10                           # Seconds to wait for DNS propagation
OAUTH_PORT = 8099                               # Local port for OAuth redirect

# Google OAuth — Method B: direct Client ID + Secret (if no JSON file)
OAUTH_CLIENT_ID = ""       # e.g. "123456-xxx.apps.googleusercontent.com"
OAUTH_CLIENT_SECRET = ""   # e.g. "GOCSPX-xxx"

# Google OAuth — Method C: 1Password (store refresh_token + client credentials)
# Item fields: "client_id", "client_secret", "refresh_token"
OP_GOOGLE_ITEM = ""        # 1Password item UUID or name for Google OAuth

# Cloudflare credentials — choose ONE method in get_cf_credentials() below
# Method A: 1Password op CLI (recommended)
OP_ACCOUNT = ""    # 1Password account ID
OP_ITEM = ""       # Item UUID or name (Cloudflare)
OP_VAULT = ""      # Vault name

# Method B: Environment variables
# Set CF_EMAIL and CF_API_KEY before running

# Method C: Direct (not recommended for shared scripts)
# CF_EMAIL_DIRECT = ""
# CF_API_KEY_DIRECT = ""
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


def main():
    force_reauth = "--reauth" in sys.argv

    # Validate config
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
    if not DOMAIN_FILE.exists():
        print(f"ERROR: Domain file not found: {DOMAIN_FILE}")
        sys.exit(1)

    seen, domains = set(), []
    for line in DOMAIN_FILE.read_text().splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        d = normalize_domain(line)
        if d and d not in seen:
            seen.add(d)
            domains.append(d)
    if not domains:
        print("ERROR: Domain file is empty")
        sys.exit(1)

    print(bold("=== Batch Add to Google Search Console ==="))
    print(dim(f"    {len(domains)} domains to process\n"))

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
