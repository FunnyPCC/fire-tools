"""统一日志 + 实时进度工具（供 app_domains / batch-add-gsc 等长任务用）。

特性：
- 每行带时间戳，同时写日志文件与 stdout（行缓冲，立即刷新）。
- 维护一个固定软链 logs/latest.log → 最近一次运行日志，用户始终可
  `tail -F <logdir>/latest.log` 看实时进度。
- 提供 header / step(i,N) / summary 等约定输出，失败照记不静默。

用法：
    rl = RunLog("add-domains")                 # 默认 ./域名维护/logs/
    rl = RunLog("batch-add-gsc", log_dir="./gsc/logs")
    rl.header("total=3 项目=f007 IP=1.2.3.4")
    rl.step(1, 3, "a.com", "✅", t=1.2)
    rl.summary(ok=2, fail=1, failed=["c.com"])
    print(rl.tail_cmd())                        # 给用户的 tail -F 命令
    rl.close()
"""
import sys
from datetime import datetime
from pathlib import Path


class RunLog:
    def __init__(self, action, log_dir=None, log_path=None):
        if log_path:
            self.path = Path(log_path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            d = Path(log_dir or "./域名维护/logs")
            d.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.path = d / f"{action}-{ts}.log"
        self.action = action
        self._f = open(self.path, "a", buffering=1, encoding="utf-8")
        self._link_latest()

    def _link_latest(self):
        latest = self.path.parent / "latest.log"
        try:
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(self.path.name)  # 相对软链，指向同目录文件
        except OSError:
            pass  # 软链失败（如 Windows 无权限）不致命

    def _w(self, line):
        msg = f"[{datetime.now().strftime('%H:%M:%S')}] {line}"
        try:
            self._f.write(msg + "\n")
        except Exception:
            pass
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    def log(self, msg):
        self._w(msg)

    def header(self, msg):
        self._w(f"=== {msg} ===")

    def step(self, i, n, obj, result, t=None):
        tail = f" ({t:.1f}s)" if t is not None else ""
        self._w(f"[{i}/{n}] {obj} {result}{tail}")

    def summary(self, ok, fail, failed=None):
        extra = ("，失败：" + ", ".join(failed)) if failed else ""
        self._w(f"汇总：成功 {ok}，失败 {fail}{extra}")

    def tail_cmd(self):
        """返回给用户的实时查看命令（绝对路径 + 稳定 latest.log）。"""
        ap = self.path.resolve()
        # 注意：不要 resolve() latest.log，否则会跟随软链解析回时间戳文件名；
        # 解析父目录再拼接，得到稳定的 latest.log 绝对路径本身。
        latest = self.path.parent.resolve() / "latest.log"
        return f'tail -F "{latest}"   # 本次具体日志：{ap}'

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass
