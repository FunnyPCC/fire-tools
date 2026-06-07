#!/usr/bin/env bash
# 一键重建「域名 × 项目 + 风险」主表(op-free)。在【项目根】运行,数据产出到 ./域名维护/。
#   bash <plugin>/skills/domain-inventory/scripts/rebuild_domains.sh
# 步骤:自动登录(无 FIRE_TOKEN 时)→ 抓取 → Safe Browsing 风险(仅域名状态正常)→ 合并。
# 存活检测不在此处(按需用 liveness_check.py)。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ -z "${FIRE_TOKEN:-}" ]; then
  echo "① 自动登录 firepikata 换 token…"
  FIRE_TOKEN="$(uv run "$HERE/fire_login.py")"
  echo "   ✅ 已获取 token(${#FIRE_TOKEN} 位)"
fi
export FIRE_TOKEN

echo "② 抓取 firepikata 域名/项目…"
uv run "$HERE/fetch_domains.py"
echo "③ Safe Browsing 风险检测(仅域名状态=正常)…"
uv run "$HERE/risk_check.py"
echo "④ 合并生成主关联表…"
uv run "$HERE/build_doc.py"
echo "✅ 完成。产物在 ./域名维护/:域名项目关联.{md,csv}、域名风险.{md,csv}、raw_*.json"
echo "   (可访问性按需:uv run \"$HERE/liveness_check.py\" [域名...])"
