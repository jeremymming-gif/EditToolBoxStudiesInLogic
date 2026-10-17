#!/usr/bin/env python3.14
"""DeepSeek Search API 文献检索证据（P1，2026-10-17 用户拍板接入，第 71 条起）。

本模块只做一件事：**给「文献核实」这条链路提供可审计的检索证据**。
它**不**改模型提示词、**不**改转换产物、**不**依赖模型的内置检索工具——
所以它不受「服务商/模型支不支持 web_search」影响，也不需要 `responses` 通道。

为什么要有它（P0 实测，案例 117 / `/tmp/p0_search/P0-REPORT.md`）：
  · DeepSeek 独立 Search API（`POST {base}/v1/tools/search`）对中文文献的标题命中
    约 70%–80%，平均 0.7 秒/次、¥5/1000 次成功；结果自带 `authority` / `rerank_score` /
    `date` / `summary`，`need_content` 还能给网页正文；
  · 但 `authority` 与 `rerank` **单独没有判别力**（94/100 条 authority≥3），真正的判据是
    「标题相似度 + 作者/年份/刊名命中 + 域名层级」——所以本模块自己算分，**不照抄元数据**；
  · 内置 `web_search` 只有 `deepseek-v4-pro` 真检索、且单次 14k–57k token（贵而慢），
    只适合当可选捷径（P2/UI 层再决定要不要暴露）。

三条纪律（与项目其它模块一致）
--------------------------------
1. **默认关闭**：`convert.py --verify-refs --search-refs`（或环境变量 `SLCN_SEARCH_REFS=1`）
   才跑；不开启时产物与从前**逐字节相同**（任务书/日志一个字段都不多）。
2. **不许假装**：检索失败、0 条结果、匹配分太低，一律如实写「未查到」，并**回显用过的
   query**——让编辑能分辨「query 烂」还是「文献真的不在」。证据只是**线索**，仍须人工核对。
3. **零新依赖**：只用标准库 `urllib` + `slcn.bibparse`。测试全走本地假端点，**不联网**。

隐私：检索的 `query` 由文献题名/作者/刊名拼成，会发到 DeepSeek 搜索服务——与
「把条目原文发给模型」同级别的外发，设置页/文档必须写明（GUIDE §3.8）。
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── 计费与限速（官方 2026-10-17：Search / Fetch 各 ¥5 / 1000 次成功请求、QPS 各 5）──
COST_PER_1000_SUCCESS = 5.0
MIN_INTERVAL_S = 0.25          # QPS 5 → 理论 0.2s；留 25% 余量
DEFAULT_COUNT = 5
DEFAULT_TIMEOUT = 40
MAX_RETRIES = 3                # 429/5xx 最多再试 3 次
RETRY_BACKOFF_S = (1.5, 3.0, 6.0)

#: 证据判词（写进任务书的那一层三态；`missing` 不是「文献不存在」，只是「没搜到证据」）。
VERDICT_CN = {
    "evidence": "检索到来源（仍须人工核对字段）",
    "check": "有候选来源，需人工判断",
    "missing": "未查到（检索证据不足，请人工查证或交给联网 AI）",
}
VERDICT_EVIDENCE = 0.75        # P0 标定：≥0.75 视为「检索到」
VERDICT_CHECK = 0.55           # 0.55–0.75 视为「有候选、需人工判断」

#: 打分为「证据」时最多保留几条候选（任务书里一行一条）。
TOP_N = 3
SUMMARY_MAX = 1200             # 单条 summary 截断（P0 实测均值 1110 字）
SNIPPET_MAX = 300


# ═══════════════════════════════════════════════════════════════════════════
# 配置：base / key 复用 LLM 那一套（也允许 SLCN_SEARCH_BASE / SLCN_SEARCH_KEY 单独覆盖）
# ═══════════════════════════════════════════════════════════════════════════

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in _TRUTHY


def refs_enabled_from_env() -> bool:
    """`SLCN_SEARCH_REFS=1` 是否要求跑检索证据（与命令行 `--search-refs` 等价）。"""
    return _truthy(os.environ.get("SLCN_SEARCH_REFS"))


def config_from_env(raw_base: str | None = None, raw_key: str | None = None) -> tuple[str, str]:
    """解析检索用的 (base, key)。

    优先级：显式参数 > `SLCN_SEARCH_*` > `SLCN_LLM_*`（通常同一家服务商、同一个密钥）。
    绝不打印/落盘密钥。返回的 base 可能为空（调用方据此跳过并给中文提示）。
    """
    base = (raw_base or "").strip() or (os.environ.get("SLCN_SEARCH_BASE") or "").strip() \
        or (os.environ.get("SLCN_LLM_BASE") or "").strip()
    key = (raw_key or "").strip() or (os.environ.get("SLCN_SEARCH_KEY") or "").strip() \
        or (os.environ.get("SLCN_LLM_KEY") or "").strip()
    return base, key


def resolve_endpoints(base: str) -> tuple[str, str]:
    """由 API base 推出 (search_url, fetch_url)。

    规则（2026-10-17 实测：带/不带 `/v1` 的 base 都常见，官方端点固定是 `/v1/tools/...`）：
      · base 已以 `/v1` 结尾（如 `https://api.deepseek.com/v1`）→ 直接 `{base}/tools/search`；
      · 否则（如 `https://api.deepseek.com`）→ `{base}/v1/tools/search`。
    两轮 P0 都实际打通，HTTP 200。
    """
    b = (base or "").strip().rstrip("/")
    if not b:
        return "", ""
    if b.endswith("/v1"):
        return b + "/tools/search", b + "/tools/fetch"
    return b + "/v1/tools/search", b + "/v1/tools/fetch"


# ═══════════════════════════════════════════════════════════════════════════
# 查询构造：把一条 bib 条目变成最多 2 个 query（P0 标定）
# ═══════════════════════════════════════════════════════════════════════════

#: 文献题名里常见的 LaTeX 宏（`\textup{（修订版）}` 之类）——**直接带进 query 会杀命中**。
_LATEX_MACRO_RE = re.compile(r"\\(?:textup|textit|textbf|textrm|textsf|texttt|text|emph|mathrm|mathit|mathbf|mbox)\s*\{([^{}]*)\}")
_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+\*?(?:\s*\{[^{}]*\})?")
#: 题名首尾要剥掉的「包装」标记：引号/书名号/空白/句读。**只剥首尾，不动中间**；
#: ⚠️ 不剥圆括号/方括号——它们常常是题名的一部分（如「（修订版）」「(2th ed)」），
#:    从「《…》」里剥掉一个 `）` 会把题名截成半截（自检时踩到）。
_EDGE_PUNCT = "“”‘’\"'「」『』《》〈〉.,，。;；:：!！?？、·—–-‐_ \t\r\n\u3000"


def clean_title(title: str) -> str:
    """清洗题名，供「精确短语」查询用（第 71 条，P0 教训）。

    P0 实测两类失败都源于没清洗：
      · `逻辑悖论研究引论\\textup{（修订版）}` 带着 LaTeX 宏去搜 → 召回极差；
      · `“美诺悖论：知识何以拥有理性的保障?”` 首字符是全角引号，再套一层查询引号 →
        检索语法被破坏。
    清洗范围：LaTeX 宏 → 内容、去掉 `$`/`{}`、剥首尾标点引号、压缩空白。**不改中间的字。**
    """
    t = (title or "").strip()
    t = _LATEX_MACRO_RE.sub(r"\1", t)
    t = _LATEX_CMD_RE.sub("", t)
    t = t.replace("$", "").replace("{", "").replace("}", "")
    t = t.strip(_EDGE_PUNCT)
    t = re.sub(r"\s+", " ", t).strip()
    return t


#: 作者里的体例标记（`（著）/（译）/（主编）` 等）不算作者名。
_ROLE_MARK_RE = re.compile(r"[（(]\s*(?:著|译|主编|编|校|整理|编译|编著|合著|等)\s*[)）]")


def author_tokens(author: str) -> list[str]:
    """从 `author` 字段取「用来判断命中」的作者串（最多 2 个）。

    为什么不用全名：bib 里可能是 `亚里士多德（著），张竹明（译）`（**译者不是作者**）、
    也可能是 `Beall, J. C. and Restall, G.`。这里只取第一作者的核心串：
      · 中文 → 头 2–4 个汉字（「高贝贝」「张建军」；省略体例标记与译者）；
      · 西文 → `姓, 名` 的第一个 token / `名 姓` 的最后一个 token。
    取不到就返回空列表（不因此扣分）。
    """
    a = _ROLE_MARK_RE.sub(" ", author or "")
    a = re.split(r"[，,;；、]\s*", a.strip())[0]
    a = a.strip()
    if not a:
        return []
    if re.search(r"[\u4e00-\u9fff]", a):
        # 中文名 2–5 字（含「亚里士多德」这种 5 字音译名）；更长就只取前 5 字。
        name = re.match(r"[\u4e00-\u9fff]{2,5}", a)
        return [name.group(0)] if name else []
    if "," in a:                      # 西文「姓, 名」→ 姓
        return [a.split(",", 1)[0].strip()] if a.split(",", 1)[0].strip() else []
    parts = a.split()
    return [parts[-1]] if parts else []


def build_queries(fields: dict) -> list[str]:
    """一条文献 → 最多 2 个检索 query（第一条失败/低分才用第二条）。

    q1：`"清洗后的题名"`（只对**题名**加引号 = 精确短语；P0 实测把整串都加引号会杀召回）。
    q2：题名 + 第一作者 + 年份 + 刊名/出版社（**整体不加引号**，提高召回）。
    两条都为空时返回空列表（调用方写「未查到」并说明「题名不可用」）。
    """
    title = clean_title(fields.get("title", ""))
    if not title:
        return []
    q1 = f'"{title}"'
    extras: list[str] = []
    extras += author_tokens(fields.get("author", ""))
    for k in ("year", "journal", "publisher", "booktitle"):
        v = (fields.get(k) or "").strip()
        if v and v not in extras:
            extras.append(v)
    q2 = " ".join([title] + extras) if extras else ""
    return [q for q in (q1, q2) if q]


# ═══════════════════════════════════════════════════════════════════════════
# 匹配打分：P0 标定（authority/rerank 不单用；标题相似度是主判据）
# ═══════════════════════════════════════════════════════════════════════════

#: 域名层级（0–1）：官方/一手来源 > 书目/学术平台 > 百科/文库 > 搜索引擎/其它。
#: ⚠️ 这是一张**判据表**，改动必须有真实稿件对照，别凭印象调（见 RULES：判据不可随便改）。
_DOMAIN_RULES: list[tuple[str, float, str]] = [
    # 中文文献库（知网/万方/维普及其镜像）
    (r"(^|\.)cnki\.(net|com|cn)$|(^|\.)cnki\.com\.cn$|cbpt\.cnki\.net$", 1.0, "知网"),
    (r"(^|\.)wanfangdata\.com$|(^|\.)wanfang\.com$", 1.0, "万方"),
    (r"(^|\.)cqvip\.com$", 1.0, "维普"),
    # DOI / 书目库
    (r"(^|\.)doi\.org$|(^|\.)crossref\.org$", 1.0, "DOI/Crossref"),
    (r"(^|\.)worldcat\.org$", 1.0, "WorldCat"),
    # 出版社/期刊官方站
    (r"(^|\.)oup\.com$|(^|\.)cambridge\.org$|(^|\.)taylorfrancis\.com$|"
     r"(^|\.)tandfonline\.com$|(^|\.)springer\.com$|(^|\.)wiley\.com$|"
     r"(^|\.)jstor\.org$|(^|\.)sagepub\.com$|(^|\.)degruyter\.com$|"
     r"(^|\.)brill\.com$|(^|\.)nature\.com$|(^|\.)science\.org$|"
     r"(^|\.)annualreviews\.org$|(^|\.)press\.", 1.0, "出版社/期刊官网"),
    # 高校域（很多期刊官网/机构库/图书馆书目页在 .edu.cn 下）
    (r"\.edu\.cn$|\.ac\.cn$|\.edu$", 0.9, "高校"),
    (r"\.(lib|opac|catalog)\.|(^|\.)opac\.|(^|\.)lib\.", 0.9, "图书馆书目"),
    # 学术平台/书目索引（可靠但非一手）
    (r"(^|\.)philpapers\.org$|(^|\.)plato\.stanford\.edu$|(^|\.)dblp\.|semanticscholar\.org$|"
     r"(^|\.)ci\.nii\.ac\.jp$|(^|\.)scholarmate\.com$|(^|\.)researchgate\.net$|"
     r"(^|\.)books\.google\.", 0.7, "学术平台/书目索引"),
    (r"(^|\.)aisixiang\.com$|(^|\.)chinacaj\.net$|(^|\.)articles\.|(^|\.)ndhx\.net$", 0.5, "二手转载"),
    # 百科 / 文库 / 电商（参考价值低，不能当唯一依据）
    (r"(^|\.)baike\.|(^|\.)wiki\.cnki\.com\.cn$|(^|\.)zhihu\.com$|(^|\.)douban\.com$",
     0.3, "百科/社区"),
    (r"(^|\.)taodocs\.com$|(^|\.)book118\.com$|(^|\.)docin\.com$|(^|\.)max\.book118\.com$|"
     r"(^|\.)dangdang\.com$|(^|\.)jd\.com$", 0.3, "文库/电商"),
    (r"(^|\.)baidu\.com$|(^|\.)bing\.com$|(^|\.)sogou\.com$|(^|\.)so\.com$", 0.2, "搜索引擎"),
]


def domain_tier(url: str) -> tuple[float, str]:
    """URL → (层级权重 0–1, 中文类别)。取**最具体**的规则（列表靠前者优先）。"""
    try:
        host = urllib.parse.urlparse(url or "").netloc.lower()
    except ValueError:
        host = ""
    if not host:
        return 0.2, "其它"
    if host.startswith("www."):
        host = host[4:]
    for pat, weight, name in _DOMAIN_RULES:
        if re.search(pat, host):
            return weight, name
    return 0.2, "其它"


def _norm_text(s: str) -> str:
    """比较用的归一：去空白/标点/大小写（保留汉字与字母数字）。"""
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _title_sim(a: str, b: str) -> float:
    """两个题名的相似度（0–1）：归一后 `SequenceMatcher`；一方包含另一方时取 0.95。"""
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) >= 6 and (na in nb or nb in na):
        return 0.95
    return difflib.SequenceMatcher(None, na, nb).ratio()


def score_result(fields: dict, row: dict) -> dict:
    """给一条检索结果打分（0–1）并保留判据明细，供任务书/日志解释「为什么算命中」。

    权重（P0 标定，写死在这里，改动需真实稿件对照）：
      0.55 × 题名相似度 + 0.15 × 作者命中 + 0.10 × 年份命中
      + 0.10 × 刊名/出版社命中 + 0.10 × 域名层级
    「命中」= 该字段出现在结果的 title/summary/snippet 里（凡出现即 1，不做次数加权）。
    """
    text = " ".join(str(row.get(k) or "") for k in ("title", "summary", "snippet"))
    title = row.get("title") or ""
    sim = _title_sim(fields.get("title", ""), title)
    tokens = author_tokens(fields.get("author", ""))
    a_hit = 1.0 if any(_norm_text(t) and _norm_text(t) in _norm_text(text) for t in tokens) else 0.0
    y = (fields.get("year") or "").strip()
    y_hit = 1.0 if y and str(y) in text else 0.0
    j = (fields.get("journal") or fields.get("publisher") or fields.get("booktitle") or "").strip()
    j_hit = 1.0 if j and _norm_text(j[:12]) and _norm_text(j[:12]) in _norm_text(text) else 0.0
    tier, category = domain_tier(row.get("url", ""))
    score = 0.55 * sim + 0.15 * a_hit + 0.10 * y_hit + 0.10 * j_hit + 0.10 * tier
    return {
        "score": round(score, 3), "title_sim": round(sim, 3),
        "author_hit": bool(a_hit), "year_hit": bool(y_hit), "journal_hit": bool(j_hit),
        "tier": tier, "category": category,
    }


def verdict_for(score: float) -> str:
    if score >= VERDICT_EVIDENCE:
        return "evidence"
    if score >= VERDICT_CHECK:
        return "check"
    return "missing"


# ═══════════════════════════════════════════════════════════════════════════
# HTTP 客户端：限速、429 退避、运行内缓存、计数与费用
# ═══════════════════════════════════════════════════════════════════════════

def _int_or_none(v: object) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _float_or_none(v: object) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _clean_result(raw: dict) -> dict:
    """官方 JSON 里 `authority` / `rerank_score` 是**字符串**（P0 实测）——这里统一转型。"""
    return {
        "title": str(raw.get("title") or ""),
        "url": str(raw.get("url") or ""),
        "site_name": str(raw.get("site_name") or ""),
        "authority": _int_or_none(raw.get("authority")),
        "rerank": _float_or_none(raw.get("rerank_score")),
        "date": str(raw.get("date") or ""),
        "snippet": str(raw.get("snippet") or ""),
        "summary": str(raw.get("summary") or ""),
        "content": str(raw.get("content") or ""),
    }


class SearchClient:
    """DeepSeek Search API 客户端（只用标准库；可注入 sleep/时钟，便于离线测试）。

    `sleep_fn` / `now_fn` 注入是给测试用的：断言限速与退避**不真的等**。
    """

    def __init__(self, base: str, key: str = "", *,
                 timeout: float = DEFAULT_TIMEOUT,
                 min_interval: float = MIN_INTERVAL_S,
                 sleep_fn=time.sleep, now_fn=time.monotonic) -> None:
        self.search_url, self.fetch_url = resolve_endpoints(base)
        self.base = (base or "").strip().rstrip("/")
        self.key = key or ""
        self.timeout = timeout
        self.min_interval = min_interval
        self._sleep = sleep_fn
        self._now = now_fn
        self._last_call = 0.0
        self._cache: dict[tuple[str, int, bool], dict] = {}
        # 计数（费用与覆盖率的唯一出处）
        self.n_http = 0
        self.n_ok = 0
        self.n_cached = 0
        self.n_429 = 0
        self.n_error = 0
        self.latencies: list[float] = []

    # ── 配置是否可用 ──────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self.search_url and self.key)

    def cost_estimate(self) -> float:
        """预估费用（元）：按**成功请求次数**计（¥5/1000 次；失败/无结果不收费）。"""
        return round(self.n_ok * COST_PER_1000_SUCCESS / 1000.0, 4)

    # ── 底层 POST ─────────────────────────────────────────────────────────
    def _post(self, url: str, payload: dict) -> tuple[int, dict | str, float]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", "Bearer " + self.key)
        req.add_header("Content-Type", "application/json")
        t0 = self._now()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                text = r.read().decode("utf-8", "replace")
                code = r.status
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            code = e.code
        except Exception as e:  # noqa: BLE001  网络层任何异常都归「没测出来」，不假装
            text = f"__EXC__ {type(e).__name__}: {e}"
            code = -1
        return code, text, self._now() - t0

    def _throttle(self) -> None:
        """两次请求之间至少间隔 `min_interval` 秒（账号级 QPS 5，留余量）。"""
        if self._last_call:
            wait = self.min_interval - (self._now() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._now()

    # ── 公开接口：search ──────────────────────────────────────────────────
    def search(self, query: str, count: int = DEFAULT_COUNT,
               need_content: bool = False) -> dict:
        """一次检索。返回 `{ok, http, results, error, cached, latency, raw}`。

        · 429 / 5xx：按 `RETRY_BACKOFF_S` 退避重试（最多 `MAX_RETRIES` 次）；
        · 4xx 其它：不重试（配置/权限问题，重试只会烧额度）；
        · 同一 `(query, count, need_content)` 在本 Client 生命周期内**只打一次**。
        """
        q = (query or "").strip()
        ck = (q, int(count), bool(need_content))
        if not q:
            return {"ok": False, "http": 0, "results": [], "error": "空 query",
                    "cached": False, "latency": 0.0, "raw": ""}
        if ck in self._cache:
            hit = dict(self._cache[ck])
            hit["cached"] = True
            self.n_cached += 1
            return hit
        if not self.search_url:
            return {"ok": False, "http": 0, "results": [], "error": "未配置检索地址（base 为空）",
                    "cached": False, "latency": 0.0, "raw": ""}
        if not self.key:
            return {"ok": False, "http": 0, "results": [], "error": "未配置 API Key",
                    "cached": False, "latency": 0.0, "raw": ""}
        payload = {"query": q, "count": int(count)}
        if need_content:
            payload["need_content"] = True
        last: dict = {}
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            self.n_http += 1
            code, text, dt = self._post(self.search_url, payload)
            self.latencies.append(round(dt, 3))
            if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                if code == 429:
                    self.n_429 += 1
                self._sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
                last = {"ok": False, "http": code, "results": [], "latency": round(dt, 3),
                        "error": f"HTTP {code}（将重试）", "raw": text[:300]}
                continue
            if code == 200:
                try:
                    data = json.loads(text)
                except ValueError:
                    self.n_error += 1
                    return {"ok": False, "http": code, "results": [], "latency": round(dt, 3),
                            "error": "响应不是合法 JSON", "cached": False, "raw": text[:300]}
                rows = [_clean_result(r) for r in (data.get("results") or [])]
                out = {"ok": True, "http": code, "results": rows, "error": "",
                       "cached": False, "latency": round(dt, 3), "raw": ""}
                self.n_ok += 1
                self._cache[ck] = out
                return dict(out)
            # 非 200：记录、不缓存（下次可能成功）、不重试（4xx）或已重试完
            self.n_error += 1
            return {"ok": False, "http": code, "results": [], "error": f"HTTP {code}: {text[:300]}",
                    "cached": False, "latency": round(dt, 3), "raw": text[:300]}
        self.n_error += 1
        return {"ok": False, "http": last.get("http", 0), "results": [],
                "error": last.get("error", "重试次数用尽"), "cached": False,
                "latency": last.get("latency", 0.0), "raw": last.get("raw", "")}

    # ── 公开接口：fetch（P1 只做能力预留，暂不接进证据包）──────────────────
    def fetch(self, url: str) -> dict:
        """抓取单个网页正文（Markdown）。与 search 同一套限速/重试/计数。

        P1 **不调用**它（P0 实测中文库反爬严重、部分官网 404，证据包先用 search 的
        summary/snippet）；留着是为了 P2 的「深读官网页」与 UI 探针复用同一份实现。
        """
        if not self.fetch_url:
            return {"ok": False, "http": 0, "content": "", "error": "未配置检索地址"}
        last_err = ""
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            self.n_http += 1
            code, text, dt = self._post(self.fetch_url, {"url": url})
            self.latencies.append(round(dt, 3))
            if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                self._sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
                last_err = f"HTTP {code}"
                continue
            if code == 200:
                try:
                    data = json.loads(text)
                except ValueError:
                    self.n_error += 1
                    return {"ok": False, "http": code, "content": "", "error": "响应不是合法 JSON"}
                self.n_ok += 1
                return {"ok": True, "http": code, "content": str(data.get("content") or ""),
                        "title": str(data.get("title") or ""), "error": ""}
            self.n_error += 1
            return {"ok": False, "http": code, "content": "", "error": f"HTTP {code}: {text[:200]}"}
        self.n_error += 1
        return {"ok": False, "http": 0, "content": "", "error": f"重试用尽（{last_err}）"}


# ═══════════════════════════════════════════════════════════════════════════
# 证据包：bib 条目 → 每条一份可审计的证据（convert.py ⑪ 调它拿结果）
# ═══════════════════════════════════════════════════════════════════════════

def entry_fields(bib_text: str) -> dict[str, dict]:
    """把 bib 文本解析成 `{key: 字段 dict}`（供匹配打分）。

    ⚠️ 用 `bibparse.parse_entries` 而不是 `bibcheck.split_entries`：后者给的是原始 body
    文本，字段要再解析一遍；前者已经压平字段（含 `year`/`journal`/`publisher` 等）。
    解析失败/重复键都以「后出现的为准」——证据只是线索，不值得为它中止整条链路。
    """
    from . import bibparse
    out: dict[str, dict] = {}
    try:
        entries, _findings = bibparse.parse_entries(bib_text or "")
    except Exception:  # noqa: BLE001  纯附加通道，解析失败就当没有字段
        return out
    for e in entries:
        try:
            fields = {k: (v or "").strip() for k, v in (e.fields or {}).items()}
        except AttributeError:
            continue
        if e.key:
            out[e.key] = fields
    return out


def gather_evidence(bib_text: str, keys: list[str], client: SearchClient,
                    *, log=None, count: int = DEFAULT_COUNT) -> dict[str, dict]:
    """为 `keys` 里的每条文献检索证据。返回 `{key: pack}`（含 verdict/top/查询/耗时）。

    `pack` 结构（任务书渲染与测验都以此为准）：
      `{"verdict": "evidence|check|missing", "score": 0.93, "queries": [...],
        "top": [{title,url,site_name,authority,rerank,date,summary,snippet,score,title_sim,
                 author_hit,year_hit,journal_hit,tier,category}], "n_results": int,
        "error": ""}`

    · 第一条 query 的最高分若 ≥ `VERDICT_CHECK`，**不再发第二条**（省额度）；
    · 任何一条 query 出错都继续（如实进 pack.error），不阻断整轮；
    · 单条文献最多 2 次查询（P0 标定的召回/成本平衡）。
    """
    fields_by_key = entry_fields(bib_text)
    out: dict[str, dict] = {}
    n = len(keys)
    for i, key in enumerate(keys, 1):
        f = fields_by_key.get(key) or {}
        queries = build_queries(f)
        pack: dict = {"verdict": "missing", "score": 0.0, "queries": [],
                      "top": [], "n_results": 0, "error": ""}
        if not queries:
            pack["error"] = "题名不可用（解析不出 title），无法构造检索 query"
            out[key] = pack
            if log:
                log(f"  检索 [{i}/{n}] {key} — {pack['error']}")
            continue
        collected: dict[str, dict] = {}
        best_rows: list[dict] = []
        query_used: list[str] = []
        for qi, q in enumerate(queries):
            res = client.search(q, count=count)
            query_used.append(q)
            if not res["ok"]:
                pack["error"] = res.get("error", "")
                continue
            scored = []
            for row in res["results"]:
                s = score_result(f, row)
                merged = dict(row)
                merged.update(s)
                scored.append(merged)
            scored.sort(key=lambda r: r["score"], reverse=True)
            best_rows.extend(scored[:TOP_N])
            for r in scored:
                if r["url"] and r["url"] not in collected:
                    collected[r["url"]] = r
            pack["n_results"] = max(pack["n_results"], len(res["results"]))
            # 第一条 query 已经有「可判断」的候选 → 不再发第二条（省额度）
            if scored and scored[0]["score"] >= VERDICT_CHECK:
                break
        pack["queries"] = query_used
        if best_rows:
            best_rows.sort(key=lambda r: r["score"], reverse=True)
            # 同一 URL 只留一次；最多 TOP_N 条
            seen: set[str] = set()
            top: list[dict] = []
            for r in best_rows:
                u = r.get("url") or ""
                if u in seen:
                    continue
                seen.add(u)
                top.append(r)
                if len(top) >= TOP_N:
                    break
            pack["top"] = top
            pack["score"] = top[0]["score"]
            pack["verdict"] = verdict_for(pack["score"])
        out[key] = pack
        if log:
            verdict = VERDICT_CN.get(pack["verdict"], pack["verdict"])
            hit = f"『{pack['top'][0]['title'][:40]}』" if pack["top"] else "（无结果）"
            log(f"  检索 [{i}/{n}] {key} — {verdict}（最佳 {pack['score']:.2f}）{hit}")
    return out


def stats_summary(client: SearchClient, evidence: dict[str, dict]) -> str:
    """一行可审计的检索统计（写进日志/任务书头部）：次数、命中分布、预估费用。"""
    n_ev = sum(1 for p in evidence.values() if p["verdict"] == "evidence")
    n_ck = sum(1 for p in evidence.values() if p["verdict"] == "check")
    n_ms = sum(1 for p in evidence.values() if p["verdict"] == "missing")
    return (f"检索 {client.n_ok} 次成功 / {client.n_http} 次请求"
            f"（缓存命中 {client.n_cached}、429 {client.n_429}、失败 {client.n_error}）；"
            f"证据分布：检索到 {n_ev}、需判断 {n_ck}、未查到 {n_ms}；"
            f"预估费用 ¥{client.cost_estimate():.4f}")


# ═══════════════════════════════════════════════════════════════════════════
# CLI：连接/覆盖自测（P1 验收与 P2 界面探针共用）
# ═══════════════════════════════════════════════════════════════════════════

def _main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m slcn.search",
        description="DeepSeek Search API 自测（不读稿件、不写文件；只打一次检索）。")
    ap.add_argument("--probe", action="store_true", help="做一次最小检索，验证地址/密钥/返回形状")
    ap.add_argument("--query", default="逻辑学研究", help="自测用的 query（默认「逻辑学研究」）")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--base", default=None, help="API 地址（也可用 SLCN_SEARCH_BASE / SLCN_LLM_BASE）")
    ap.add_argument("--key", default=None, help="API 密钥（也可用 SLCN_SEARCH_KEY / SLCN_LLM_KEY；⚠️ 别写进 argv）")
    ap.add_argument("--json", action="store_true", help="stdout 只输出一行 JSON（供界面调用）")
    a = ap.parse_args(argv)

    base, key = config_from_env(a.base, a.key)
    res: dict = {"ok": False, "endpoint": "", "http": 0, "n_results": 0, "cost_yuan": 0.0,
                 "error": "", "detail": ""}
    if not base:
        res["error"] = "没配置 API 地址（--base 或 SLCN_SEARCH_BASE / SLCN_LLM_BASE）"
    elif not key:
        res["error"] = "没配置 API Key（--key 或 SLCN_SEARCH_KEY / SLCN_LLM_KEY）"
    else:
        c = SearchClient(base, key)
        res["endpoint"] = c.search_url
        r = c.search(a.query, count=a.count)
        res["http"] = r["http"]
        res["n_results"] = len(r["results"])
        res["ok"] = bool(r["ok"] and r["results"])
        res["cost_yuan"] = c.cost_estimate()
        res["error"] = "" if res["ok"] else (r.get("error") or "检索成功但 0 条结果")
        if r["results"]:
            top = r["results"][0]
            res["detail"] = f"{top['title']} — {top['url']}"
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        verdict = "✅ 可用" if res["ok"] else "❌ 不可用"
        print(f"DeepSeek Search 自测：{verdict}")
        print(f"  端点：{res['endpoint'] or '（未配置）'}")
        print(f"  HTTP：{res['http']}  结果数：{res['n_results']}  预估费用：¥{res['cost_yuan']}")
        if res["detail"]:
            print(f"  首条：{res['detail']}")
        if res["error"]:
            print(f"  错误：{res['error']}")
    return 0 if res["ok"] else (1 if res["http"] == 200 else 2)


if __name__ == "__main__":
    sys.exit(_main())
