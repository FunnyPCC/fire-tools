"""
Fire 项目宝塔面板批量管理 CLI

子命令:
  list                       列出 panels.yml 里的面板
  ping                       调 GetSystemTotal 探活
  sites                      拉所有面板的站点 → sites.csv（不含域名列表，速度优先）
  domains PATTERN            按需查某个 fNNN_app 站点的全部域名（主+附加）
  call ACTION                通用 OpenAPI 调用
  exec "<cmd>"               在面板宿主机执行 shell
  find-site PATTERN          在 sites.csv 里找匹配的站点（网站名 OR 根目录）
  add-domain NNN DOMAIN...   把域名加到 fNNN_app 站点（默认 dry-run，加 --apply 才生效）
  sync                       diff bt-client SQLite vs panels.yml（每个会话开始跑）

通用选项 (放在子命令前):
  --filter REGEX             名字或主机匹配
  -g GROUP                   按分组过滤
  -c N                       并发数 (默认 8)

示例:
  python bt.py ping --filter f068
  python bt.py find-site f065_app
  python bt.py domains f065_app                              # 看 f065_app 现有所有域名
  python bt.py add-domain 065 web3-x.com web3-y.com           # dry-run 看匹配 + 自动 dedup
  python bt.py add-domain 065 web3-x.com web3-y.com --apply   # 真加
  python bt.py sync
"""
import argparse, base64, hashlib, json, os, re, sys, ssl, time
import urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from Crypto.Cipher import AES  # bt-client SQLite 解密用 (sync 命令需要)

sys.stdout.reconfigure(encoding='utf-8')

# 数据目录: 默认 ~/.fire/ops/，可用 FIRE_BT_OPS_DIR 覆盖
# 这样 bt.py 可以从插件 scripts/ 跑，但 panels.yml/sites.csv/log 不在插件里
OPS_DIR = os.environ.get('FIRE_BT_OPS_DIR') or os.path.expanduser('~/.fire/ops')
os.makedirs(OPS_DIR, exist_ok=True)
PANELS_YML = os.path.join(OPS_DIR, 'panels.yml')

