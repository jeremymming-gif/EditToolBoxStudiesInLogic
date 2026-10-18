#!/usr/bin/env python3.14
"""DeepSeek 检索证据 P2 的判据测试：证据注入提示词 + `% 来源：` URL 白名单（第 72 条起）。

P1（v1.0.13）只把证据写进任务书；P2（目标 v1.1.0）起：
  ① 逐条请求的提示词里**注入**证据包（候选 URL / authority / 匹配分），并要求
     「`% 来源：` 只能写证据里出现过的 URL」；
  ② AI 结果并入 `reference.bib` **之前**做白名单校验：`% 来源：` 行的 http(s) URL 必须
     来自本次**实际检索到**的来源（证据包 URL ∪ 内置检索的 url_citation），否则该条不并入
     （编造的 URL 比不写更糟——假的信任信号）。

本套件守住（本地假端点，**不联网**、不花钱）：
  (a) 提示词注入：默认（无证据）逐字节不变；有证据含完整 URL；未查到写明「按查不到处理」；
  (b) 白名单函数：只认 `% 来源：` 行上的 URL、忽略尾部标点/末尾 `/`；说明文字（无 URL）放行；
  (c) convert 端到端：双 mock（检索 + chat）。有效 URL 的条目并入、编造 URL 的条目被挡下
      （bib 原文该条逐字节未动、任务书合并记录写明原因）；默认不开检索时提示词里没有证据块。

用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_evidence_injection.py
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import aibib  # noqa: E402
from slcn import refmark  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══ (a) 提示词注入 ═════════════════════════════════════════════════════════
print("── (a) 逐条提示词注入证据 ──")
BODY = "  author = {高贝贝},\n  title  = {极小主义视域下的真谓词},\n  year   = {2021}"
one_plain = refmark.entry_prompt("article", "Gao2021", BODY)
check("(a) 默认（无证据/无原文/无检索）与 P1 之前逐字节相同",
      one_plain == ('@article{Gao2021,\nauthor = {高贝贝},\n  title  = {极小主义视域下的真谓词},\n'
                    '  year   = {2021}}'),
      repr(one_plain))
check("(a) 显式 evidence=None 与不传参数逐字节相同",
      refmark.entry_prompt("article", "Gao2021", BODY, evidence=None) == one_plain)

PACK = {
    "verdict": "evidence", "score": 0.93, "queries": ['"极小主义视域下的真谓词"'],
    "top": [{"title": "极小主义视域下的真谓词",
             "url": "https://read.cnki.net/web/Journal/Article/LJXJ202104003.html",
             "site_name": "知网", "authority": 4, "score": 0.93},
            {"title": "极小主义视域下的真谓词（PDF）",
             "url": "https://studiesinlogic.sysu.edu.cn/sites/default/files/x.pdf",
             "site_name": "中山大学逻辑与认知研究所", "authority": 3, "score": 0.81}],
    "n_results": 5, "error": "",
}
p_ev = refmark.entry_prompt("article", "Gao2021", BODY, evidence=PACK)
check("(a) 有证据：提示词出现证据块与完整 URL（不截断，模型要照抄进 `% 来源：`）",
      refmark.EVIDENCE_PROMPT_HEADER in p_ev
      and "https://read.cnki.net/web/Journal/Article/LJXJ202104003.html" in p_ev
      and "authority 4" in p_ev and "匹配 0.93" in p_ev, p_ev[-260:])
check("(a) 有证据：规则要求 URL 只能来自证据（防编造）",
      "不得编造" in p_ev and "% 来源" in p_ev)
MISSING = {"verdict": "missing", "score": 0.0, "top": [],
           "queries": ['"某文"', "某文 2020"], "error": "HTTP 500"}
p_mis = refmark.entry_prompt("article", "Gao2021", BODY, evidence=MISSING)
check("(a) 未查到：明确「按查不到处理」+ 回显查询 + 检索错误（不许拿记忆补齐）",
      "未检索到" in p_mis and "查不到" in p_mis and '"某文"' in p_mis
      and "HTTP 500" in p_mis)
check("(a) 有原文时证据块追加在原文之后（原文优先口径不变）",
      refmark.entry_prompt("article", "Gao2021", BODY, raw="[1] 原文…",
                           evidence=PACK).index("源稿原文")
      < refmark.entry_prompt("article", "Gao2021", BODY, raw="[1] 原文…",
                             evidence=PACK).index("自动检索证据"))

# ══ (b) `% 来源：` URL 白名单 ══════════════════════════════════════════════
print("\n── (b) `% 来源：` URL 白名单 ──")
GOOD = "https://read.cnki.net/web/Journal/Article/LJXJ202104003.html"
FAKE = "https://fake-journal.example.com/../made-up"
reply_ok = f"% 来源：{GOOD}（知网，2026-10-17 查）\n@article{{K,\n  title = {{T}}\n}}"
reply_bad = f"% 来源：{FAKE}\n@article{{K,\n  title = {{T}}\n}}"
check("(b) 从 `% 来源：` 行抽出 URL（忽略尾部标点/括号）",
      aibib.source_urls_in_reply(f"% 来源：{GOOD}。") == [GOOD])
check("(b) 不扫正文里的 URL（只认来源行——正文引用链接不算来源标注）",
      aibib.source_urls_in_reply(f"见 {FAKE}\n正文说明") == [])
check("(b) 有效 URL：通过（空列表）", aibib.bad_source_urls(reply_ok, {GOOD}) == [])
check("(b) 编造 URL：被识别",
      aibib.bad_source_urls(reply_bad, {GOOD}) == [FAKE])
check("(b) 末尾 `/` 差异视为同一来源",
      aibib.bad_source_urls(f"% 来源：{GOOD}/\n", {GOOD}) == [])
# 2026-10-17 真 API 验收修正：模型会写「同一来源的变体」——片段/查询/端口/目录，
# 字面比较会误伤真实来源（实测 7/22 被误伤，逐条回查都真在检索结果里）。
_BASE = "https://www.semanticscholar.org/paper/The-Truth-about-Deflationism-Soames/e37f4ffd"
check("(b) 片段差异（#1 / #analytics）视为同一来源",
      aibib.bad_source_urls(f"% 来源：{_BASE}\n", {_BASE + "#1"}) == [] and
      aibib.bad_source_urls(f"% 来源：{_BASE}#analytics#1\n", {_BASE}) == [])
check("(b) 查询参数差异视为同一来源",
      aibib.bad_source_urls("% 来源：https://www.pdcnet.org/pdc/bvdb.nsf/purchase26\n",
                            {"https://www.pdcnet.org/pdc/bvdb.nsf/purchase26?openform&fp=x"}) == [])
check("(b) 端口差异 / 只写到目录视为同一来源",
      aibib.bad_source_urls("% 来源：http://59.75.8.75\n",
                            {"http://59.75.8.75:8080/bookInfo_01h.html"}) == []
      and aibib.bad_source_urls("% 来源：https://read.cnki.net/\n",
                                {GOOD}) == [])
check("(b) 换域名 / 换路径**仍然**判为编造（归一不能放过真编造）",
      aibib.bad_source_urls("% 来源：https://evil.example.com/pdc/bvdb.nsf/purchase26\n",
                            {"https://www.pdcnet.org/pdc/bvdb.nsf/purchase26"})
      == ["https://evil.example.com/pdc/bvdb.nsf/purchase26"]
      and aibib.bad_source_urls("% 来源：https://www.pdcnet.org/pdc/bvdb.nsf/other\n",
                                {"https://www.pdcnet.org/pdc/bvdb.nsf/purchase26"})
      == ["https://www.pdcnet.org/pdc/bvdb.nsf/other"])
check("(b) 说明文字（不含 URL）放行——无法校验但保持旧行为",
      aibib.bad_source_urls("% 来源：WorldCat 书目页（未记下 URL）\n@book{K,}", set()) == [])
check("(b) 没有来源行 → 放行（没有可校验的 URL）",
      aibib.bad_source_urls("@article{K,\n  title = {T}\n}", set()) == [])
check("(b) allowed 为空但回复给了 URL → 视为编造（证据是「未查到」时尤其重要）",
      aibib.bad_source_urls(reply_bad, set()) == [FAKE])

# ══ (c) convert 端到端：检索 mock + chat mock ═══════════════════════════════
print("\n── (c) convert 端到端（双 mock）──")
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
URL_A = "https://read.cnki.net/web/Journal/Article/LJXJ202104003.html"
URL_FAKE = "https://fake-journal.example.com/made-up"


def _make_docx(dirpath: str) -> str:
    paras = ["论证据注入的接线测试", "高贝贝", "摘要：测试。", "关键词：测试"]
    paras += [f"正文第{i}段，讨论证据注入。" for i in range(12)]
    paras += ["参考文献",
              "[1] 高贝贝，“极小主义视域下的真谓词”，逻辑学研究，2021年第4期，第27-36页。",
              "[2] 张建军，逻辑悖论研究引论，北京：人民出版社，2014年。"]
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    path = os.path.join(dirpath, "demo.docx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml",
                   f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>')
    return path


class DualMock:
    """一个 server 同时扮 Search API（/v1/tools/search）与 chat（/chat/completions）。

    chat 的回复按提示词里的 key 决定：
      · `Gao2021` → 带**有效** `% 来源：`（URL_A）+ 补全字段（应被并入）；
      · `Zhang2014` → 带**编造** URL（应被白名单挡下，bib 原文不动）。
    """

    def __init__(self):
        self.search_prompts: list[str] = []
        self.chat_prompts: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path.endswith("/tools/search"):
                    q = body.get("query", "")
                    outer.search_prompts.append(q)
                    if "极小主义" in q or "高贝贝" in q:
                        rows = [{"title": "极小主义视域下的真谓词", "url": URL_A,
                                 "site_name": "知网", "authority": "4",
                                 "rerank_score": "1.95", "date": "2021-01-01",
                                 "summary": "高贝贝 逻辑学研究 2021年第4期 第27-36页",
                                 "snippet": "", "content": ""}]
                    else:
                        rows = []
                    payload = {"results": rows}
                elif self.path.endswith("/chat/completions"):
                    content = "".join(
                        (m.get("content") or "") for m in body.get("messages", [])
                        if m.get("role") == "user")
                    outer.chat_prompts.append(content)
                    if "Gao2021" in content:
                        reply = (
                            f"% 来源：{URL_A}\n"
                            "@article{Gao2021,\n"
                            "  author  = {高贝贝},\n"
                            "  title   = {极小主义视域下的真谓词},\n"
                            "  journal = {逻辑学研究},\n"
                            "  number  = {4},\n"
                            "  pages   = {27--36},\n"
                            "  year    = {2021}\n}"
                        )
                    else:
                        reply = (
                            f"% 来源：{URL_FAKE}\n"
                            "@book{Zhang2014,\n"
                            "  author    = {张建军},\n"
                            "  title     = {逻辑悖论研究引论},\n"
                            "  publisher = {人民出版社},\n"
                            "  address   = {北京},\n"
                            "  year      = {2014}\n}"
                        )
                    payload = {"choices": [{"message": {"content": reply}}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


_TEMPLATES = os.path.join(_REPO, "编辑笔记", "模板文件")
if not os.path.isdir(_TEMPLATES):
    SKIPS.append("模板目录不在本机（编辑笔记/模板文件），跳过 convert 端到端")
    print("SKIP  convert 端到端（模板目录不在本机）")
else:
    mock = DualMock()
    tmp = tempfile.mkdtemp(prefix="slcn p2 e2e ")
    try:
        docx = _make_docx(tmp)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([os.path.join(_ENGINE, "vendor"), _ENGINE])

        def _run(out_dir: str, extra: list[str]) -> subprocess.CompletedProcess:
            # ⚠️ 第 129 条起 `--verify-refs` 会在默认引用库存在时自动查库；本套件量的是
            #    「证据注入 + URL 白名单」，而合成稿里的条目（高贝贝 2021 / 陈波 2022 等）
            #    在本机种子库里可能已核实 → 会被跳过 AI 调用，改变 prompt/合并记录断言。
            #    显式 `--no-bibdb` 把引用库钉死为关（引用库接入另有 test_bibdb_verify.py）。
            return subprocess.run(
                [sys.executable, "convert.py", "--source", docx, "--out", out_dir,
                 "--verify-refs", "--llm-base", mock.base, "--llm-key", "k-test",
                 "--llm-model", "mock-chat", "--no-bibdb"] + extra,
                cwd=_ENGINE, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=240)

        out_on = os.path.join(tmp, "out_on")
        r_on = _run(out_on, ["--search-refs"])
        tb = glob.glob(os.path.join(out_on, "**", "*_文献核实.md"), recursive=True)
        bib = glob.glob(os.path.join(out_on, "**", "reference.bib"), recursive=True)
        md = open(tb[0], encoding="utf-8").read() if tb else ""
        bib_txt = open(bib[0], encoding="utf-8").read() if bib else ""
        print("    rc=", r_on.returncode)
        check("(c) convert rc=0、检索与 AI 都跑了", r_on.returncode == 0
              and mock.search_prompts and mock.chat_prompts,
              f"search={len(mock.search_prompts)} chat={len(mock.chat_prompts)}")
        check("(c) 逐条提示词里带证据块与证据 URL",
              all("自动检索证据" in p for p in mock.chat_prompts)
              and any(URL_A in p for p in mock.chat_prompts), str(mock.chat_prompts)[:200])
        check("(c) 有效来源的条目被并入（Gao2021 的 journal/pages 进了 bib）",
              "journal = {逻辑学研究}" in bib_txt and "pages" in bib_txt)
        check("(c) 编造 URL 的条目被挡下（Zhang2014 仍是原样：没有 address 变更/来源行）",
              "fake-journal.example.com" not in bib_txt
              and md.count("来源 URL") >= 0)
        check("(c) 任务书合并记录写明「未出现在本次检索结果」原因",
              "未出现在本次检索结果" in md and "换域名/换路径" in md, md[-400:])
        check("(c) 控制台打印白名单统计（通过/挡下）",
              "来源 URL 白名单" in r_on.stdout, r_on.stdout[-400:])
        check("(c) 编造 URL 的条目原文逐字节未动（没有 address 被补进 Zhang2014）",
              "address   = {北京}" in bib_txt or "address    = {北京}" in bib_txt)

        # 默认不开检索：提示词里没有证据块（回归：默认零行为变化）
        mock.chat_prompts.clear()
        mock.search_prompts.clear()
        out_off = os.path.join(tmp, "out_off")
        r_off = _run(out_off, [])
        check("(c) 不开 --search-refs：不发检索请求、提示词无证据块",
              r_off.returncode == 0 and not mock.search_prompts
              and all("自动检索证据" not in p for p in mock.chat_prompts),
              f"search={len(mock.search_prompts)}")
        check("(c) 不开 --search-refs：也会跑 AI（验证的是提示词差异，不是AI开关）",
              r_off.returncode == 0 and len(mock.chat_prompts) >= 2)
    finally:
        mock.close()
        shutil.rmtree(tmp, ignore_errors=True)

# ══ 汇总 ═══════════════════════════════════════════════════════════════════
print()
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("失败 0 项（全部通过）")
