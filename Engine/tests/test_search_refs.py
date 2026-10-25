#!/usr/bin/env python3.14
"""DeepSeek 检索证据（P1，第 71 条起）的离线判据测试。

背景（案例 117 / P0 报告）：DeepSeek 独立 Search API 对中文文献标题命中约 70%–80%，
结果自带 authority/rerank/date/summary；但 authority 单独没有判别力，所以要自己打分。
本模块（`slcn/search.py`）只做**检索证据**：不改模型提示词、不改转换产物；默认关闭，
不开启时转换产物与从前逐字节相同。

本套件守住（全部只打 127.0.0.1 上的本地假端点，**不联网**、不花额度）：
  (a) 端点解析：base 带/不带 `/v1` 都拼出官方 `/v1/tools/search`；配置优先级
      显式参数 > SLCN_SEARCH_* > SLCN_LLM_*；
  (b) 查询构造：题名清洗（LaTeX 宏/首尾引号/保留圆括号）；只对题名加引号；
      中文名 2–5 字、西文取姓、体例标记与译者不算作者；
  (c) 匹配打分：标题相似度是主判据；authority 高但题名不符 → 低分；域名层级与判词阈值；
  (d) HTTP 客户端：JSON 字符串字段转类型、运行内缓存、429 退避重试、4xx 不重试、
      空配置报错、费用估算；
  (e) 证据包：命中后不再发第二条 query；missing 时两条都发；错误不阻断；
  (f) 任务书渲染：有证据才出现「🔎 自动检索证据」，默认调用**逐字节不变**；
  (g) CLI：`python -m slcn.search --probe --json` 的机器可读输出；
  (h) 端到端接线：`convert.py --verify-refs` 默认**不碰网络**、任务书无证据；
      加 `--search-refs` 才打检索、任务书写证据（本地假端点，无模板/缺模板则 SKIP）。

不写 out/、不联网、不跑 corpus.py / build.sh。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_search_refs.py
"""
import contextlib
import io
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import refmark  # noqa: E402
from slcn import search  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══ 假端点 ══════════════════════════════════════════════════════════════════
class FakeSearch:
    """本地假 Search API：按剧本返回结果、记录请求；支持「先 429 再成功」。"""

    def __init__(self):
        self.requests: list[dict] = []
        self.paths: list[str] = []
        self.ignore_first = 0          # 前 N 次回 429
        self.fail_all = False          # 永远 500（测错误路径）
        self.results_for = None        # 可选：query → [results]，默认固定两条
        # 计数器
        self.n_search = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                outer.paths.append(self.path)
                outer.requests.append(body)
                if self.path.endswith("/tools/search"):
                    outer.n_search += 1
                    if outer.ignore_first > 0:
                        outer.ignore_first -= 1
                        self.send_response(429)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if outer.fail_all:
                        self.send_response(500)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    q = body.get("query", "")
                    rows = outer.results_for(q) if outer.results_for else [
                        {"title": "极小主义视域下的真谓词",
                         "url": "https://read.cnki.net/web/Journal/Article/LJXJ202104003.html",
                         "site_name": "知网", "authority": "4", "rerank_score": "1.95",
                         "date": "2021-01-01", "summary": "林小雨 逻辑学研究 2021年第4期 第27-36页",
                         "snippet": "", "content": ""},
                        {"title": "无关的候选 " + q[:6],
                         "url": "https://example.com/x", "site_name": "某站",
                         "authority": "2", "rerank_score": "1.0", "date": "",
                         "summary": "", "snippet": "", "content": ""},
                    ]
                    payload = {"results": rows}
                elif self.path.endswith("/tools/fetch"):
                    payload = {"title": "抓取页", "content": "正文" * 100}
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


# ══ (a) 端点解析与配置优先级 ════════════════════════════════════════════════
print("── (a) 端点解析 / 配置优先级 ──")
check("(a) base 以 /v1 结尾 → {base}/tools/search",
      search.resolve_endpoints("https://api.deepseek.com/v1")
      == ("https://api.deepseek.com/v1/tools/search", "https://api.deepseek.com/v1/tools/fetch"),
      str(search.resolve_endpoints("https://api.deepseek.com/v1")))