# ------- minimal YAML loader (avoid PyYAML dep) -------
def load_panels():
    panels = []; meta = {'host_aliases': []}
    cur = None; section = None
    with open(PANELS_YML, encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            if not line.strip() or line.lstrip().startswith('#'): continue
            if line.startswith('proxy:'):
                meta['proxy'] = line.split(':', 1)[1].strip(); section = None
            elif line.startswith('host_aliases:'):
                section = 'aliases'
            elif line.startswith('panels:'):
                section = 'panels'
            elif section == 'aliases' and line.lstrip().startswith('- ['):
                inner = line.lstrip()[3:]  # 去掉 "- ["
                if ']' in inner:
                    inner = inner[:inner.index(']')]
                ips = [x.strip() for x in inner.split(',') if x.strip()]
                if ips: meta['host_aliases'].append(ips)
            elif section == 'panels' and line.startswith('  - id:'):
                if cur: panels.append(cur)
                cur = {'id': int(line.split(':', 1)[1].strip())}
            elif section == 'panels' and line.startswith('    ') and ':' in line:
                k, v = line.lstrip().split(':', 1)
                v = v.strip().strip('"')
                if k == 'port': v = int(v)
                cur[k] = v
    if cur: panels.append(cur)
    # build canonical host map
    canon = {}
    for grp in meta['host_aliases']:
        rep = grp[0]
        for h in grp: canon[h] = rep
    meta['_canonical'] = canon
    return meta, panels

def canonical_host(host, meta):
    return meta.get('_canonical', {}).get(host, host)

def make_opener(proxy):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    return urllib.request.build_opener(*handlers)

def sign(sk):
    t = int(time.time())
    tok = hashlib.md5(f"{t}{hashlib.md5(sk.encode()).hexdigest()}".encode()).hexdigest()
    return {'request_time': t, 'request_token': tok}

def call_api(opener, panel, action_path, extra=None, timeout=10):
    """action_path 形如 '/system?action=GetSystemTotal' 或 '/files?action=GetDir'"""
    payload = sign(panel['sk'])
    if extra: payload.update(extra)
    url = panel['url'] + action_path
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', errors='replace')
            try: return resp.status, json.loads(body)
            except: return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')[:300]
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"

# ------- filtering -------
def filter_panels(panels, name_re=None, group=None):
    out = panels
    if group:
        out = [p for p in out if p.get('group') == group]
    if name_re:
        rx = re.compile(name_re, re.I)
        out = [p for p in out if rx.search(p.get('name', '')) or rx.search(p.get('host', ''))]
    return out

# ------- commands -------
def cmd_list(args, meta, panels):
    sel = filter_panels(panels, args.filter, args.group)
    print(f"{'ID':>3}  {'GROUP':<8}  {'NAME':<32}  {'HOST:PORT'}")
    print('-' * 80)
    for p in sel:
        print(f"{p['id']:>3}  {p['group']:<8}  {p['name'][:32]:<32}  {p['host']}:{p['port']}")
    print(f"\nTotal: {len(sel)} / {len(panels)}")

def cmd_ping(args, meta, panels):
    sel = filter_panels(panels, args.filter, args.group)
    opener = make_opener(meta.get('proxy'))
    results = []
    def task(p):
        code, body = call_api(opener, p, '/system?action=GetSystemTotal', timeout=8)
        return p, code, body
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for fut in as_completed([ex.submit(task, p) for p in sel]):
            p, code, body = fut.result()
            if code == 200 and isinstance(body, dict):
                mem = body.get('memNewRealUsed', '?')
                memT = body.get('memNewTotal', '?')
                cpu = body.get('cpuRealUsed', '?')
                sysn = body.get('system', '?')[:40]
                up = body.get('time', '?')
                results.append((p, 'OK', f"cpu={cpu}% mem={mem}/{memT} up={up} {sysn}"))
            else:
                results.append((p, f'FAIL({code})', str(body)[:80]))
    results.sort(key=lambda x: (x[1] != 'OK', x[0]['id']))
    for p, st, info in results:
        mark = '✓' if st == 'OK' else '✗'
        print(f"{mark} [{p['id']:>3}] {p['name'][:28]:<28} {p['host']:<18}  {st:<10}  {info}")
    ok = sum(1 for _, s, _ in results if s == 'OK')
    print(f"\nOK: {ok}/{len(results)}")

def cmd_call(args, meta, panels):
    sel = filter_panels(panels, args.filter, args.group)
    opener = make_opener(meta.get('proxy'))
    extra = {}
    for kv in args.data or []:
        k, v = kv.split('=', 1)
        extra[k] = v
    def task(p):
        code, body = call_api(opener, p, args.action, extra)
        return p, code, body
    out = {}
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for fut in as_completed([ex.submit(task, p) for p in sel]):
            p, code, body = fut.result()
            out[p['name']] = {'id': p['id'], 'host': p['host'], 'code': code, 'body': body}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for name, r in sorted(out.items(), key=lambda kv: kv[1]['id']):
            print(f"\n=== [{r['id']}] {name}  ({r['host']})  code={r['code']} ===")
            b = r['body']
            print(json.dumps(b, ensure_ascii=False, indent=2)[:600] if isinstance(b, (dict, list)) else str(b)[:600])

def get_domains(opener, panel, site_id, timeout=8):
    """拉单个站点的附加域名列表 (不含主域名). 返回 [{name, port}] 或 []"""
    code, body = call_api(opener, panel, '/data?action=getData&table=domain',
                          {'search': str(site_id), 'list': 'True'}, timeout=timeout)
    if code == 200 and isinstance(body, list):
        return [{'name': d.get('name'), 'port': d.get('port', 80)} for d in body]
    return []

def cmd_sites(args, meta, panels):
    sel = filter_panels(panels, args.filter, args.group)
    opener = make_opener(meta.get('proxy'))
    extra = {'p': '1', 'limit': '500', 'type': '-1', 'search': '', 'tojs': ''}
    rows = []
    no_data = []
    def task(p):
        code, body = call_api(opener, p, '/data?action=getData&table=sites', dict(extra), timeout=10)
        return p, code, body
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for fut in as_completed([ex.submit(task, p) for p in sel]):
            p, code, body = fut.result()
            if code == 200 and isinstance(body, dict) and isinstance(body.get('data'), list):
                for s in body['data']:
                    st = s.get('status', '?')
                    st_label = '运行' if st == '1' else ('停止' if st == '0' else st)
                    rows.append({
                        'panel_id': p['id'], 'panel': p['name'], 'host': p['host'],
                        'site': s.get('name', ''), 'path': s.get('path', ''),
                        'status': st_label, 'status_raw': st,
                        'type': s.get('project_type', ''),
                        'php': s.get('php_version', ''),
                        'ps': (s.get('ps') or '').strip(),
                    })
            else:
                no_data.append((p, code, str(body)[:60]))
    rows.sort(key=lambda r: (r['panel_id'], r['site']))
    if args.json:
        print(json.dumps({'sites': rows, 'unreachable': [(p['name'], p['host'], c) for p,c,_ in no_data]}, ensure_ascii=False, indent=2))
        return
    csv_path = os.path.join(OPS_DIR, 'sites.csv')
    cols_zh = ['服务器名称','IP','网站名','状态','根目录','类型','PHP','备注']
    keys = ['panel','host','site','status','path','type','php','ps']
    def q(v):
        v = str(v or '')
        return '"' + v.replace('"', '""') + '"' if any(c in v for c in ',\n"') else v
    with open(csv_path, 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write(','.join(cols_zh) + '\n')
        for r in rows:
            f.write(','.join(q(r[k]) for k in keys) + '\n')
    # unreachable list to second file
    unreach_path = os.path.join(OPS_DIR, 'sites_unreachable.csv')
    with open(unreach_path, 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write('服务器名称,IP,错误码,错误\n')
        for p, c, b in sorted(no_data, key=lambda x: x[0]['id']):
            f.write(','.join(q(x) for x in [p['name'], p['host'], c, b]) + '\n')
    print(f"Wrote {csv_path}  ({len(rows)} 站点 / {len(set(r['panel_id'] for r in rows))} 台面板)")
    print(f"Wrote {unreach_path}  ({len(no_data)} 台不可达)")
    # terminal summary by panel
    print("\n按面板汇总：")
    from collections import Counter
    by_panel = {}
    for r in rows:
        by_panel.setdefault((r['panel_id'], r['panel'], r['host']), []).append(r)
    for (pid, name, host), sites in sorted(by_panel.items()):
        run = sum(1 for s in sites if s['status'] == '运行')
        stop = len(sites) - run
        st_lbl = f"{run}运行" + (f"+{stop}停" if stop else "")
        print(f"  [{pid:>3}] {name[:24]:<24} {host:<18}  {len(sites):>3}站  ({st_lbl})")
    if no_data:
        print(f"\nAPI 不可达 {len(no_data)} 台 (面板侧 IP 白名单或 token 失效)：")
        for p, c, b in sorted(no_data, key=lambda x: x[0]['id']):
            print(f"  [{p['id']:>3}] {p['name'][:24]:<24} {p['host']}")

# ------- sites.csv helpers -------
def load_sites_csv():
    """读 sites.csv → list[dict]。csv 列: 服务器名称,IP,网站名,状态,根目录,类型,PHP,备注"""
    csv_path = os.path.join(OPS_DIR, 'sites.csv')
    if not os.path.exists(csv_path):
        return None
    import csv
    with open(csv_path, encoding='utf-8-sig') as f:
        rd = csv.DictReader(f)
        return list(rd)

def site_main_name(s):
    """兼容新旧 csv 列名: 优先 '网站名(主)'，回退 '网站名'"""
    return s.get('网站名(主)') or s.get('网站名') or ''

def match_sites(pattern, sites):
    """按 网站名 OR 根目录 contains pattern 匹配。"""
    if not sites: return []
    p = pattern.lower()
    out = []
    for s in sites:
        name = site_main_name(s).lower()
        path = (s.get('根目录') or '').lower()
        if p in name or p in path:
            out.append(s)
    return out

def site_id_for(opener, panel, site_name):
    """通过 GetSiteId 拿到指定站点的内部 id（AddDomain 需要）"""
    code, body = call_api(opener, panel, '/data?action=getData&table=sites',
                          {'p':'1','limit':'500','type':'-1','search':site_name,'tojs':''})
    if code != 200 or not isinstance(body, dict): return None, code, body
    for s in (body.get('data') or []):
        if s.get('name') == site_name:
            return s.get('id'), 200, s
    return None, 200, f"not found: {site_name}"

def cmd_find_site(args, meta, panels):
    sites = load_sites_csv()
    if sites is None:
        print("sites.csv 不存在，先跑 `python bt.py sites`"); return
    hits = match_sites(args.pattern, sites)
    if not hits:
        print(f"无匹配: {args.pattern}"); return
    print(f"匹配 {len(hits)} 个站点 (pattern={args.pattern}):")
    for s in hits:
        st = s.get('状态', '?')
        print(f"  [{s.get('IP'):<16}] {site_main_name(s):<38} → {s.get('根目录'):<28} ({st})  面板:{s.get('服务器名称','')}")

def cmd_domains(args, meta, panels):
    """按需查某个 pattern 对应站点的全部域名（主+附加），跨匹配到的所有面板"""
    sites = load_sites_csv()
    if sites is None:
        print("sites.csv 不存在，先跑 `python bt.py sites`"); return
    hits = match_sites(args.pattern, sites)
    if not hits:
        print(f"无匹配: {args.pattern}"); return
    opener = make_opener(meta.get('proxy'))
    by_host = {p['host']: p for p in panels}
    out = []
    for s in hits:
        host = s.get('IP'); site_name = site_main_name(s)
        panel = by_host.get(host)
        if not panel:
            out.append({'host': host, 'site': site_name, 'error': 'panel not in panels.yml'}); continue
        sid, c, info = site_id_for(opener, panel, site_name)
        if not sid:
            out.append({'host': host, 'site': site_name, 'error': f'site_id lookup {c}: {info}'}); continue
        bound = get_domains(opener, panel, sid)
        out.append({
            'host': host, 'site': site_name, 'site_id': sid,
            'primary': site_name,
            'bound': [f"{d['name']}:{d['port']}" if d['port']!=80 else d['name'] for d in bound],
            'panel': s.get('服务器名称'),
        })
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2)); return
    for r in out:
        print(f"\n[{r['host']:<16}] {r['site']}  (面板: {r.get('panel','?')})")
        if 'error' in r:
            print(f"  ✗ {r['error']}"); continue
        print(f"  * {r['primary']}  (主域名)")
        for d in r['bound']:
            print(f"  + {d}")
        print(f"  共 {1 + len(r['bound'])} 个域名")

def _add_domain_result(code, body):
    """解析 /site?action=AddDomain 的响应。返回 (ok, msg)。
    格式: {domains: [{name, status: bool, msg}]} 或 {status: bool, msg}
    """
    if code != 200: return False, f"HTTP {code}"
    if isinstance(body, dict):
        if isinstance(body.get('domains'), list) and body['domains']:
            r = body['domains'][0]
            return bool(r.get('status')), str(r.get('msg', ''))
        if 'status' in body:
            return bool(body['status']), str(body.get('msg', ''))
    return False, str(body)[:120]

def _collect_sites_for_pattern(opener, meta, panels, pattern, exclude_hosts=None):
    """匹配 → 按 canonical_host 去重 → 拉当前域名。返回 [{canonical_host, hosts:[IP...], panel, site_id, site_name, path, status, domains:set}]"""
    sites = load_sites_csv()
    hits = match_sites(pattern, sites) if sites is not None else []
    by_host = {p['host']: p for p in panels}
    excluded = set(exclude_hosts or [])
    # 把 exclude_hosts 里如果是别名 IP，也加上其 canonical 等效
    excluded |= {canonical_host(h, meta) for h in excluded}
    # 按 canonical host 聚合
    grouped = {}  # canonical_host -> {hosts:[], site rows, panel}
    for s in hits:
        host = s.get('IP'); ch = canonical_host(host, meta)
        if ch in excluded or host in excluded: continue
        site_name = site_main_name(s)
        key = (ch, site_name)  # 同机同站合并
        g = grouped.setdefault(key, {'canonical_host': ch, 'hosts': [], 'site_name': site_name, 'path': s.get('根目录'), 'status': s.get('状态'), 'panel_label': s.get('服务器名称')})
        if host not in g['hosts']: g['hosts'].append(host)
    # 对每个 group 查一次域名（用 canonical host 对应的 panel）
    results = []
    for (ch, sname), g in grouped.items():
        panel = by_host.get(ch)
        if not panel:
            g['error'] = 'no panel in panels.yml'; g['domains'] = set(); g['site_id'] = None
            results.append(g); continue
        sid, c, info = site_id_for(opener, panel, sname)
        if not sid:
            g['error'] = f'site_id lookup {c}: {str(info)[:60]}'; g['domains'] = set(); g['site_id'] = None
            results.append(g); continue
        bound = get_domains(opener, panel, sid)
        g['site_id'] = sid; g['panel'] = panel
        g['domains'] = {sname} | {d['name'] for d in bound}
        results.append(g)
    return hits, results

def cmd_add_domain(args, meta, panels):
    """加域名到 fNNN_app 站点。默认 dry-run；--apply 才调 API。"""
    if args.branch.startswith('f'):
        pattern = args.branch
    else:
        pattern = f"f{args.branch}_app" if not args.backend else f"f{args.branch}"
    opener = make_opener(meta.get('proxy'))
    excl = args.exclude_host.split(',') if args.exclude_host else None
    hits_raw, groups = _collect_sites_for_pattern(opener, meta, panels, pattern, exclude_hosts=excl)
    print(f"=== 站点匹配 (pattern={pattern}) ===")
    if not groups:
        print("无匹配。检查分支号是否正确，或试试 --backend。"); return
    print(f"原始匹配 {len(hits_raw)} 条 → 按物理机去重后 {len(groups)} 组：\n")
    for i, g in enumerate(groups, 1):
        hosts = '/'.join(g['hosts'])
        alias_note = f" (同机 {len(g['hosts'])} IP)" if len(g['hosts']) > 1 else ''
        print(f"  [{i}] {hosts:<35} {g['panel_label']}{alias_note}")
        if g.get('error'):
            print(f"      ✗ {g['error']}"); continue
        print(f"      站点: {g['site_name']} → {g['path']} (id={g['site_id']}, 状态={g['status']}, 现有{len(g['domains'])}域名)")

    # 漂移检测
    valid = [g for g in groups if not g.get('error')]
    drift_lines = []
    if len(valid) >= 2:
        union = set().union(*(g['domains'] for g in valid))
        for d in sorted(union):
            present = [g['canonical_host'] for g in valid if d in g['domains']]
            absent = [g['canonical_host'] for g in valid if d not in g['domains']]
            if absent:
                drift_lines.append(f"  - {d}  在 [{','.join(present)}] 有，[{','.join(absent)}] 无")
    if drift_lines:
        print(f"\n⚠️  域名漂移 ({len(drift_lines)} 条)：各物理机域名列表不一致")
        for l in drift_lines: print(l)
        print(f"  → 想同步补齐用: `python bt.py sync-domains {pattern} --apply`")

    # 计划
    print(f"\n=== 待加域名 ({len(args.domains)}) ===")
    plan = []  # (g, domain, status)
    for g in groups:
        for d in args.domains:
            dn = d.strip()
            if g.get('error') or not g.get('site_id'):
                plan.append((g, dn, 'SKIP(无可用 panel)'))
            elif dn in g['domains']:
                plan.append((g, dn, 'SKIP(已存在)'))
            else:
                plan.append((g, dn, 'ADD'))
    by_d = {}
    for g, d, st in plan:
        by_d.setdefault(d, []).append((g['canonical_host'], st))
    for d, lst in by_d.items():
        print(f"  + {d}")
        for h, st in lst: print(f"      {h}: {st}")

    add_count = sum(1 for _,_,st in plan if st == 'ADD')
    skip_count = len(plan) - add_count
    print(f"\n汇总: 新加 {add_count} | 跳过 {skip_count} (含 {len(valid)} 个物理机)")
    if drift_lines:
        print(f"⚠️  {len(drift_lines)} 条域名漂移待用户决定是否同步")
    if not args.apply:
        print("[DRY-RUN] 加 --apply 真执行（漂移补齐请单独跑 sync-domains）")
        return
    if add_count == 0:
        print("无可加项，退出。"); return
    log_path = os.path.join(OPS_DIR, 'domain_add.log')
    import datetime
    for g, d, st in plan:
        if st != 'ADD': continue
        host = g['canonical_host']; panel = g['panel']; sid = g['site_id']; sname = g['site_name']
        payload = {'id': str(sid), 'webname': sname, 'domain': d}
        c2, b2 = call_api(opener, panel, '/site?action=AddDomain', payload)
        ok, msg = _add_domain_result(c2, b2)
        mark = '✓' if ok else '✗'
        print(f"  {mark} {host} {sname} ← {d}  | {msg}")
        with open(log_path, 'a', encoding='utf-8') as lf:
            lf.write(f"{datetime.datetime.now().isoformat()} {'OK' if ok else 'FAIL'} {host} {sname} domain={d} msg={msg}\n")

def cmd_sync_domains(args, meta, panels):
    """把匹配站点的域名做并集，补齐每个物理机缺的域名。需 --apply。"""
    opener = make_opener(meta.get('proxy'))
    if args.pattern.startswith('f'):
        pattern = args.pattern
    else:
        pattern = f"f{args.pattern}_app"
    excl = args.exclude_host.split(',') if args.exclude_host else None
    _, groups = _collect_sites_for_pattern(opener, meta, panels, pattern, exclude_hosts=excl)
    valid = [g for g in groups if not g.get('error')]
    if len(valid) < 2:
        print(f"匹配 {len(valid)} 组（去重后），无需同步"); return
    union = set().union(*(g['domains'] for g in valid))
    plan = []  # (group, domain)
    for g in valid:
        for d in union - g['domains']:
            plan.append((g, d))
    if not plan:
        print(f"✓ {len(valid)} 组域名已一致 ({len(union)} 个域名)，无需同步"); return
    print(f"=== 漂移同步计划 (pattern={pattern}) ===")
    print(f"并集 {len(union)} 个域名 / 涉及 {len(valid)} 个物理机\n")
    by_g = {}
    for g, d in plan: by_g.setdefault(g['canonical_host'], []).append(d)
    for h, ds in by_g.items():
        print(f"  → {h} 补 {len(ds)} 个：")
        for d in ds: print(f"      + {d}")
    if not args.apply:
        print(f"\n[DRY-RUN] 加 --apply 真同步")
        return
    log_path = os.path.join(OPS_DIR, 'domain_add.log')
    import datetime
    for g, d in plan:
        host = g['canonical_host']; panel = g['panel']; sid = g['site_id']; sname = g['site_name']
        payload = {'id': str(sid), 'webname': sname, 'domain': d}
        c2, b2 = call_api(opener, panel, '/site?action=AddDomain', payload)
        ok, msg = _add_domain_result(c2, b2)
        mark = '✓' if ok else '✗'
        print(f"  {mark} {host} {sname} ← {d}  | {msg}")
        with open(log_path, 'a', encoding='utf-8') as lf:
            lf.write(f"{datetime.datetime.now().isoformat()} {'SYNC-OK' if ok else 'SYNC-FAIL'} {host} {sname} domain={d} msg={msg}\n")

def cmd_sync(args, meta, panels):
    """diff bt-client SQLite vs panels.yml"""
    import shutil, tempfile
    src_db = os.path.expanduser('~/AppData/Roaming/bt-client/data/data.db')
    cfg_path = os.path.expanduser('~/AppData/Roaming/bt-client/data/config.json')
    if not os.path.exists(src_db):
        print(f"找不到 bt-client 数据库: {src_db}"); return
    # copy to avoid lock
    tmp = tempfile.mktemp(suffix='.db')
    shutil.copy(src_db, tmp)
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)
    ph = cfg.get('password_hash', '')
    key = "".join(ph[i] for i in range(32) if i % 2 == 0).encode()
    def dec(field):
        if not field or not field.startswith('BTx:'): return field
        raw = base64.b64decode(field[4:])
        d = AES.new(key, AES.MODE_ECB).decrypt(raw)
        p = d[-1]
        if 1 <= p <= 16: d = d[:-p]
        return d.decode('utf-8', errors='replace').strip()
    import sqlite3
    c = sqlite3.connect(tmp); c.row_factory = sqlite3.Row
    grp = {r['group_id']: r['group_name'] for r in c.execute('SELECT * FROM panel_group')}
    KEEP = {'公共','ptn项目','网关','东京网关'}
    db_panels = {}
    for r in c.execute('SELECT * FROM panel_info WHERE auth_type=2'):
        g = grp.get(r['group_id'], '?')
        if g not in KEEP: continue
        from urllib.parse import urlparse
        u = urlparse(r['url'])
        db_panels[r['panel_id']] = {
            'id': r['panel_id'], 'group': g, 'name': r['title'],
            'url': r['url'], 'host': u.hostname, 'port': u.port,
            'sk': dec(r['api_token']),
        }
    c.close()
    try: os.remove(tmp)
    except: pass
    # diff vs panels.yml
    yml_by_id = {p['id']: p for p in panels}
    db_ids = set(db_panels)
    yml_ids = set(yml_by_id)
    added = db_ids - yml_ids
    removed = yml_ids - db_ids
    renamed, host_changed, token_rotated, group_changed = [], [], [], []
    for pid in db_ids & yml_ids:
        a, b = db_panels[pid], yml_by_id[pid]
        if a['name'] != b['name']: renamed.append((pid, b['name'], a['name']))
        if a['host'] != b['host'] or a['port'] != int(b['port']): host_changed.append((pid, f"{b['host']}:{b['port']}", f"{a['host']}:{a['port']}"))
        if a['sk'] != b.get('sk'): token_rotated.append((pid, a['name']))
        if a['group'] != b.get('group'): group_changed.append((pid, b.get('group'), a['group']))
    total = len(added) + len(removed) + len(renamed) + len(host_changed) + len(token_rotated) + len(group_changed)
    if total == 0:
        print("✓ panels.yml 与 bt-client SQLite 一致，无变化。")
        return
    print(f"⚠️  面板侧有 {total} 项变化:\n")
    if added:
        print(f"  新增 {len(added)}:")
        for pid in sorted(added):
            p = db_panels[pid]; print(f"    + [{pid:>3}] {p['group']:<8} {p['name']:<28} {p['host']}:{p['port']}")
    if removed:
        print(f"  删除 {len(removed)}:")
        for pid in sorted(removed):
            p = yml_by_id[pid]; print(f"    - [{pid:>3}] {p.get('group',''):<8} {p['name']:<28} {p['host']}:{p['port']}")
    if renamed:
        print(f"  改名 {len(renamed)}:")
        for pid, old, new in renamed: print(f"    [{pid:>3}] '{old}' → '{new}'")
    if host_changed:
        print(f"  换 IP/端口 {len(host_changed)}:")
        for pid, old, new in host_changed: print(f"    [{pid:>3}] {old} → {new}")
    if group_changed:
        print(f"  换组 {len(group_changed)}:")
        for pid, old, new in group_changed: print(f"    [{pid:>3}] {old} → {new}")
    if token_rotated:
        print(f"  Token 轮换 {len(token_rotated)}:")
        for pid, name in token_rotated: print(f"    [{pid:>3}] {name}")
    print(f"\n要刷新 panels.yml 和重跑 sites.csv 跑: `python bt.py sync --apply`")
    if args.apply:
        # 直接 regenerate panels.yml (复用最早的生成逻辑)
        print("\n[--apply] 重写 panels.yml...")
        lines = [
            '# Fire 项目宝塔面板清单 (auto-generated from bt-client data.db)',
            '# 出口: 走 Clash 代理 127.0.0.1:7897',
            '# 鉴权: Baota OpenAPI request_token = md5(time + md5(sk))',
            '',
            f'proxy: {meta.get("proxy", "http://127.0.0.1:7897")}',
            '',
        ]
        # 保留手维护的 host_aliases
        aliases = meta.get('host_aliases', [])
        if aliases:
            lines += [
                '# 同一物理机的多 IP 别名（手维护，sync 不会动这里）',
                '# 用于 add-domain 去重 + 漂移检测',
                'host_aliases:',
            ]
            for grp in aliases:
                lines.append(f"  - [{', '.join(grp)}]")
            lines.append('')
        lines.append('panels:')
        for pid in sorted(db_panels):
            p = db_panels[pid]
            title = p['name'].replace('"', '\\"')
            lines += [
                f'  - id: {p["id"]}',
                f'    group: {p["group"]}',
                f'    name: "{title}"',
                f'    url: {p["url"]}',
                f'    host: {p["host"]}',
                f'    port: {p["port"]}',
                f'    sk: {p["sk"]}',
                '',
            ]
        with open(PANELS_YML, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines))
        try: os.chmod(PANELS_YML, 0o600)
        except: pass
        print(f"  ✓ panels.yml 重写完成 ({len(db_panels)} 台)")
        print(f"  → 建议接着跑 `python bt.py sites` 刷新 sites.csv")

