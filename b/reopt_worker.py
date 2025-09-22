import json, os, time, subprocess, pathlib, sys
QUEUE = pathlib.Path("/srv/wxresume/reopt_queue")
DONE  = QUEUE / "done"
GEN   = "/srv/wxresume/b/generate_once.py"

def process(fn: pathlib.Path):
    try:
        data = json.loads(fn.read_text(encoding="utf-8"))
        openid = data["openid"]
        # 这里简单处理：再次优化 = 重新生成一份新的PDF（可换成带主题/参数）
        cmd = ["python3", GEN, "--openid", openid, "--theme", "jsonresume-theme-flat"]
        subprocess.check_call(cmd)
        DONE.mkdir(parents=True, exist_ok=True)
        fn.rename(DONE / fn.name)
        print("OK", fn.name)
    except Exception as e:
        print("ERR", fn.name, e, file=sys.stderr)

def main():
    QUEUE.mkdir(parents=True, exist_ok=True)
    print("reopt_worker watching", QUEUE)
    seen = set()
    while True:
        for f in sorted(QUEUE.glob("*.json")):
            if f.name in seen:
                continue
            seen.add(f.name)
            process(f)
        time.sleep(2)

if __name__ == "__main__":
    main()
