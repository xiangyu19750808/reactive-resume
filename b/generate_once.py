import argparse, json, os, time, base64, shutil, urllib.request

def b64url(s): return base64.urlsafe_b64encode(s).decode().rstrip("=")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--openid", required=True)
    p.add_argument("--theme", default="jsonresume-theme-flat")
    args = p.parse_args()

    resume = {
        "basics": {"name":"测试用户","email":"test@example.com","label":"产品经理","summary":"有3年互联网经验"},
        "work":[{"name":"示例公司","position":"产品经理","startDate":"2022-01","summary":"负责需求与落地"}],
        "education":[{"institution":"示例大学","area":"计算机","studyType":"本科","startDate":"2018","endDate":"2022"}],
        "skills":[{"name":"产品设计","level":"中级","keywords":["PRD","Axure"]}]
    }
    data = json.dumps({"resume": resume, "theme": args.theme}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:3000/render", data=data, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    assert out.get("ok"), f"render failed: {out}"
    pdf_path = out["pdf_path"]

    ts = int(time.time()*1000)
    dst_dir = f"/srv/wxresume/resumes_pdf/{args.openid}"
    os.makedirs(dst_dir, exist_ok=True)
    filename = f"{ts}.pdf"
    dst = os.path.join(dst_dir, filename)
    shutil.copyfile(pdf_path, dst)

    rid = b64url(f"{args.openid}/{filename}".encode("utf-8"))
    print(json.dumps({"ok": True, "result_id": rid, "file": dst}, ensure_ascii=False))
if __name__ == "__main__":
    main()