check("(a) base 不带 /v1 → {base}/v1/tools/search",
      search.resolve_endpoints("https://api.deepseek.com")
      == ("https://api.deepseek.com/v1/tools/search", "https://api.deepseek.com/v1/tools/fetch"))
check("(a) 末尾多余斜杠被吃掉",
      search.resolve_endpoints("http://127.0.0.1:9/")[0] == "http://127.0.0.1:9/v1/tools/search")
check("(a) 空 base → 空端点（调用方据此跳过，不瞎拼）",
      search.resolve_endpoints("") == ("", ""))

_old = {k: os.environ.get(k) for k in ("SLCN_SEARCH_BASE", "SLCN_LLM_BASE",
                                       "SLCN_SEARCH_KEY", "SLCN_LLM_KEY")}
try:
    os.environ.pop("SLCN_SEARCH_BASE", None)
    os.environ.pop("SLCN_SEARCH_KEY", None)
    os.environ["SLCN_LLM_BASE"] = "https://llm.example/v1"
    os.environ["SLCN_LLM_KEY"] = "k-llm"
    check("(a) 没给显式参数 → 回落 SLCN_LLM_BASE/KEY",
          search.config_from_env(None, None) == ("https://llm.example/v1", "k-llm"))
    os.environ["SLCN_SEARCH_BASE"] = "https://search.example/v1"
    os.environ["SLCN_SEARCH_KEY"] = "k-search"
    check("(a) SLCN_SEARCH_* 优先于 SLCN_LLM_*",
          search.config_from_env(None, None) == ("https://search.example/v1", "k-search"))
    check("(a) 显式参数优先于环境变量",
          search.config_from_env("https://explicit/v1", "k-explicit")
          == ("https://explicit/v1", "k-explicit"))
finally:
    for k, v in _old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

# ══ (b) 题名清洗 / 作者 / query 构造 ═══════════════════════════════════════
print("\n── (b) 题名清洗 / query 构造 ──")
check("(b) LaTeX 宏剥掉、内容保留（含结尾括号）",
      search.clean_title("逻辑悖论研究引论\\textup{（修订版）}") == "逻辑悖论研究引论（修订版）",
      repr(search.clean_title("逻辑悖论研究引论\\textup{（修订版）}")))
check("(b) 首尾全角引号/问号被剥掉",
      search.clean_title("“美诺悖论：知识何以拥有理性的保障?”") == "美诺悖论：知识何以拥有理性的保障",
      repr(search.clean_title("“美诺悖论：知识何以拥有理性的保障?”")))
check("(b) 书名号被剥、中间括号保留",
      search.clean_title("《逻辑哲学研究》") == "逻辑哲学研究"
      and search.clean_title("Truth (2th ed)") == "Truth (2th ed)")
check("(b) 连续空白压成一个空格", search.clean_title("a   b\n c") == "a b c")
check("(b) 中文作者取 2–5 字（亚里士多德不是 4 字截断）",
      search.author_tokens("亚里士多德（著），张竹明（译）") == ["亚里士多德"])
check("(b) 体例标记/译者不算作者",
      search.author_tokens("林小雨") == ["林小雨"]
      and search.author_tokens("Beall, J. C. and Restall, G.") == ["Beall"])
check("(b) 空作者 → 空列表（不因作者扣分）", search.author_tokens("") == [])
_q = search.build_queries({"title": "“美诺悖论”", "author": "李雷",
                           "year": "2020", "journal": "逻辑学研究"})
check("(b) q1 只对题名加引号（精确短语）", _q[0] == '"美诺悖论"', str(_q))
check("(b) q2 题名+作者+年份+刊名，整体不加引号",
      _q[1] == "美诺悖论 李雷 2020 逻辑学研究", _q[1] if len(_q) > 1 else "(缺)")
check("(b) 题名不可用 → 不构造 query（如实写未查到）",
      search.build_queries({"title": "   "}) == [])