def cmd_exec(args, meta, panels):
    sel = filter_panels(panels, args.filter, args.group)
    opener = make_opener(meta.get('proxy'))
    def task(p):
        code, body = call_api(opener, p, '/system?action=RunShell', {'shell': args.cmd})
        return p, code, body
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for fut in as_completed([ex.submit(task, p) for p in sel]):
            p, code, body = fut.result()
            print(f"\n=== [{p['id']}] {p['name']}  ({p['host']})  code={code} ===")
            if isinstance(body, dict):
                # bt RunShell returns {status: bool, msg: str} or similar
                print(json.dumps(body, ensure_ascii=False, indent=2)[:800])
            else:
                print(str(body)[:800])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-c', '--concurrency', type=int, default=8)
    ap.add_argument('--filter', help='regex on name or host')
    ap.add_argument('-g', '--group', help='exact group name (公共/ptn项目/网关/东京网关)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    sub.add_parser('ping')
    c = sub.add_parser('call'); c.add_argument('action'); c.add_argument('-d', '--data', action='append'); c.add_argument('--json', action='store_true')
    e = sub.add_parser('exec'); e.add_argument('cmd')
    s = sub.add_parser('sites'); s.add_argument('--json', action='store_true')
    dn = sub.add_parser('domains'); dn.add_argument('pattern'); dn.add_argument('--json', action='store_true')
    fs = sub.add_parser('find-site'); fs.add_argument('pattern')
    ad = sub.add_parser('add-domain'); ad.add_argument('branch'); ad.add_argument('domains', nargs='+')
    ad.add_argument('--apply', action='store_true', help='真执行（默认 dry-run）')
    ad.add_argument('--backend', action='store_true', help='匹配 fNNN 后台（默认匹配 fNNN_app 前台）')
    ad.add_argument('--exclude-host', help='跳过指定 IP（逗号分隔，例: 45.197.2.251）')
    sy = sub.add_parser('sync'); sy.add_argument('--apply', action='store_true', help='重写 panels.yml')
    sd = sub.add_parser('sync-domains'); sd.add_argument('pattern'); sd.add_argument('--apply', action='store_true', help='补齐多面板域名漂移')
    sd.add_argument('--exclude-host', help='跳过指定 IP（逗号分隔）')
    args = ap.parse_args()

    meta, panels = load_panels()
    {'list': cmd_list, 'ping': cmd_ping, 'call': cmd_call, 'exec': cmd_exec,
     'sites': cmd_sites, 'find-site': cmd_find_site, 'add-domain': cmd_add_domain,
     'sync': cmd_sync, 'domains': cmd_domains, 'sync-domains': cmd_sync_domains}[args.cmd](args, meta, panels)

if __name__ == '__main__':
    main()
