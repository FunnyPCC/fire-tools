"""
fire-bt-ops 首次安装脚本

读本机 %APPDATA%\\bt-client\\data\\data.db（堡塔多机管理客户端 SQLite），
按 Fire 项目相关分组 (公共/ptn项目/网关/东京网关) 解密生成 ~/.fire/ops/panels.yml。

依赖: pycryptodome  (pip install pycryptodome)

解密算法（来自 bt-client app.asar 的 Go 源码）：
  1. 读 config.json 的 password_hash (32 字符 hex)
  2. AES key = 取偶数位字符 (index 0,2,4,...,30) 拼成 16 字符 ASCII
  3. 字段去掉 "BTx:" 前缀 → base64 decode → AES-128-ECB decrypt → PKCS7 unpad
"""
import base64, json, os, shutil, sqlite3, sys, tempfile
from urllib.parse import urlparse

try: sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass

try:
    from Crypto.Cipher import AES
except ImportError:
    sys.stderr.write("缺少依赖: pip install pycryptodome\n")
    sys.exit(1)

KEEP_GROUPS = {'公共', 'ptn项目', '网关', '东京网关'}

DEFAULT_PROXY = 'http://127.0.0.1:7897'
DEFAULT_OPS_DIR = os.environ.get('FIRE_BT_OPS_DIR') or os.path.expanduser('~/.fire/ops')

# 已知同机别名（用户手维护，setup 不要覆盖已有 panels.yml 里的）
DEFAULT_ALIASES = [
    ['128.241.233.59', '154.81.136.4'],   # 主网关同机双 IP
]


def bt_client_data_dir():
    """bt-client SQLite 目录(跨平台)。可用 FIRE_BT_DIR 覆盖。
    Windows: %APPDATA%\\bt-client\\data
    macOS:   ~/Library/Application Support/bt-client/data
    Linux:   ~/.config/bt-client/data
    """
    override = os.environ.get('FIRE_BT_DIR')
    if override:
        candidates = [override]
    elif sys.platform == 'darwin':
        candidates = [os.path.expanduser('~/Library/Application Support/bt-client/data')]
    elif sys.platform.startswith('win'):
        appdata = os.environ.get('APPDATA')
        candidates = [os.path.join(appdata, 'bt-client', 'data')] if appdata else []
    else:
        candidates = [os.path.expanduser('~/.config/bt-client/data')]
    for path in candidates:
        if path and os.path.exists(os.path.join(path, 'data.db')):
            return path
    tried = ', '.join(candidates) or '(无候选路径)'
    sys.stderr.write(f"未找到 bt-client 数据目录(尝试: {tried})。\n"
                     f"是否安装了堡塔多机管理客户端?或用 FIRE_BT_DIR 指定路径。\n")
    sys.exit(1)


def derive_aes_key(password_hash: str) -> bytes:
    """从 password_hash 偶数位派生 16 字符 ASCII key"""
    if len(password_hash) < 32:
        raise ValueError(f"password_hash 长度异常: {len(password_hash)}")
    return ''.join(password_hash[i] for i in range(32) if i % 2 == 0).encode()


def decrypt_field(field: str, key: bytes) -> str:
    if not field or not field.startswith('BTx:'):
        return field
    raw = base64.b64decode(field[4:])
    dec = AES.new(key, AES.MODE_ECB).decrypt(raw)
    pad = dec[-1]
    if 1 <= pad <= 16:
        dec = dec[:-pad]
    return dec.decode('utf-8', errors='replace').strip()