# ══ (c) 域名层级 / 打分 / 判词 ══════════════════════════════════════════════
print("\n── (c) 域名层级 / 匹配打分 ──")
check("(c) 知网/出版社/高校/百科/文库/搜索引擎各归其层",
      (search.domain_tier("https://read.cnki.net/x")[0] == 1.0
       and search.domain_tier("https://global.oup.com/x")[0] == 1.0
       and search.domain_tier("https://logic.sysu.edu.cn/x")[0] == 0.9
       and search.domain_tier("https://baike.baidu.com/x")[0] == 0.3
       and search.domain_tier("https://www.taodocs.com/x")[0] == 0.3
       and search.domain_tier("https://www.baidu.com/s")[0] == 0.2
       and search.domain_tier("https://unknown.example/x")[0] == 0.2))
_f = {"title": "极小主义视域下的真谓词", "author": "林小雨", "year": "2021",
      "journal": "逻辑学研究"}
_good = {"title": "极小主义视域下的真谓词", "url": "https://read.cnki.net/x",
         "summary": "林小雨 逻辑学研究 2021年第4期 第27-36页"}
_bad = {"title": "完全无关的东西", "url": "https://high-authority.example/x",
        "summary": "authority 4 也没用"}
_s_good = search.score_result(_f, _good)
_s_bad = search.score_result(_f, _bad)
check("(c) 题名/作者/年份/刊名全命中 → 判证据（≥0.75）",
      _s_good["score"] >= 0.75 and search.verdict_for(_s_good["score"]) == "evidence",
      str(_s_good))
check("(c) 题名不符时，域名层级也救不回来（判 missing）",
      _s_bad["score"] < 0.55 and search.verdict_for(_s_bad["score"]) == "missing",
      str(_s_bad))
check("(c) 判词三档阈值：0.75/0.55",
      search.verdict_for(0.75) == "evidence" and search.verdict_for(0.74) == "check"
      and search.verdict_for(0.55) == "check" and search.verdict_for(0.54) == "missing")

# ══ (d) HTTP 客户端：类型转换 / 缓存 / 429 / 4xx / 费用 ══════════════════════
print("\n── (d) HTTP 客户端 ──")
fake = FakeSearch()
try:
    c = search.SearchClient(fake.base, "k-test", sleep_fn=lambda s: None)
    r = c.search("测试查询", count=5)
    check("(d) 正常检索：HTTP 200、两条结果", r["ok"] and r["http"] == 200 and len(r["results"]) == 2)
    check("(d) authority/rerank 字符串转成 int/float",
          r["results"][0]["authority"] == 4 and isinstance(r["results"][0]["rerank"], float),
          str((r["results"][0]["authority"], r["results"][0]["rerank"])))
    check("(d) 打到 {base}/v1/tools/search", fake.paths[-1] == "/v1/tools/search", fake.paths[-1])
    _n_before = fake.n_search
    c.search("测试查询", count=5)
    check("(d) 同一 query 第二次走缓存、不打网络",
          fake.n_search == _n_before and c.n_cached == 1, f"http={fake.n_search} cached={c.n_cached}")
    check("(d) 费用 = 成功次数 × ¥5/1000", abs(c.cost_estimate() - 0.005) < 1e-9,
          str(c.cost_estimate()))

    fake2 = FakeSearch()
    c2 = search.SearchClient(fake2.base, "k", sleep_fn=lambda s: None)
    fake2.ignore_first = 2
    r2 = c2.search("429重试")
    check("(d) 429 自动退避重试（前两次 429 后成功）",
          r2["ok"] and c2.n_429 == 2 and c2.n_ok == 1,
          f"ok={r2['ok']} 429={c2.n_429} okN={c2.n_ok}")

    fake3 = FakeSearch()
    fake3.fail_all = True
    c3 = search.SearchClient(fake3.base, "k", sleep_fn=lambda s: None)
    r3 = c3.search("服务端全错")
    check("(d) 5xx 重试后用尽 → ok=False、如实带错误（不假装成功）",
          not r3["ok"] and "HTTP" in (r3.get("error") or "") and fake3.n_search == 4,
          f"error={r3.get('error')} n={fake3.n_search}")
    fake3.close()

    check("(d) 空 base/空 key → 明确报错（不发请求）",
          not search.SearchClient("", "").search("x")["ok"]
          and not search.SearchClient("http://127.0.0.1:1", "").search("x")["ok"])
finally:
    fake.close()

