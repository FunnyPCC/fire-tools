---
name: bt-panel-ops
description: Use when the user asks to add, sync, or query domains on Fire 项目 fNNN_app sites; mentions 宝塔/baota/btpanel/bt-client/堡塔; references panel IPs like 154.213.191.* or 128.241.233.*; says 加域名/漂移/同步面板/查站点. Encodes the multi-maintainer workflow (sync-first, confirm-before-apply, host_aliases dedup, code-block output).
---

# Fire 项目宝塔面板批量管理工作流

This skill encodes the operational rules for batch-managing the user's 43 Fire 项目 Baota panels through `~/.fire/ops/bt.py` (CLI tool, also in `${CLAUDE_PLUGIN_ROOT}/scripts/bt.py`). The user maintains the panels jointly with another person who also adds/removes domains, so the workflow has a strong "verify before act" bias.

Apply these rules whenever helping the user with panel domain management — they override generic shell/file instincts.

## Rule 1 (CRITICAL): Always sync-check at panel-task start

**Why:** Another maintainer modifies panels in parallel — names change, sites move, domains get added. If `~/.fire/ops/panels.yml` and `sites.csv` are stale, every match/add operation will hit wrong sites.

**How to apply:**
- Before any panel-related work (add-domain, sync-domains, drift query): run `python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py sync` once per session
- The SessionStart hook does this automatically — if you see `⚠️ 面板侧有 N 项变化` in the session boot context, immediately tell the user and ask whether to refresh
- After `sync --apply` (panels changed), also re-run `bt.py sites` to refresh sites.csv before any add-domain work
- If user says "刚导了配置" / "刚同步了" mid-session → re-sync immediately

## Rule 2 (CRITICAL): Show match list, wait for explicit confirmation

**Why:** A simple "加 X 到 065" is shorthand. It expands to multiple `AddDomain` API calls across 2-3 physical machines. Wrong target = production domain pollution.

**How to apply:** When the user says "把 [domains...] 加到 [NNN]" or similar:
1. Run `bt.py add-domain NNN <domains...>` (dry-run by default)
2. Show the user the matched sites, drift detection, and planned ADDs
3. Wait for the user to explicitly say "ok" / "加" / "确认" before passing `--apply`
4. Never assume — even if the previous turn already had `--apply`, treat the new request fresh

## Rule 3: Site matching = `fNNN_app` on 网站名 OR 根目录

**Why:** Panel names ("主网关" / "f021-f065-f066") get renamed by the other maintainer. Site names/paths are stable.

**How to apply:**
- "加到 NNN" / "查 NNN 的域名" → pattern is **`fNNN_app`** (frontend, default for domain adds)
- "加到 NNN 后台" → pattern is **`fNNN`** (no `_app` — admin project) → pass `--backend` to bt.py
- Old sites that don't follow naming: fall back to root-dir match (`/www/wwwroot/fNNN_app`)
- The bt.py matcher handles both 网站名 and 根目录 contains check automatically

## Rule 4: Dedup by physical machine (host_aliases)

**Why:** Some servers have 2 IPs pointing to the same machine (different NICs). bt-client lists them as separate panels but they share a SQLite DB — adding to one IP = adding to both. Without dedup, you'd "add" once per IP and confuse drift detection.

