"""SessionStart hook — 安静检查 bt-client SQLite vs panels.yml 差异。

策略:
- 没差异 → 不输出（不打扰）
- 有差异 → 输出一段简短摘要到 stdout（会注入 session 初始上下文）
- panels.yml 不存在 / bt-client 没装 → 不输出（首次安装前别报警）
- 任何报错 → 不输出（不能阻塞会话启动）
"""
import os, sys, subprocess

try: sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass

PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT_PY = os.path.join(PLUGIN_ROOT, 'scripts', 'bt.py')
OPS_DIR = os.environ.get('FIRE_BT_OPS_DIR') or os.path.expanduser('~/.fire/ops')
PANELS_YML = os.path.join(OPS_DIR, 'panels.yml')

# 静默退出条件
if not os.path.exists(PANELS_YML):
    sys.exit(0)
if not os.path.exists(BT_PY):
    sys.exit(0)

try:
    r = subprocess.run(
        [sys.executable, BT_PY, 'sync'],
        capture_output=True, timeout=15, text=True, encoding='utf-8', errors='replace',
    )
except Exception:
    sys.exit(0)

out = (r.stdout or '').strip()
if not out:
    sys.exit(0)
# 无变化时第一行是 "✓ panels.yml 与 bt-client SQLite 一致..." → 不打扰
if out.startswith('✓'):
    sys.exit(0)

# 有变化 → 简短输出到 stdout 让 Claude 看到
# 控制规模：最多 30 行
lines = out.splitlines()
if len(lines) > 30:
    lines = lines[:28] + [f'... (省略 {len(lines)-28} 行)', '完整 diff 见 `/fire-bt-ops:sync-panels`']
print('[fire-bt-ops] 会话开始检测到面板侧变化：')
print('\n'.join(lines))
print('')
print('用户做 add-domain 等操作前，建议先 `/fire-bt-ops:sync-panels` 同步。')