# ══ (e) 证据包：三态 / 查询上限 / 错误不阻断 ═════════════════════════════════
print("\n── (e) 证据包 ──")
fake = FakeSearch()
try:
    bib = (
        "@article{Lin2021,\n  author = {林小雨}, title = {极小主义视域下的真谓词},\n"
        "  journal = {逻辑学研究}, year = {2021}, number = {4}, pages = {27--36}\n}\n"
        "@book{Zheng2014,\n  author = {郑建国}, title = {逻辑悖论研究引论},\n"
        "  publisher = {人民出版社}, address = {北京}, year = {2014}\n}\n"
    )
    c = search.SearchClient(fake.base, "k", sleep_fn=lambda s: None)
    logs = []
    ev = search.gather_evidence(bib, ["Lin2021", "Zheng2014"], c, log=logs.append)
    check("(e) 命中条目的判词与最佳分", ev["Lin2021"]["verdict"] == "evidence"
          and ev["Lin2021"]["score"] >= 0.75, str(ev["Lin2021"]["score"]))
    check("(e) 命中后不再发第二条 query；未命中才补第二条（每条至多 2 次）",
          len(ev["Lin2021"]["queries"]) == 1
          and 1 <= len(ev["Zheng2014"]["queries"]) <= 2,
          f"{ev['Lin2021']['queries']} | {ev['Zheng2014']['queries']}")
    check("(e) 证据包带 top≤3、URL 与 authority", len(ev["Lin2021"]["top"]) <= 3
          and ev["Lin2021"]["top"][0]["url"].startswith("https://read.cnki.net/")
          and ev["Lin2021"]["top"][0]["authority"] == 4)
    _allowed = ev["Lin2021"].get("allowed_urls") or []
    check("(e) 证据包另存「本次全部检索结果 URL」供白名单（含不在 top3 的结果）",
          isinstance(_allowed, list) and len(_allowed) >= 2
          and all(u.startswith("http") for u in _allowed)
          and any("example.com/x" in u for u in _allowed), str(_allowed)[:160])
    check("(e) 每条都有中文进度日志", len(logs) == 2 and all("检索 [" in x for x in logs), str(logs[:2]))
    check("(e) stats_summary 含次数/分布/费用",
          "检索" in search.stats_summary(c, ev) and "¥" in search.stats_summary(c, ev),
          search.stats_summary(c, ev))

    # 未查到 → 两条 query 都发；错误不阻断（bib 里有 title 不合法的条目）
    fake2 = FakeSearch()
    fake2.results_for = lambda q: []
    c2 = search.SearchClient(fake2.base, "k", sleep_fn=lambda s: None)
    ev2 = search.gather_evidence(bib, ["Lin2021"], c2)
    check("(e) 0 条结果 → missing，且依次试满两条 query",
          ev2["Lin2021"]["verdict"] == "missing" and len(ev2["Lin2021"]["queries"]) == 2
          and fake2.n_search == 2, f"{ev2['Lin2021']['queries']} n={fake2.n_search}")
    check("(e) missing 的证据包也带上用过的 query（回显给编辑）",
          ev2["Lin2021"]["queries"][0].startswith('"'))
    fake2.close()

    fake3 = FakeSearch()
    fake3.fail_all = True
    c3 = search.SearchClient(fake3.base, "k", sleep_fn=lambda s: None)
    ev3 = search.gather_evidence(bib, ["Lin2021"], c3)
    check("(e) 检索出错 → 仍返回 pack（missing + error），不抛异常阻断出稿",
          ev3["Lin2021"]["verdict"] == "missing" and "HTTP" in ev3["Lin2021"]["error"],
          ev3["Lin2021"]["error"][:60])
    fake3.close()
finally:
    fake.close()

# ══ (f) 任务书渲染：有证据才出现；默认调用逐字节不变 ═══════════════════════
print("\n── (f) 任务书渲染 ──")
BIB = (
    "@article{Lin2021,\n  author  = {林小雨},\n  title   = {极小主义视域下的真谓词},\n"
    "  journal = {逻辑学研究},\n  number  = {4},\n  year    = {2021},\n  pages   = {27--36}\n}\n"
)
base_md = refmark.build(BIB, "demo")
check("(f) 不传 evidence → 任务书里没有「自动检索证据」",
      "自动检索证据" not in base_md)