**How to apply:**
- `~/.fire/ops/panels.yml` has a `host_aliases:` section listing same-machine IP groups
- bt.py uses `canonical_host()` (each group's first IP) to dedup matches
- **Known aliases:** `[128.241.233.59, 154.81.136.4]` — 主网关同机双 IP
- If you observe two panels with identical uptime + memory + site list → ask the user "这两个 IP 是同一台机器吗？" and append to `host_aliases` after confirmation

## Rule 5: Drift detection on every add-domain

**Why:** Long-term parallel maintenance creates drift: domain X exists on machine A but not on B. Surface this every time so the user can decide whether to backfill.

**How to apply:**
- `bt.py add-domain` dry-run automatically diffs domain sets across all canonical machines
- Output drift summary: `⚠️ 域名漂移：X.com 在 [A] 有，[B] 无`
- Ask user: "发现 N 条漂移，要不要顺便同步？" — do NOT proactively sync without asking
- If user confirms: run `bt.py sync-domains <pattern> --apply` after the add-domain run
- The "东京镜像，暂时没用" panel `45.197.2.251` often has stale domain lists — when present in matches, default to **asking** whether to include it before adding

## Rule 6: Exclude irrelevant mirror panels with --exclude-host

**Why:** Some panels are decommissioned mirrors (e.g., `45.197.2.251` "东京镜像，暂时没用") but bt-client still lists them. Users typically don't want to push new domains there.

**How to apply:**
- When you see a panel name containing "暂时没用" / "废" / "停" / "镜像" in matches, **stop and ask** the user whether to include it
- Use `--exclude-host IP1,IP2` on add-domain and sync-domains to skip
- When excluded, drift detection automatically restricts to remaining machines

## Rule 7 (CRITICAL): Code-block output after every successful add

**Why:** The user copies this to share with downstream (DNS team / product team). It must have the IDE's "copy" button = triple-backtick code block.

**How to apply:** After `add-domain --apply` succeeds, **always** append a separate message with:

````
```
fNNN 新增域名
https://<domain1>
https://<domain2>
```
````

- Only list the **domains the user explicitly requested** — exclude drift-sync backfills (those aren't "新增" for the project, just sync)
- Domain order = user's input order, don't sort
- All prefixed with `https://`
- Triple-backtick code block (not plain text, not table, not markdown list)
- This is a separate message section, not mixed with the execution report

## Rule 8: Small-step verification only on API format changes

**Why:** First-ever `AddDomain` call needed format validation. Now we know `{id, webname, domain}` + checking `body.domains[0].status` works. No need to repeat per call.

**How to apply:** Default to direct `--apply` after dry-run confirmation. Only do small-step (1 site + 1 domain, then verify) if:
- bt-client version changed (`bt.py sync` shows panel software updates)
- AddDomain return format changes (status field moves)
- A specific panel keeps returning errors — isolate which payload field is wrong

## Reference: CLI commands

All driven by `python ${CLAUDE_PLUGIN_ROOT}/scripts/bt.py <subcommand>`:

| Subcommand | When |
|---|---|
| `sync` | Session start auto-runs; re-run after user mentions config re-import |
| `sites` | After `sync --apply` rewrote panels.yml; refreshes site catalog |
| `find-site PATTERN` | Quick offline lookup in sites.csv (no API) |
| `add-domain NNN DOMAIN...` | Default dry-run; add `--apply` after user confirm |
| `domains PATTERN` | "What domains does fNNN_app have?" — live query |
| `sync-domains PATTERN --apply` | Backfill drift, takes union of domain sets per machine |
| `ping` / `list` | Diagnostic, rarely user-initiated |
| `call ACTION` / `exec "cmd"` | Generic OpenAPI / shell, for unusual one-offs |

## Reference: Data files (not in plugin tree)

| Path | Content | Maintainer |
|---|---|---|
| `~/.fire/ops/panels.yml` | 43 panels + sk + host_aliases | `setup.py` / `sync --apply` (auto), `host_aliases` (manual) |
| `~/.fire/ops/sites.csv` | Site catalog cache | `sites` (regenerates each run) |
| `~/.fire/ops/domain_add.log` | Audit log of every applied AddDomain | bt.py append |

## Reference: Connectivity requirements

- **Clash Verge / mihomo must be running** on `127.0.0.1:7897` — panels have IP allowlists that only accept Clash node IPs. Direct connection fails with TLS handshake EOF.
- If user reports "全部 fail" / "ssl error" → first check Clash status, before debugging anything else.
- 26/43 panels currently respond to OpenAPI; 17 fail due to per-panel API IP allowlist not yet covering Clash exit IP — known state, not a bug.

## Reference: Sub-skills (slash commands)

Users can also invoke specific commands directly:
- `/fire-bt-ops:add-domain` — guided add-domain flow
- `/fire-bt-ops:sync-panels` — refresh panels.yml from bt-client
- `/fire-bt-ops:sync-domains` — fix drift on a pattern
- `/fire-bt-ops:domains` — query domain list
- `/fire-bt-ops:find-site` — search sites.csv
- `/fire-bt-ops:list-sites` — regenerate sites.csv
- `/fire-bt-ops:setup` — first-time install (decrypt bt-client SQLite)
