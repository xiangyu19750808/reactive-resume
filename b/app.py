# /srv/wxresume/b/app.py
# -*- coding: utf-8 -*-
"""
WXResume Function B（测试版）
分层结构：
0) 配置与常量
1) 字体注册（_reg_font）
2) HTTP 基础工具
3) 飞书 API
4) 取数小工具
5) 解析层
6) 路由（/, /test_font, /test_bitable, /test_parse）
"""

# ========== 0) 配置与常量 ==========
import io, os, json, urllib.request
from flask import Flask, send_file, jsonify
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4

app = Flask(__name__)

APP_TOKEN       = os.getenv("APP_TOKEN","").strip()
TABLE_ID        = os.getenv("TABLE_ID","").strip()
VIEW_ID         = os.getenv("VIEW_ID","").strip()
LARK_APP_ID     = os.getenv("LARK_APP_ID","").strip()
LARK_APP_SECRET = os.getenv("LARK_APP_SECRET","").strip()

OPENID_FIELD    = os.getenv("OPENID_FIELD","openid").strip()
COMPANY_FIELD   = os.getenv("COMPANY_FIELD","目标公司").strip()
POSITION_FIELD  = os.getenv("POSITION_FIELD","目标岗位").strip()
RESUME_FIELD    = os.getenv("RESUME_FIELD","简历文件").strip()
AVATAR_FIELD    = os.getenv("AVATAR_FIELD","简历照片").strip()

OUTPUT_DIR      = os.getenv("OUTPUT_DIR","/srv/wxresume/output").strip()
FONT_PATH       = os.getenv("FONT_PATH","/srv/wxresume/fonts/NotoSansSC-Regular.ttf").strip()

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ========== 1) 字体注册 ==========
def _reg_font():
    if not os.path.exists(FONT_PATH):
        raise FileNotFoundError(f"Font file not found: {FONT_PATH}")
    try:
        pdfmetrics.registerFont(TTFont("CNSans", FONT_PATH))
    except Exception:
        pass
    try:
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        registerFontFamily("CNSans", normal="CNSans", bold="CNSans", italic="CNSans", boldItalic="CNSans")
    except Exception:
        pass
    return "CNSans"


# ========== 2) HTTP 基础工具 ==========
def _http_get_json(url: str, headers: dict=None, timeout=30) -> dict:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _http_post_json(url: str, payload: dict, headers: dict=None, timeout=30) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type":"application/json"}
    if headers: hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ========== 3) 飞书 API ==========
def _tenant_access_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    j = _http_post_json(url, {"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET})
    tok = j.get("tenant_access_token")
    if not tok: raise RuntimeError("get tenant_access_token failed")
    return tok

def _bitable_first_record() -> tuple:
    tok = _tenant_access_token()
    headers = {"Authorization": f"Bearer {tok}"}
    qs = "?page_size=1" + (f"&view_id={VIEW_ID}" if VIEW_ID else "")
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records{qs}"
    j = _http_get_json(url, headers=headers)
    items = ((j.get("data") or {}).get("items")) or []
    if not items: raise RuntimeError("No records in Bitable")
    return items[0], tok

def _download_media(file_token: str, tenant_token: str) -> bytes:
    url = f"https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tenant_token}"}, method="GET")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


# ========== 4) 取数小工具 ==========
def _plain(v):
    if isinstance(v, list) and v and isinstance(v[0], dict) and "text" in v[0]:
        return "".join([str(x.get("text","")) for x in v])
    return str(v or "")

def _first_file(val):
    if isinstance(val, list) and val:
        v = val[0]
        if isinstance(v, dict) and v.get("file_token"):
            return v
    return None


# ========== 5) 解析层 ==========
def sanitize_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text or "")
    text = text.replace("\x00", " ").replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    clean, prev_blank = [], False
    for ln in lines:
        if not ln:
            if not prev_blank:
                clean.append("")
            prev_blank = True
        else:
            clean.append(ln)
            prev_blank = False
    return "\n".join(clean).strip()

def _fallback_decode(content: bytes) -> str:
    if content is None:
        return ""
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return content.decode(enc)
        except Exception:
            continue
    return content.decode("utf-8", errors="ignore")

def _parse_docx(content: bytes) -> str:
    from docx import Document
    import io as _io
    doc = Document(_io.BytesIO(content))
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    for tbl in getattr(doc, "tables", []) or []:
        for row in tbl.rows:
            row_txt = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_txt:
                paras.append(" | ".join(row_txt))
    return "\n".join(paras)

