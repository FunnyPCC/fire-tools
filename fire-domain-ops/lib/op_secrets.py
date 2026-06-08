"""1Password 凭证解析(本地缓存优先)。

策略:**1P 是源,本地是缓存**。取值优先级:
  ① 环境变量  ② 本地缓存文件  ③ 1Password(取出后写回缓存)
凭证失效/过期时调 `refresh_secret()` 强制回 1P 重取并覆盖缓存 —— 避免每次都要 op 授权。

缓存文件默认 **全局** `~/.fire/secrets.json`(可用环境变量 `DOMAIN_SECRETS` 覆盖):
  - 全局而非 CWD 相对 → ① 跨项目复用,不用每个项目重新 op;② 不会落进业务 git 仓库泄密。
  - 仍**只读兼容**旧版 CWD 相对 `gsc/.secrets.json`(读得到、不再写),首次回 1P 取值时
    会顺带把旧缓存合并写进全局文件完成迁移。
**插件 repo 不含密钥**,密钥只在用户本地缓存(权限 0600)。

供插件内各脚本 import:
    import sys; from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
    from op_secrets import get_secret, refresh_secret
"""
import json
import os
import subprocess
from pathlib import Path

OP_ACCOUNT = os.environ.get("OP_ACCOUNT", "I2VNP7XCVFHWLFDDA3ZL6ZTP3I")

# 旧版 CWD 相对缓存:只读兼容,不再写入(见模块 docstring)
_LEGACY_CACHE = Path("gsc/.secrets.json")


def _cache_path() -> Path:
    """主缓存(写入目标):默认全局 ~/.fire/secrets.json,DOMAIN_SECRETS 可覆盖。"""
    env = os.environ.get("DOMAIN_SECRETS")
    return Path(env) if env else (Path.home() / ".fire" / "secrets.json")


def _read_json(p: Path) -> dict:
    if p.exists():
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _load() -> dict:
    """主缓存优先,旧 CWD 缓存只读兜底(主缓存的同名键覆盖旧缓存)。"""
    cache = dict(_read_json(_LEGACY_CACHE))
    cache.update(_read_json(_cache_path()))
    return cache


def _save(cache: dict) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _op(item: str, field: str) -> str:
    return subprocess.check_output(
        ["op", "item", "get", item, "--account", OP_ACCOUNT,
         "--fields", f"label={field}", "--reveal"], text=True).strip()


def get_secret(key: str, op_item: str = None, op_field: str = None, env: str = None) -> str:
    """环境变量 > 本地缓存 > 1Password(取出后写回缓存)。"""
    ev = env or key.upper()
    if os.environ.get(ev):
        return os.environ[ev].strip()
    cache = _load()
    if cache.get(key):
        return str(cache[key]).strip()
    if op_item and op_field:
        val = _op(op_item, op_field)
        cache[key] = val
        _save(cache)
        return val
    raise SystemExit(
        f"❌ 缺少凭证「{key}」:本地缓存({_cache_path()})无,且未指定 1Password item。\n"
        f"   请解锁 1Password 桌面端后重试,或手动写入缓存文件。")


def refresh_secret(key: str, op_item: str, op_field: str) -> str:
    """强制从 1Password 重取并覆盖缓存(用于缓存失效/过期)。"""
    val = _op(op_item, op_field)
    cache = _load()
    cache[key] = val
    _save(cache)
    return val
