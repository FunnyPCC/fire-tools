# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Fire 项目 Jenkins 构建 CLI —— 给 deploy-project skill 用

凭据从 ~/.fire/jenkins.json 读（可用 FIRE_JENKINS_CONF 覆盖路径），**不进插件仓库**：
  {"url": "http://<jenkins-host>:9090", "user": "pt", "token": "<API token>"}

子命令:
  build <job> [--wait] [--timeout 1200] [--poll 10]   触发构建; --wait 轮询到结束
                                                       (退出码: 0=SUCCESS, 2=非SUCCESS, 1=错误/超时)
  status <job>                                          最近一次构建 building/result/number
  console <job> [build_no]                              拉 console 文本(排障; 默认 lastBuild)

示例:
  uv run jenkins.py build fire_h5_f076 --wait
  uv run jenkins.py build fire_admin_web --wait
  uv run jenkins.py status fire_h5_f076
  uv run jenkins.py console fire_h5_f076
"""
import argparse, base64, json, os, sys, time
import urllib.request, urllib.parse, urllib.error

sys.stdout.reconfigure(encoding='utf-8')

CONF = os.environ.get('FIRE_JENKINS_CONF') or os.path.expanduser('~/.fire/jenkins.json')


def load_conf():
    if not os.path.exists(CONF):
        sys.exit(
            f"缺少 Jenkins 配置: {CONF}\n"
            f'  建立它(chmod 600): {{"url":"http://<jenkins-host>:9090","user":"pt","token":"<API token>"}}'
        )
    with open(CONF, encoding='utf-8') as f:
        c = json.load(f)
    for k in ('url', 'user', 'token'):
        if not c.get(k):
            sys.exit(f"{CONF} 缺字段: {k}")
    c['url'] = c['url'].rstrip('/')
    return c


def api(conf, path, method='GET', expect_json=True, timeout=30):
    """返回 (http_code, body, headers)。body 是 dict(JSON) 或 str。"""
    req = urllib.request.Request(conf['url'] + path, method=method)
    auth = base64.b64encode(f"{conf['user']}:{conf['token']}".encode()).decode()
    req.add_header('Authorization', 'Basic ' + auth)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', 'replace')
            code, hdrs = resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')[:500], {}
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}", {}
    if expect_json and body.strip():
        try:
            return code, json.loads(body), hdrs
        except ValueError:
            return code, body, hdrs
    return code, body, hdrs


def last_build(conf, job):
    code, b, _ = api(
        conf,
        f"/job/{urllib.parse.quote(job)}/lastBuild/api/json?tree=building,result,number,duration",
    )
    return b if code == 200 and isinstance(b, dict) else None


def cmd_status(conf, args):
    b = last_build(conf, args.job)
    if not b:
        sys.exit(f"取不到 {args.job} 状态 (job 不存在? 凭据? 网络/Clash?)")
    print(json.dumps(b, ensure_ascii=False))


def cmd_build(conf, args):
    job = args.job
    base = last_build(conf, job)
    base_num = base.get('number', 0) if base else 0
    code, body, _ = api(conf, f"/job/{urllib.parse.quote(job)}/build", method='POST', expect_json=False)
    if code not in (200, 201):
        sys.exit(f"触发失败 HTTP={code}: {str(body)[:300]}")
    print(f"已触发 {job} 构建（上一次 #{base_num}），HTTP={code}")
    if not args.wait:
        return
    # Jenkins 有静默期 + 队列：等到出现 > base_num 的构建且 building=false
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        b = last_build(conf, job)
        if b:
            num, building = b.get('number', 0), b.get('building', True)
            if num > base_num and not building:
                result = b.get('result')
                dur = round(b.get('duration', 0) / 1000)
                ok = result == 'SUCCESS'
                print(f"{'✓' if ok else '✗'} #{num} {result} ({dur}s)")
                sys.exit(0 if ok else 2)
            print(f"  #{num} {'building' if building else 'queued/idle'}...", flush=True)
        time.sleep(args.poll)
    sys.exit(f"超时 {args.timeout}s 未等到 {job} 构建完成")


def cmd_console(conf, args):
    seg = args.build_no or 'lastBuild'
    _, body, _ = api(conf, f"/job/{urllib.parse.quote(args.job)}/{seg}/consoleText", expect_json=False)
    print(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='subcmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('job')
    b.add_argument('--wait', action='store_true')
    b.add_argument('--timeout', type=int, default=1200)
    b.add_argument('--poll', type=int, default=10)
    s = sub.add_parser('status')
    s.add_argument('job')
    c = sub.add_parser('console')
    c.add_argument('job')
    c.add_argument('build_no', nargs='?')
    args = ap.parse_args()
    conf = load_conf()
    {'build': cmd_build, 'status': cmd_status, 'console': cmd_console}[args.subcmd](conf, args)


if __name__ == '__main__':
    main()