check("(f) 默认参数与显式 None/空 note 逐字节相同",
      refmark.build(BIB, "demo", evidence=None, evidence_note="") == base_md)
pack = {
    "verdict": "evidence", "score": 0.93,
    "top": [{"title": "极小主义视域下的真谓词",
             "url": "https://read.cnki.net/web/Journal/Article/LJXJ202104003.html",
             "site_name": "知网", "authority": 4, "score": 0.93}],
    "queries": ['"极小主义视域下的真谓词"'], "n_results": 5, "error": "",
}
md = refmark.build(BIB, "demo", evidence={"Lin2021": pack},
                   evidence_note="### 本次检索证据（自动，DeepSeek Search API）\n\n> 测试统计。")
check("(f) 有证据 → 出现「🔎 自动检索证据」与来源 URL 行",
      "🔎 **自动检索证据**" in md and "read.cnki.net" in md and "最佳匹配 0.93" in md)
check("(f) 证据行出现在该条目块内（位于 `### \\`Lin2021\\`` 之后）",
      md.index("🔎 **自动检索证据**") > md.index("### `Lin2021`"))
check("(f) 头部统计行写进任务书", "测试统计" in md)
missing_pack = {"verdict": "missing", "score": 0.0, "top": [], "queries": ['"某文"', "某文 2020"],
                "n_results": 0, "error": ""}
md2 = refmark.build(BIB, "demo", evidence={"Lin2021": missing_pack})
check("(f) 未查到 → 渲染「未查到」并回显用过的 query",
      "**未查到**" in md2 and '"某文"' in md2 and "某文 2020" in md2)
_extra = [ln for ln in md.splitlines() if ln not in base_md.splitlines()]
check("(f) 证据只在给 evidence 时出现（新增行全部属于证据块，没有别处变动）",
      _extra and any(ln.startswith("- 🔎") for ln in _extra)
      and all(ln == "" or ln.startswith(("### 本次检索证据", "> ", "- 🔎", "  - "))
              for ln in _extra),
      str(_extra[:4]))

# ══ (g) CLI：--probe --json 机器可读 ════════════════════════════════════════
print("\n── (g) CLI 探针 --json ──")
fake = FakeSearch()
try:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = search._main(["--probe", "--base", fake.base, "--key", "k-test",
                           "--query", "逻辑学研究", "--json"])
    line = buf.getvalue().strip().splitlines()[-1]
    data = json.loads(line)
    check("(g) --probe --json 输出合法 JSON 且 ok=True", rc == 0 and data.get("ok") is True, line[:120])
    check("(g) JSON 里带 endpoint/http/n_results/cost",
          data.get("endpoint") == fake.base + "/v1/tools/search" and data.get("http") == 200
          and data.get("n_results", 0) > 0 and "cost_yuan" in data, line[:160])
    # 未配置时：退出码 2 + 明确错误（测试环境不能真联网，base 为空即走纯配置错误路径）
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc2 = search._main(["--probe", "--json", "--base", "", "--key", ""])
    d2 = json.loads(buf2.getvalue().strip().splitlines()[-1])
    check("(g) 未配置 → rc=2 且错误可读", rc2 == 2 and "没配置" in d2.get("error", ""), str(d2)[:120])
finally:
    fake.close()

# ══ (h) 端到端接线：convert.py --search-refs（子进程 + 本地假端点）══════════
print("\n── (h) convert.py 端到端接线 ──")
import glob            # noqa: E402
import shutil          # noqa: E402
import subprocess      # noqa: E402
import tempfile        # noqa: E402
import zipfile         # noqa: E402
from xml.sax.saxutils import escape  # noqa: E402

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_docx(dirpath: str) -> str:
    """只含 word/document.xml 的最小 docx（与 test_refmark_raw_refs.py 同法）。"""
    paras = ["论检索证据的接线测试", "林小雨", "摘要：本文测试检索证据。", "关键词：检索；证据"]
    paras += [f"正文第{i}段，讨论检索证据与参考文献核实。" for i in range(12)]
    paras += [
        "参考文献",
        "[1] 林小雨，“极小主义视域下的真谓词”，逻辑学研究，2021年第4期，第27-36页。",
        "[2] 郑建国，逻辑悖论研究引论，北京：人民出版社，2014年。",
    ]
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    path = os.path.join(dirpath, "demo.docx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml",
                   f'<w:document xmlns:w="{_W_NS}"><w:body>{ps}</w:body></w:document>')
    return path