def read_existing_aliases(yml_path: str):
    """已有 panels.yml 的 host_aliases 段是手维护的，必须保留"""
    if not os.path.exists(yml_path):
        return DEFAULT_ALIASES
    aliases = []
    in_alias = False
    with open(yml_path, encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            stripped = line.lstrip()
            if not stripped or stripped.startswith('#'):
                continue
            if line.startswith('host_aliases:'):
                in_alias = True; continue
            if in_alias:
                if stripped.startswith('- ['):
                    inner = stripped[3:]
                    if ']' in inner:
                        inner = inner[:inner.index(']')]
                    ips = [x.strip() for x in inner.split(',') if x.strip()]
                    if ips: aliases.append(ips)
                elif not stripped.startswith('-') and ':' in stripped:
                    in_alias = False  # 进入下一段
    return aliases or DEFAULT_ALIASES


def read_existing_proxy(yml_path: str) -> str:
    """保留已有 panels.yml 的 proxy 设置(用户可能改成 Surge 等),没有则用默认。"""
    if not os.path.exists(yml_path):
        return DEFAULT_PROXY
    with open(yml_path, encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if line.startswith('proxy:') and not line.startswith('#'):
                val = line.split(':', 1)[1].strip()
                if val:
                    return val
    return DEFAULT_PROXY


def main():
    bt_dir = bt_client_data_dir()
    db_path = os.path.join(bt_dir, 'data.db')
    cfg_path = os.path.join(bt_dir, 'config.json')

    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)
    ph = cfg.get('password_hash', '')
    if not ph:
        sys.stderr.write("config.json 缺少 password_hash 字段\n")
        sys.exit(1)
    key = derive_aes_key(ph)

    # 拷一份 db 避免锁
    tmp = tempfile.mktemp(suffix='.db')
    shutil.copy(db_path, tmp)
    c = sqlite3.connect(tmp)
    c.row_factory = sqlite3.Row
    groups = {r['group_id']: r['group_name'] for r in c.execute('SELECT * FROM panel_group')}

    rows = []
    for r in c.execute('SELECT * FROM panel_info WHERE auth_type=2 ORDER BY group_id, panel_id'):
        g = groups.get(r['group_id'], '?')
        if g not in KEEP_GROUPS:
            continue
        u = urlparse(r['url'])
        rows.append({
            'id': r['panel_id'], 'group': g, 'name': r['title'],
            'url': r['url'], 'host': u.hostname, 'port': u.port,
            'sk': decrypt_field(r['api_token'], key),
        })
    c.close()
    try: os.remove(tmp)
    except OSError: pass

    if not rows:
        sys.stderr.write("没有符合条件的面板（auth_type=2 且在 KEEP_GROUPS 内）\n")
        sys.exit(1)

    os.makedirs(DEFAULT_OPS_DIR, exist_ok=True)
    yml_path = os.path.join(DEFAULT_OPS_DIR, 'panels.yml')
    aliases = read_existing_aliases(yml_path)
    proxy = read_existing_proxy(yml_path)

    lines = [
        '# Fire 项目宝塔面板清单 (auto-generated from bt-client data.db)',
        '# 出口: 走本机代理(proxy 字段,可改 Surge/Clash;面板侧白名单需放行该出口 IP)',
        '# 鉴权: Baota OpenAPI request_token = md5(time + md5(sk))',
        '',
        f'proxy: {proxy}',
        '',
        '# 同一物理机的多 IP 别名（手维护，sync 不会动这里）',
        '# 用于 add-domain 去重 + 漂移检测',
        'host_aliases:',
    ]
    for grp in aliases:
        lines.append(f"  - [{', '.join(grp)}]")
    lines += ['', 'panels:']
    for p in rows:
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
    with open(yml_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))
    try: os.chmod(yml_path, 0o600)
    except OSError: pass

    # .gitignore
    gi = os.path.join(DEFAULT_OPS_DIR, '.gitignore')
    if not os.path.exists(gi):
        with open(gi, 'w', encoding='utf-8') as f:
            f.write('*\n!.gitignore\n')

    print(f"✓ 写入 {yml_path}  ({len(rows)} 面板)")
    print(f"  host_aliases: {len(aliases)} 组")
    print(f"  接下来跑: python bt.py sites  生成 sites.csv")


if __name__ == '__main__':
    main()