def _parse_pdf(content: bytes) -> str:
    import io as _io
    try:
        from pdfminer_high_level import extract_text as _pdfminer_extract  # typo? keep original behavior
    except Exception:
        from pdfminer.high_level import extract_text as _pdfminer_extract
    try:
        return _pdfminer_extract(_io.BytesIO(content)) or ""
    except Exception:
        import PyPDF2
        reader = PyPDF2.PdfReader(_io.BytesIO(content))
        buf = []
        for pg in reader.pages:
            try:
                t = pg.extract_text() or ""
                if t.strip():
                    buf.append(t)
            except Exception:
                continue
        return "\n".join(buf)

def extract_text_from_attachment(content: bytes, filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        raw = _parse_docx(content)
    elif name.endswith(".pdf"):
        raw = _parse_pdf(content)
    elif name.endswith((".txt", ".md", ".csv")):
        raw = _fallback_decode(content)
    else:
        raw = _fallback_decode(content)
    return sanitize_text(raw)


# ========== 6) 路由 ==========
@app.route("/", methods=["GET"])
def index():
    return jsonify({"ok": True, "service": "resume-b", "tip": "/test_font | /test_bitable | /test_parse"})

@app.route("/test_font", methods=["GET"])
def test_font():
    font_name = _reg_font()
    out_path = os.path.join(OUTPUT_DIR, "font_test.pdf")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont(font_name, 20)
    c.drawString(100, 750, "中文测试：你好，世界！")
    c.drawString(100, 720, "Resume Service - 字体测试 OK")
    c.save()
    buf.seek(0)
    with open(out_path, "wb") as f:
        f.write(buf.getvalue())
    return send_file(io.BytesIO(buf.getvalue()), mimetype="application/pdf",
                     as_attachment=True, download_name="font_test.pdf")

@app.route("/test_bitable", methods=["GET"])
def test_bitable():
    try:
        rec, tok = _bitable_first_record()
        fields = rec.get("fields") or {}
        record_id = rec.get("record_id") or rec.get("id")

        openid   = (_plain(fields.get(OPENID_FIELD)) or "unknown").strip() or "unknown"
        company  = _plain(fields.get(COMPANY_FIELD) or "未填公司")
        position = _plain(fields.get(POSITION_FIELD) or "未填岗位")

        outdir = os.path.join(OUTPUT_DIR, openid)
        os.makedirs(outdir, exist_ok=True)

        # 简历附件
        resume_meta = None; resume_path = None
        ro = _first_file(fields.get(RESUME_FIELD))
        if ro:
            try:
                rb = _download_media(ro["file_token"], tok)
                rname = ro.get("name") or "resume.bin"
                resume_path = os.path.join(outdir, rname)
                with open(resume_path, "wb") as f:
                    f.write(rb)
                resume_meta = {"name": rname, "size": len(rb)}
            except Exception as e:
                resume_meta = {"error": f"download resume failed: {e}"}

        # 头像附件
        avatar_meta = None; avatar_path = None
        ao = _first_file(fields.get(AVATAR_FIELD))
        if ao:
            try:
                ab = _download_media(ao["file_token"], tok)
                aname = ao.get("name") or "avatar.bin"
                avatar_path = os.path.join(outdir, aname)
                with open(avatar_path, "wb") as f:
                    f.write(ab)
                avatar_meta = {"name": aname, "size": len(ab)}
            except Exception as e:
                avatar_meta = {"error": f"download avatar failed: {e}"}

        return jsonify({
            "ok": True,
            "record_id": record_id,
            "openid": openid,
            "company": company,
            "position": position,
            "resume": {"meta": resume_meta, "path": resume_path},
            "avatar": {"meta": avatar_meta, "path": avatar_path},
            "outdir": outdir
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/test_parse", methods=["GET"])
def test_parse():
    """
    生成两份解析文本：
    1) 简历文件 → resume_parsed.txt
    2) 目标公司 + 岗位JD → job_parsed.txt
    """
    try:
        rec, _ = _bitable_first_record()
        fields = rec.get("fields") or {}
        openid = (_plain(fields.get(OPENID_FIELD)) or "unknown").strip() or "unknown"
        outdir = os.path.join(OUTPUT_DIR, openid)
        os.makedirs(outdir, exist_ok=True)

        # 1) 简历文件
        resume_txt_path = None
        ro = _first_file(fields.get(RESUME_FIELD))
        if ro:
            rname = ro.get("name") or "resume.bin"
            resume_path = os.path.join(outdir, rname)
            if os.path.exists(resume_path):
                with open(resume_path, "rb") as f:
                    rb = f.read()
                resume_text = extract_text_from_attachment(rb, rname)
                resume_txt_path = os.path.join(outdir, "resume_parsed.txt")
                with open(resume_txt_path, "w", encoding="utf-8") as f:
                    f.write(resume_text)

        # 2) 公司+岗位JD
        company = _plain(fields.get(COMPANY_FIELD) or "")
        position = _plain(fields.get(POSITION_FIELD) or "")
        job_text = f"目标公司：{company}\n目标岗位：{position}\n"
        job_txt_path = os.path.join(outdir, "job_parsed.txt")
        with open(job_txt_path, "w", encoding="utf-8") as f:
            f.write(job_text)

        return jsonify({
            "ok": True,
            "openid": openid,
            "resume_parsed": resume_txt_path,
            "job_parsed": job_txt_path
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500

# ========== 7) 生成层（Moonshot 调用示例） ==========
import requests

MOONSHOT_API_KEY = os.getenv("MOONSHOT_API_KEY","").strip()

def _call_moonshot(prompt: str, model="moonshot-v1-32k") -> str:
    """
    调用 Moonshot API，返回生成的文本
    """
    url = "https://api.moonshot.cn/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MOONSHOT_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一位顶尖的简历分析与生成专家。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    j = r.json()
    return j["choices"][0]["message"]["content"]

# ===== 生成层最小测试路由：/test_generate =====
@app.route("/test_generate", methods=["GET"])
def test_generate():
    """
    读取 /test_parse 落盘的 resume_parsed.txt 与 job_parsed.txt，
    调用 Moonshot 生成两份文本，并落盘：
      - resume_generated.txt（面向目标公司/岗位的定制简历）
      - analysis_generated.txt（能力评估+匹配度+面试问题）
    """
    try:
        import requests, os

        key = os.getenv("MOONSHOT_API_KEY", "").strip()
        if not key:
            return jsonify({"ok": False, "error": "MOONSHOT_API_KEY 未设置"}), 400
        api_base = os.getenv("MOONSHOT_API_BASE", "https://api.moonshot.cn/v1").strip()
        model    = os.getenv("MOONSHOT_MODEL", "moonshot-v1-32k").strip()

        # 取 openid 与输出目录
        rec, _ = _bitable_first_record()
        fields = rec.get("fields") or {}
        openid = (_plain(fields.get(OPENID_FIELD)) or "unknown").strip() or "unknown"
        outdir = os.path.join(OUTPUT_DIR, openid)

        resume_txt = os.path.join(outdir, "resume_parsed.txt")
        job_txt    = os.path.join(outdir, "job_parsed.txt")
        if not os.path.exists(resume_txt) or not os.path.exists(job_txt):
            return jsonify({"ok": False, "error": "缺少解析文件，请先调用 /test_parse"}), 400

        with open(resume_txt, "r", encoding="utf-8", errors="ignore") as f:
            resume_text = f.read()
        with open(job_txt, "r", encoding="utf-8", errors="ignore") as f:
            job_text = f.read()

        def _call_moonshot(prompt: str) -> str:
            url = api_base.rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [
                    {"role":"system","content":"你是顶尖级简历分析与生成专家。所有输出为中文，务实、简洁、可执行。"},
                    {"role":"user","content": prompt}
                ],
                "temperature": 0.3,
                "stream": False
            }
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            j = r.json()
            return (j.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()

        # 1) 定制简历
        prompt_resume = (
            "以下是用户原始简历文本：\n<<<" + resume_text + ">>>\n\n"
            "以下是目标公司与岗位信息：\n<<<" + job_text + ">>>\n\n"
            "请基于以上信息，为该岗位生成一份‘投递版’专业简历：\n"
            "- 只保留与岗位强相关的经历与能力；\n"
            "- 要点用动宾结构 + 量化结果；\n"
            "- 不杜撰经历；\n"
            "- 输出为可直接复制粘贴的纯文本简历。"
        )
        gen_resume = _call_moonshot(prompt_resume)

        # 2) 分析报告
        prompt_analysis = (
            "以下是用户原始简历文本：\n<<<" + resume_text + ">>>\n\n"
            "以下是目标公司与岗位信息：\n<<<" + job_text + ">>>\n\n"
            "请输出分析报告（纯文本）：\n"
            "a) 用户能力全方位评估（分点，最后附10分制评分表）\n"
            "b) 用户能力与岗位需求匹配度评分，并说明主要理由（要点化）\n"
            "c) 预测HR在二面可能会问的5-10个问题，简短不赘述\n"
            "d) 任何有助于通过二面的补充建议（如补档、作品集、量化数据等）"
        )
        gen_analysis = _call_moonshot(prompt_analysis)

        # 落盘
        gen_resume_path   = os.path.join(outdir, "resume_generated.txt")
        gen_analysis_path = os.path.join(outdir, "analysis_generated.txt")
        with open(gen_resume_path, "w", encoding="utf-8") as f:
            f.write(gen_resume)
        with open(gen_analysis_path, "w", encoding="utf-8") as f:
            f.write(gen_analysis)

        return jsonify({
            "ok": True,
            "openid": openid,
            "resume_generated": gen_resume_path,
            "analysis_generated": gen_analysis_path,
            "preview": {
                "resume": gen_resume[:200],
                "analysis": gen_analysis[:200]
            }
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ===== 专业版生成层测试路由：/test_generate_pro（不改旧路由） =====
@app.route("/test_generate_pro", methods=["GET"])
def test_generate_pro():
    """
    基于 resume_parsed.txt + job_parsed.txt 生成更专业的两份产物：
      1) resume_generated_pro.txt  —— 面向目标公司的投递版简历（顶尖水准）
      2) analysis_generated_pro.txt —— 能力全评 + 匹配度 + 预测面试问题（顶尖水准）
         同时落盘结构化 JSON：analysis_generated_pro.json（含 scores 与 chart_spec，便于后续彩色图表渲染）
    """
    try:
        import os, json, requests, re

        key = os.getenv("MOONSHOT_API_KEY","").strip()
        if not key:
            return jsonify({"ok": False, "error": "MOONSHOT_API_KEY 未设置"}), 400
        api_base = os.getenv("MOONSHOT_API_BASE","https://api.moonshot.cn/v1").strip()
        model    = os.getenv("MOONSHOT_MODEL","moonshot-v1-32k").strip()

        # 取首条记录 + 输出目录
        rec, _ = _bitable_first_record()
        fields   = rec.get("fields") or {}
        openid   = (_plain(fields.get(OPENID_FIELD)) or "unknown").strip() or "unknown"
        company  = _plain(fields.get(COMPANY_FIELD) or "")
        position = _plain(fields.get(POSITION_FIELD) or "")
        outdir   = os.path.join(OUTPUT_DIR, openid)

        resume_txt = os.path.join(outdir, "resume_parsed.txt")
        job_txt    = os.path.join(outdir, "job_parsed.txt")
        if not (os.path.exists(resume_txt) and os.path.exists(job_txt)):
            return jsonify({"ok": False, "error": "缺少解析文件，请先调用 /test_parse"}), 400

        with open(resume_txt, "r", encoding="utf-8", errors="ignore") as f:
            resume_text = f.read()
        with open(job_txt, "r", encoding="utf-8", errors="ignore") as f:
            job_text = f.read()

        def _call_moonshot_local(prompt: str, temperature=0.25) -> str:
            url = api_base.rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type":"application/json"}
            payload = {
                "model": model,
                "messages": [
                    {"role":"system","content":"你是世界级招聘专家与简历教练，输出专业、精准、务实的中文内容。"},
                    {"role":"user","content": prompt}
                ],
                "temperature": float(temperature),
                "stream": False
            }
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            j = r.json()
            return (j.get("choices") or [{}])[0].get("message", {}).get("content","").strip()

        # ---- 产物一：投递版简历（顶尖水准） ----
        prompt_resume = f"""# 目标
基于“用户真实简历文本”与“目标公司+岗位JD”，生成一份**HR初筛高通过率**的投递版专业简历（纯文本）。
【目标公司】{company}
【目标岗位】{position}
【岗位JD原文】<<<{job_text}>>>
【用户原始简历文本】<<<{resume_text}>>>

# 输出要求（严格遵守）
- 结构：抬头(姓名/目标职位/联系方式)；求职目标(≤2行)；核心优势(3-5条、动宾结构+量化)；
  关键业绩(6-10条、每条含动作+方法+指标提升)；核心技能(工具/方法/行业)；经历(逆序、与岗位强相关部分优先)；
  教育/证书；补充信息（如可入职时间/城市偏好）。
- 只保留与“{position}”强相关的信息；不得杜撰或夸大无凭证数据。
- 用短句、去口语化；量化指标尽量具体（如“转化率+18%”“成本-12%”“周期从10天→3天”）。
- 输出为**可直接投递**的中文纯文本；不加解释说明，不输出多余段落。"""

        resume_out = _call_moonshot_local(prompt_resume, temperature=0.2)

        # ---- 产物二：能力评估+匹配度+面试问题（顶尖水准，含结构化JSON以供彩色图表渲染） ----
        schema = {
            "scores":[{"dimension":"string","score":0.0}],
            "rationales":{"维度":"简明理由"},
            "summary":"<=60字要点总结",
            "interview_questions":["5-10条"],
            "chart_spec":{"type":"bar","title":"能力结构","data":[{"name":"维度","value":0.0}]}
        }
        prompt_analysis_json = f"""严格**仅输出 JSON**，匹配此 SCHEMA（字段名保持一致）：
SCHEMA={json.dumps(schema, ensure_ascii=False)}
评分范围0~10，保留1位小数；覆盖但不限于这些维度：
["岗位匹配度","团队管理","项目落地能力","数据与度量意识","沟通影响力","问题分解与结构化","学习适应力","成本与风险控制","工具与自动化能力","职业稳定性"]
背景：
【目标公司】{company}
【目标岗位】{position}
【岗位JD原文】<<<{job_text}>>>
【用户原始简历文本】<<<{resume_text}>>>
要求：
- "scores"：每个维度一个对象{{dimension, score}}，分数数值型；
- "rationales"：对每个维度给出1-2句理由；
- "summary"：≤60字；
- "interview_questions"：给出5-10条**极可能**被追问的二面问题（短句、直击能力验证）；
- "chart_spec"：柱状图配置，data 数组与 scores 对应，用于后续彩色渲染。"""
        out_json_text = _call_moonshot_local(prompt_analysis_json, temperature=0.2)

        def _safe_load_json(s: str) -> dict:
            try:
                return json.loads(s)
            except Exception:
                m = re.search(r'\{.*\}', s, re.S)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except Exception:
                        pass
            return {"scores":[], "rationales":{}, "summary":"", "interview_questions":[], "chart_spec":{"type":"bar","data":[]}}

        analysis_obj = _safe_load_json(out_json_text)

        # 生成可读报告文本
        lines = []
        lines.append(f"# 能力评估与岗位匹配报告｜{company}｜{position}")
        lines.append("")
        if analysis_obj.get("summary"):
            lines.append("## 摘要")
            lines.append(analysis_obj["summary"])
            lines.append("")
        if analysis_obj.get("scores"):
            lines.append("## 匹配度评分（/10）")
            for s in analysis_obj["scores"]:
                try:
                    val = float(s.get("score", 0))
                except Exception:
                    val = 0.0
                dim = str(s.get("dimension","")).strip() or "未命名"
                lines.append(f"- {dim}：{round(max(0.0,min(10.0,val)),1)}")
            lines.append("")
        if analysis_obj.get("rationales"):
            lines.append("## 维度说明")
            for k,v in (analysis_obj["rationales"] or {}).items():
                lines.append(f"- {k}：{str(v).strip()}")
            lines.append("")
        if analysis_obj.get("interview_questions"):
            lines.append("## 预测二面问题（练习清单）")
            for q in analysis_obj["interview_questions"][:10]:
                lines.append(f"- {str(q).strip()}")
            lines.append("")
        # 说明 chart_spec 已生成，后续用于彩色图表渲染（PDF阶段再画）
        if analysis_obj.get("chart_spec"):
            lines.append("（已生成 chart_spec，后续渲染层将输出彩色图表）")

        # 落盘
        resume_pro_path   = os.path.join(outdir, "resume_generated_pro.txt")
        analysis_txt_path = os.path.join(outdir, "analysis_generated_pro.txt")
        analysis_json_path= os.path.join(outdir, "analysis_generated_pro.json")
        with open(resume_pro_path, "w", encoding="utf-8") as f:
            f.write(resume_out.strip())
        with open(analysis_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).strip()+"\n")
        with open(analysis_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_obj, f, ensure_ascii=False, indent=2)

        # 预览
        preview_scores = (analysis_obj.get("scores") or [])[:5]
        preview_qs     = (analysis_obj.get("interview_questions") or [])[:5]

        return jsonify({
            "ok": True,
            "openid": openid,
            "company": company,
            "position": position,
            "resume_generated_pro": resume_pro_path,
            "analysis_generated_pro": analysis_txt_path,
            "analysis_json": analysis_json_path,
            "preview": {
                "resume_head": resume_out[:200],
                "scores_top5": preview_scores,
                "questions_top5": preview_qs
            }
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500



# ========== 8) 渲染层（PDF + 图表，追加） ==========

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as _rl_canvas
from reportlab.lib.colors import HexColor, black, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os, io, math, tempfile

# --- 颜色 & 尺寸 ---
TEAL  = HexColor("#26B4A3")   # 主绿（接近模板）
TEAL_DARK = HexColor("#0E8E7F")
GREY  = HexColor("#9BA3AE")
LINE  = HexColor("#E6F3F1")
TXT   = HexColor("#252A34")

def mm(v):  # 毫米转 pt
    return v * 2.834645669

PAGE_W, PAGE_H = A4
MARGIN_L, MARGIN_R = mm(24), mm(18)
MARGIN_T, MARGIN_B = mm(18), mm(18)

# --- 字体注册：优先 Noto/思源/微软雅黑，兜底 Helvetica ---
def _try_register_font(name, path_list):
    for p in path_list:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont(name, p))
                return True
            except Exception:
                pass
    return False

def _ensure_fonts():
    if "CN-Regular" in pdfmetrics.getRegisteredFontNames():
        return
    # 常见中文字体路径候选
    candidates_regular = [
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto/NotoSansSC-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/chinese/TrueType/simhei.ttf",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ]
    candidates_bold = [
        "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto/NotoSansSC-Bold.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/chinese/TrueType/simhei.ttf",  # 黑体当粗体
    ]
    ok_r = _try_register_font("CN-Regular", candidates_regular)
    ok_b = _try_register_font("CN-Bold", candidates_bold)
    if not ok_r:
        # 兜底
        pdfmetrics.registerFont(TTFont("CN-Regular", candidates_regular[-1])) if os.path.exists(candidates_regular[-1]) else None
    # Helvetica 兜底别名
    if "CN-Regular" not in pdfmetrics.getRegisteredFontNames():
        # 没中文字体时也能画出英文/数字，不报错
        pass

def _text_width(s, font, size):
    try:
        return pdfmetrics.stringWidth(s, font, size)
    except Exception:
        return pdfmetrics.stringWidth(s, "Helvetica", size)

def _draw_para(c, text, x, y, w, font="CN-Regular", size=10.5, leading=14, color=TXT):
    """简单换行排版，返回绘制后最后一行 baseline 的 y 值"""
    c.setFillColor(color)
    c.setFont(font if font in pdfmetrics.getRegisteredFontNames() else "Helvetica", size)
    words = []
    # 按中文/英文混排粗切
    for line in text.split("\n"):
        if not line.strip():
            y -= leading
            continue
        buf = ""
        for ch in line:
            candidate = (buf + ch)
            if _text_width(candidate, c._fontname, size) <= w:
                buf = candidate
            else:
                words.append(buf)
                buf = ch
        if buf:
            words.append(buf)
        for seg in words:
            c.drawString(x, y, seg)
            y -= leading
        words = []
    return y

def _bullet_lines(c, items, x, y, w, font="CN-Regular", size=10.5, leading=15, color=TXT):
    c.setFont(font if font in pdfmetrics.getRegisteredFontNames() else "Helvetica", size)
    c.setFillColor(color)
    bullet_w = _text_width("• ", c._fontname, size)
    for it in items:
        # 换行包裹
        line = f"• {it}"
        buf, cur = "", []
        for ch in line:
            cand = buf + ch
            if _text_width(cand, c._fontname, size) <= w:
                buf = cand
            else:
                cur.append(buf); buf = ch
        if buf: cur.append(buf)
        first = True
        for seg in cur:
            if first:
                c.drawString(x, y, seg)
                first = False
            else:
                c.drawString(x + bullet_w, y, seg.lstrip())
            y -= leading
    return y

def _section_header(c, title, x, y):
    # 左侧方形图标
    s = mm(9)  # 约 26px
    c.setFillColor(TEAL)
    c.roundRect(x, y - s + 2, s, s, 3, stroke=0, fill=1)
    # 标题
    c.setFillColor(TEAL_DARK)
    c.setFont("CN-Bold" if "CN-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold", 14)
    c.drawString(x + s + mm(4), y - s/2 + 3, title)
    # 细分割线
    c.setStrokeColor(LINE); c.setLineWidth(1)
    c.line(MARGIN_L, y - s - mm(3), PAGE_W - MARGIN_R, y - s - mm(3))
    return y - s - mm(6)

def _draw_right_tag(c, txt, x_right, y_top):
    """右侧公司/项目标签（灰黑字）"""
    c.setFillColor(TXT)
    c.setFont("CN-Bold" if "CN-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold", 10.5)
    w = _text_width(txt, c._fontname, 10.5)
    c.drawString(x_right - w, y_top, txt)

def _rounded_image(c, img_path, cx, cy, d_mm):
    """头像圆形裁剪（Pillow 可用时圆形；否则画圆边+方图）"""
    d = int(mm(d_mm))
    try:
        from PIL import Image, ImageDraw
        im = Image.open(img_path).convert("RGB").resize((d, d))
        mask = Image.new("L", (d, d), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, d, d), fill=255)
        circ = Image.new("RGB", (d, d), (255, 255, 255))
        circ.paste(im, (0, 0), mask=mask)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        circ.save(tmp.name, "PNG")
        c.drawImage(tmp.name, cx - d/2, cy - d/2, d, d, mask='auto')
        try: os.unlink(tmp.name)
        except: pass
    except Exception:
        # 兜底：圆环+方图
        c.setFillColor(white); c.circle(cx, cy, d/2 + 2, stroke=0, fill=1)
        c.setStrokeColor(white); c.setLineWidth(4); c.circle(cx, cy, d/2, stroke=1, fill=0)
        c.drawImage(img_path, cx - d/2, cy - d/2, d, d, preserveAspectRatio=True, anchor='c')

def _draw_header_bar(c, spec):
    # 顶部整条绿带
    bar_h = mm(20)
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - bar_h, PAGE_W, bar_h, stroke=0, fill=1)
    # 左侧箭头块
    arrow_w = mm(16)
    c.setFillColor(white)
    c.polygon([mm(10), PAGE_H - bar_h/2,
               mm(10)+arrow_w, PAGE_H - bar_h + 3,
               mm(10)+arrow_w, PAGE_H - 3], stroke=0, fill=1)
    # “个人简历 PERSONAL RESUME”
    c.setFillColor(white)
    c.setFont("CN-Bold" if "CN-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold", 20)
    c.drawString(mm(28), PAGE_H - bar_h/2 + 3, "个人简历")
    c.setFont("Helvetica", 12)
    c.drawString(mm(90), PAGE_H - bar_h/2 + 1, "PERSONAL RESUME")

    # 右侧头像
    photo_d_mm = 48
    cx = PAGE_W - MARGIN_R - mm(8) - mm(photo_d_mm/2)
    cy = PAGE_H - bar_h - mm(12) - mm(photo_d_mm/2)
    avatar = spec.get("avatar_path")
    if avatar and os.path.exists(avatar):
        _rounded_image(c, avatar, cx, cy, photo_d_mm)

    # 基本信息（左上）
    base_x = MARGIN_L
    base_y = PAGE_H - bar_h - mm(12)
    name = spec.get("name") or ""
    phone = spec.get("phone") or " "
    email = spec.get("email") or " "
    c.setFillColor(TXT)
    c.setFont("CN-Bold" if "CN-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold", 14.5)
    c.drawString(base_x, base_y, f"姓名：{name}")
    c.setFont("CN-Regular" if "CN-Regular" in pdfmetrics.getRegisteredFontNames() else "Helvetica", 11)
    c.drawString(base_x + mm(60), base_y, f"电话：{phone}")
    c.drawString(base_x, base_y - mm(8), f"邮箱：{email}")

    # 下方分隔线
    c.setStrokeColor(LINE); c.setLineWidth(1)
    c.line(MARGIN_L, base_y - mm(14), PAGE_W - MARGIN_R, base_y - mm(14))
    return base_y - mm(20)

def render_resume_pdf_wps_green(spec: dict) -> bytes:
    """
    严格按照“WPS 绿条风格”模板排版，输入 spec 结构示例：
    {
      name, phone, email, avatar_path, intent_position, job_type,
      experiences: [{period, role, company, bullets: []}, ...],
      projects: [{period, name, tag, bullets: []}],
      skills: ["...", "...", ...]
    }
    """
    _ensure_fonts()
    buf = io.BytesIO()
    c = _rl_canvas.Canvas(buf, pagesize=A4)
    c.setTitle("个人简历")

    # 头部
    cur_y = _draw_header_bar(c, spec)

    # --- 求职意向 ---
    cur_y = _section_header(c, "求职意向", MARGIN_L, cur_y)
    # 两列小字段
    c.setFont("CN-Regular" if "CN-Regular" in pdfmetrics.getRegisteredFontNames() else "Helvetica", 11.5)
    c.setFillColor(TXT)
    left_label_w = _text_width("意向岗位：", c._fontname, 11.5)
    c.drawString(MARGIN_L, cur_y, "意向岗位：")
    c.drawString(MARGIN_L + left_label_w + mm(2), cur_y, (spec.get("intent_position") or ""))
    c.drawString(MARGIN_L + mm(60), cur_y, "求职类型：")
    c.drawString(MARGIN_L + mm(60) + _text_width("求职类型：", c._fontname, 11.5) + mm(2), cur_y, (spec.get("job_type") or "社招"))
    cur_y -= mm(10)

    # --- 工作经历 ---
    cur_y = _section_header(c, "工作经历", MARGIN_L, cur_y)
    x_text = MARGIN_L
    x_right = PAGE_W - MARGIN_R
    w_text = x_right - x_text
    for idx, exp in enumerate(spec.get("experiences") or []):
        # 时间
        c.setFillColor(TXT)
        c.setFont("CN-Bold" if "CN-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold", 12)
        c.drawString(x_text, cur_y, (exp.get("period") or "").strip())
        # 右侧公司
        if exp.get("company"):
            _draw_right_tag(c, exp["company"].strip(), x_right, cur_y)
        cur_y -= mm(6.5)
        # 职位（次行）
        c.setFont("CN-Regular" if "CN-Regular" in pdfmetrics.getRegisteredFontNames() else "Helvetica", 11.5)
        c.drawString(x_text, cur_y, (exp.get("role") or "").strip())
        cur_y -= mm(5)
        # 要点
        bullets = [b for b in (exp.get("bullets") or []) if b.strip()]
        cur_y = _bullet_lines(c, bullets, x_text, cur_y, w_text, leading=15)
        cur_y -= mm(2)
        if idx < len(spec.get("experiences")) - 1:
            # 小间距
            cur_y -= mm(2)

    # --- 项目经历 ---
    cur_y = _section_header(c, "项目经历", MARGIN_L, cur_y)
    for idx, pj in enumerate(spec.get("projects") or []):
        c.setFillColor(TXT)
        c.setFont("CN-Bold" if "CN-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold", 12)
        c.drawString(MARGIN_L, cur_y, (pj.get("period") or "").strip())
        if pj.get("tag"):
            _draw_right_tag(c, pj["tag"].strip(), PAGE_W - MARGIN_R, cur_y)
        cur_y -= mm(6.5)
        c.setFont("CN-Regular" if "CN-Regular" in pdfmetrics.getRegisteredFontNames() else "Helvetica", 11.5)
        c.drawString(MARGIN_L, cur_y, (pj.get("name") or "").strip())
        cur_y -= mm(5)
        bullets = [b for b in (pj.get("bullets") or []) if b.strip()]
        cur_y = _bullet_lines(c, bullets, MARGIN_L, cur_y, PAGE_W - MARGIN_L - MARGIN_R, leading=15)
        cur_y -= mm(3)
        if idx < len(spec.get("projects")) - 1:
            cur_y -= mm(2)

    # --- 相关技能 ---
    cur_y = _section_header(c, "相关技能", MARGIN_L, cur_y)
    skills = [s.strip() for s in (spec.get("skills") or []) if s.strip()]
    if skills:
        # 以点列出；不足自动换行；两列视可用宽度自适应
        col_gap = mm(14)
        col_w = (PAGE_W - MARGIN_L - MARGIN_R - col_gap) / 2
        left_y = cur_y
        right_y = cur_y
        half = math.ceil(len(skills) / 2)
        left, right = skills[:half], skills[half:]
        left_y = _bullet_lines(c, left, MARGIN_L, left_y, col_w, leading=15)
        right_y = _bullet_lines(c, right, MARGIN_L + col_w + col_gap, right_y, col_w, leading=15)
        cur_y = min(left_y, right_y) - mm(2)
    else:
        c.setFont("CN-Regular" if "CN-Regular" in pdfmetrics.getRegisteredFontNames() else "Helvetica", 11)
        c.setFillColor(GREY)
        c.drawString(MARGIN_L, cur_y, "（无）")
        cur_y -= mm(6)

    c.showPage()
    c.save()
    return buf.getvalue()

# ========== 8) 渲染层（END） ==========



# ===== 渲染层测试路由：/test_render_pro（基于已生成的专业版文本/JSON） =====
@app.route("/test_render_pro", methods=["GET"])
def test_render_pro():
    try:
        # 基于 bitable 首条记录定位 openid & outdir
        rec, _ = _bitable_first_record()
        fields   = rec.get("fields") or {}
        openid   = (_plain(fields.get(OPENID_FIELD)) or "unknown").strip() or "unknown"
        company  = _plain(fields.get(COMPANY_FIELD) or "")
        position = _plain(fields.get(POSITION_FIELD) or "")
        outdir   = os.path.join(OUTPUT_DIR, openid)

        # 输入文件（来自前一步 /test_generate_pro）
        resume_pro_txt = os.path.join(outdir, "resume_generated_pro.txt")
        analysis_pro_txt= os.path.join(outdir, "analysis_generated_pro.txt")
        analysis_pro_json= os.path.join(outdir, "analysis_generated_pro.json")
        if not (os.path.exists(resume_pro_txt) and os.path.exists(analysis_pro_txt) and os.path.exists(analysis_pro_json)):
            return jsonify({"ok": False, "error": "缺少生成文件，请先调用 /test_generate_pro"}), 400

        with open(resume_pro_txt, "r", encoding="utf-8", errors="ignore") as f:
            resume_text = f.read()
        with open(analysis_pro_txt, "r", encoding="utf-8", errors="ignore") as f:
            analysis_text = f.read()
        with open(analysis_pro_json, "r", encoding="utf-8") as f:
            analysis_obj = json.load(f)
        scores = analysis_obj.get("scores", [])

        avatar_path = _pick_avatar(outdir)

        # 生成两份 PDF
        resume_pdf   = build_resume_pdf(resume_text, company, position, avatar_path)
        analysis_pdf = build_analysis_pdf(company, position, analysis_text, scores)

        ts = now_ts()
        resume_path   = os.path.join(outdir, f"resume_{ts}.pdf")
        analysis_path = os.path.join(outdir, f"analysis_{ts}.pdf")
        _save_bytes(resume_path, resume_pdf)
        _save_bytes(analysis_path, analysis_pdf)

        return jsonify({
            "ok": True,
            "openid": openid,
            "company": company,
            "position": position,
            "resume_path": resume_path,
            "analysis_path": analysis_path
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002)