_TEMPLATES = os.path.join(os.path.dirname(_ENGINE), "编辑笔记", "模板文件")
if not os.path.isdir(_TEMPLATES):
    SKIPS.append("模板目录不在本机（编辑笔记/模板文件），跳过 convert 端到端")
    print("SKIP  convert.py 端到端（模板目录不在本机）")
else:
    fake = FakeSearch()
    tmp = tempfile.mkdtemp(prefix="slcn search e2e ")
    try:
        docx = _make_docx(tmp)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [os.path.join(_ENGINE, "vendor"), _ENGINE])
        # 批61 修复（task-34，**测试 hermetic**）：本机存在真实笔记仓 `bib_db/` 时，
        # `convert.py --verify-refs` 会先查引用库；fixture 里的引用一旦被判 safe 就按
        # 设计**跳过检索**（n_search=0），于是 (h) 的断言随机器而变（CI 无笔记仓不触发）。
        # 这里把子进程的 SLCN_NOTES 指向「空笔记仓」（有 cases/、无 bib_db/）→ 固定为
        # CI 的**无库口径**；同时清掉继承来的 SLCN_* 旋钮，避免本机配置再改变行为。
        for _key in [k for k in env if k.startswith("SLCN_")]:
            env.pop(_key, None)
        _empty_notes = os.path.join(tmp, "notes_empty")
        os.makedirs(os.path.join(_empty_notes, "cases"), exist_ok=True)
        env["SLCN_NOTES"] = _empty_notes

        def _run(out_dir: str, extra: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, "convert.py", "--source", docx, "--out", out_dir,
                 "--verify-refs"] + extra,
                cwd=_ENGINE, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)

        out_off = os.path.join(tmp, "out_off")
        r_off = _run(out_off, ["--llm-base", fake.base, "--llm-key", "k-test"])
        check("(h) 默认不检索：convert rc=0 且**一次检索请求都没发**",
              r_off.returncode == 0 and fake.n_search == 0,
              f"rc={r_off.returncode} n_search={fake.n_search}")
        _tb_off = glob.glob(os.path.join(out_off, "**", "*_文献核实.md"), recursive=True)
        check("(h) 默认任务书生成、且不含「自动检索证据」（默认零行为变化）",
              len(_tb_off) == 1
              and "自动检索证据" not in open(_tb_off[0], encoding="utf-8").read())

        out_on = os.path.join(tmp, "out_on")
        r_on = _run(out_on, ["--search-refs", "--llm-base", fake.base,
                             "--llm-key", "k-test"])
        _tb_on = glob.glob(os.path.join(out_on, "**", "*_文献核实.md"), recursive=True)
        md_on = open(_tb_on[0], encoding="utf-8").read() if _tb_on else ""
        check("(h) 加 --search-refs：convert rc=0、确实打了检索",
              r_on.returncode == 0 and fake.n_search > 0,
              f"rc={r_on.returncode} n_search={fake.n_search}")
        check("(h) 日志里能看到「⑪·检索」与统计（次数/费用）",
              "⑪·检索" in r_on.stdout and "预估费用" in r_on.stdout,
              r_on.stdout[-400:] if r_on.returncode else "")
        check("(h) 任务书里出现证据行（来源 URL）",
              "🔎 **自动检索证据**" in md_on and "read.cnki.net" in md_on)
        check("(h) 转换产物（tex）不受检索影响：两次都生成了 .tex",
              bool(glob.glob(os.path.join(out_off, "**", "*.tex"), recursive=True))
              and bool(glob.glob(os.path.join(out_on, "**", "*.tex"), recursive=True)))
    finally:
        fake.close()
        shutil.rmtree(tmp, ignore_errors=True)

# ══ 汇总 ═══════════════════════════════════════════════════════════════════
print()
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("失败 0 项（全部通过）")
