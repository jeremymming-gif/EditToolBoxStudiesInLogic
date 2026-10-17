#!/usr/bin/env python3
r"""本地 OpenAI 兼容「假端点」——给 GUI 验收用，**只用标准库、只绑 127.0.0.1**。

为什么要这个东西
----------------
工具箱新加的两条 AI 通道（「设置」页的「测试连接 / 体检」、以及「初排转换」的
「用 AI 处理参考文献」）此前**只做过编译验证与 harness 验证，从没真正对着任何端点跑过**。
要人工点一遍，就必须有一个端点：

  · **离线**：验收不能依赖外网、不能花编辑部的额度，也不能把稿件内容发出去；
  · **确定性**：每个模式对应一种「端点会怎么坏」的情形，才能一条条核对 GUI 的反应
    （截断、内容过滤、字段不被支持、连不上、超时……）；
  · **可对照**：GUI 里看到的东西与服务器端看到的请求/回复必须能一一对上，
    否则「GUI 真的调用了这个端点吗」这个问题没法回答。

所以本文件把这三件事做全：**只绑 127.0.0.1**（外部网络永远碰不到它，它也不主动外联）、
**模式可运行时切换**（改一个 curl 就能换剧本，不用重启 GUI）、**每个请求打一行中文日志**
（人拿 GUI 的输出与这行日志对）。

用法（最短）
------------
    python3 tools/mock_llm_server.py              # 监听 127.0.0.1:8799
    python3 tools/mock_llm_server.py --port 8801  # 换端口
    python3 tools/mock_llm_server.py --mode length --slow-seconds 20

    # 夹具回放（练「AI 结果并入 reference.bib」那条通道）：
    python3 tools/mock_llm_server.py --replies tools/fixtures/merge_replies.json

    # 运行中换剧本（GUI 不用重启；换完再点一次「体检」/「转换」即可）
    curl -s -XPOST 127.0.0.1:8799/__mode -d '{"mode":"length"}'
    curl -s 127.0.0.1:8799/__mode        # 看当前剧本
    curl -s 127.0.0.1:8799/__stats       # 看「GUI 到底打了几次、打的什么」

GUI 里怎么填（「设置」页）
--------------------------
    API 地址  http://127.0.0.1:8799/v1      ← 注意带 /v1，本工具会自动补 /chat/completions
    API 密钥  任意（本 mock 不校验；留空也行，引擎会发 "Bearer none"）
    模型名    mock-model                    ← 或 deepseek-flash，两个都在 /v1/models 里

模式一览（每个模式**故意模拟一种坏法**，注释里写它练的是 GUI 的哪条分支）
------------------------------------------------------------------------
    normal      正常回答，usage 里 completion_tokens_details.reasoning_tokens > 0
                → 练「设置」页 ③「当前是思考模式」；练「初排转换」逐条 tokens 行**带**「（含推理 r）」
    stop        正常回答，reasoning_tokens = 0
                → 练 ③「非思考模式」；练逐条 tokens 行**不带**推理后缀（对照 normal）
    length      finish_reason="length" + **半截 bibtex**
                → 练「初排转换」的截断告警（stdout 一行 + 任务书里那条 `> ⚠️ 本条 AI 返回被截断`）
                  ——这是「假完整比明显缺失更危险」那条纪律的现场复现
    content_filter                   finish_reason="content_filter"
    insufficient_system_resource     finish_reason="insufficient_system_resource"
                → 两者都练「非 stop 也要标注」：任务书里出现 `未正常结束——被内容过滤`
                  / `未正常结束——服务端资源不足被中断`（从前只盯 length，这两种会静默通过）
    reject_json     请求体里出现 response_format → HTTP 400
                → 练「设置」页 ④ 的降级显示（`✗ 不支持（HTTP 400）`＋原始错误体）；
                  也练引擎侧 json_mode_rejected() 的判据（只认 400 且文案含 json/response_format）
    reject_thinking 请求体里出现 thinking → HTTP 400
                → 练引擎侧的**一次性降级**：打印一行「端点不支持 thinking 字段…本条改为不带该字段重试」，
                  然后本次运行余下条目都不再发该字段，**照样出 AI 建议**（不因可选参数全灭）
    slow            先睡 --slow-seconds 秒再正常回答
                → 练超时路径：Swift 体检每个请求 15s 超时（`✗ 连接失败/超时`），
                  引擎侧默认 60s 超时；把秒数开到 20+ 就能稳定复现
    http500         直接 HTTP 500 + OpenAI 形状的错误体
                → 练服务故障：体检 ② 报 `✗ HTTP 500`；转换侧连续 3 条失败后**提前收手**
                  并明说「本次只出任务书」（AI 失败不影响出稿）
    replay      **夹具回放**：按请求里的 bib key 回 `--replies` 夹具里写好的那段文本
                （2026-10-15 加，为「AI 结果并入 reference.bib」这条通道验收）。
                夹具里没有这个 key 时**故意不编假条目**，只回一句话——引擎侧会把它记成
                「回复里没有可用条目」，绝不会污染 reference.bib。
                为什么要有它：真实端点每次回来的东西都不一样，「并进去了哪几条、跳过了
                哪几条」就无从逐条核对；夹具把「好的 / 带待核实注释的 / 改了 key 的 /
                垃圾回复」四种情形钉死，验收才能复现（见 `tools/fixtures/README.md`）。

    ── 以下三个模式服务 **Responses 通道**（`POST /v1/responses`，2026-10-15 加）──
    responses_search          回 `web_search_call` + `url_citation`（真·支持检索的端点）。
                              探针应当报 **支持**；引擎跑完应当打印「检索证据 N 个」。
                              ⚠️ 第 70 条起工具名可配（`SLCN_LLM_SEARCH_TOOL`）：本模式
                              **收到任何非空 `tools` 都回证据**（它扮演的就是「你发什么
                              检索工具我就认什么」的端点），所以换成 `web_search_preview`
                              也照样判支持——判据是证据，不是名字。
    responses_ignore_search   **收到 `tools` 也当没看见**，回 200 但**不给** `web_search_call`
                              —— 这是 DeepSeek **flash** 的真实行为（官方兼容性表把
                              `web_search` 列为「忽略」，且「不支持的参数会被静默忽略、不会报错」；
                              2026-10-17 起同厂 `deepseek-v4-pro` 会真检索）。
                              探针必须据此报 **不支持**；这是本 mock 最重要的一条剧本，
                              没有它，「静默忽略被误报成支持」这个 bug 就无法回归。
    responses_reject_search   请求里带 `tools` 就 **HTTP 400**（端点明确拒绝），
                              探针必须报 **不支持** 并把原始错误体展示出来。

token 数为什么是**写死的**
--------------------------
验收要能心算：N 条条目 → 收尾那行必须是 `共 N 次调用，120N prompt + 40N completion tokens
（含推理 12N）`。真模型每次的 token 数都不一样，那种「差不多对」的对照等于没对照。
写死之后再叠**两条**调整规则，两条都只看请求本身，所以结果照样可以预测：

  ① `thinking={"type":"disabled"}` → 推理记 0，并从 completion 里扣掉（40−12=28）。
     这一条让「设置」页那个三态开关**可证**：关掉之后逐条 tokens 行不再有「（含推理 r）」，
     开着（或不发该字段）则有。`stop` 模式同样按「非思考」算，专门用来对照两种行。
  ② 请求里给了更小的 `max_tokens` 就夹一次：体检 ② 的 `max_tokens=8` 不会回出
     40 token 这种自相矛盾的数，而 ⑪ 的 `max_tokens=2048` 不会被夹，验收算术保持干净。

⚠️ 本文件**不联网**（除监听本机回环外没有任何出站请求），也不读任何配置/密钥。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── 只绑回环：写死，不提供 --host ────────────────────────────────────────────
#
# 为什么连一个 --host 参数都不给：验收脚本一旦能被指到 0.0.0.0，就等于在编辑部
# 内网上开了一个「不校验密钥、谁都能调」的假端点。少一个开关就少一条走错的路。
HOST = "127.0.0.1"
DEFAULT_PORT = 8799

# ── 写死的 token 数（理由见文件头「token 数为什么是写死的」）─────────────────
_BASE_PROMPT_TOKENS = 120
_BASE_COMPLETION_TOKENS = 40
_BASE_REASONING_TOKENS = 12     # 推理 token（思考模式）；算在 completion 里（按输出计费）

MODELS = ["mock-model", "deepseek-flash"]

MODES = (
    "normal", "stop", "length", "content_filter", "insufficient_system_resource",
    "reject_json", "reject_thinking", "slow", "http500", "replay",
    # ── Responses 通道（2026-10-15 加，练「真联网检索 / 静默忽略 / 明确拒绝」三条路）──
    "responses_search", "responses_ignore_search", "responses_reject_search",
)

_MODE_HINT = {
    "normal": "正常回答；推理 token 随 thinking 字段变（关了 → 0，且 completion 28）",
    "stop": "正常回答，强制 reasoning=0（专门对照逐条 tokens 行的两种写法）",
    "length": "finish_reason=length + 半截 bibtex（截断告警）",
    "content_filter": "finish_reason=content_filter（内容过滤也要标注）",
    "insufficient_system_resource": "finish_reason=insufficient_system_resource（服务端资源不足）",
    "reject_json": "带 response_format 就 HTTP 400（练 ④ 的降级显示）",
    "reject_thinking": "带 thinking 就 HTTP 400（练引擎侧一次性降级）",
    "slow": "先睡 N 秒再回答（练超时路径）",
    "http500": "HTTP 500（练服务故障：连败 3 条提前收手）",
    "replay": "按 --replies 的夹具**逐键回放**（练「AI 结果并入 reference.bib」那条通道）",
    "responses_search": "POST /v1/responses：真回 web_search_call + url_citation"
                        "（练「端点支持检索 → 探针报支持」）",
    "responses_ignore_search": "POST /v1/responses：**收到 tools 也当没看见**，"
                               "回 200 但不给 web_search_call"
                               "（练 DeepSeek flash 的「静默忽略」→ 探针必须报不支持）",
    "responses_reject_search": "POST /v1/responses：请求里带 tools 就 HTTP 400"
                               "（练「端点明确拒绝」→ 探针报不支持 + 原始错误体）",
}

# 收到的请求体里若有这个形状，就把 key 抄进回复，方便人对着任务书逐条核对
_KEY_RE = re.compile(r"@\s*([A-Za-z][\w:+-]*)\s*\{\s*([^,\s}]+)")


class State:
    """进程内共享的可变状态（模式 + 计数 + 最近日志）。多线程下用一把锁护住。"""

    def __init__(self, mode: str, slow_seconds: float) -> None:
        self.lock = threading.Lock()
        self.mode = mode
        self.slow_seconds = slow_seconds
        self.n_requests = 0
        self.n_by_path: dict[str, int] = {}
        self.n_by_finish: dict[str, int] = {}
        self.log_lines: list[str] = []
        # replay 模式的夹具（请求里的 key → 一段完整的 AI 回复文本），见 load_replies()
        self.replies: dict[str, str] = {}
        self.replies_path = ""
        self.n_replay_hit = 0
        self.n_replay_miss = 0
        # 最近收到的请求里那条 **user 消息**（引擎 ⑪ 的提示词本体），只留内存、不落盘。
        # 为什么要留：验收「引擎到底发了什么」不能只看 n_requests 与 token 数——
        # 2026-10-15 第 68 条要证明「⑪ 的请求里带了源稿原文」，唯一诚实的证据就是
        # 端点**实际收到的字节**（夹具回复是造好的，不能当证据）。每条截断到 4000 字符、
        # 只留最近 20 条，长时间跑不会吃内存。
        #
        # 第 69 条起每条还带 `protocol` / `tools` / `web_search`：验收要证明「开关真的
        # 把 `tools:[{"type":"web_search"}]` 发出去了」，而这件事只有端点收到的字节能作证。
        self.recent_prompts: list[dict] = []
        # responses 通道收到的工具类型计数（`{"web_search": N}`）——一眼看出工具到底发没发。
        self.n_tools_requested: dict[str, int] = {}

    def note(self, line: str) -> None:
        """打一行日志并留一份（/__stats 要回给人看，人不必去翻终端回滚缓冲）。"""
        with self.lock:
            self.log_lines.append(line)
            del self.log_lines[:-200]     # 只留最近 200 行，长时间跑不会吃内存
        print(line, flush=True)


STATE: State = State("normal", 3.0)


def load_replies(path: str) -> dict[str, str]:
    """读 `--replies <json>` 夹具：`{请求里的 bib key: 一段 AI 回复原文}`。

    为什么夹具要按**key**索引：引擎 ⑪ 是**逐条**问的（prompt 里带 `@类型{key,`），
    所以「哪条回复对应哪条文献」这件事在真实端点下也只能由 key 决定；
    夹具按同一个键索引，回放出来的东西才与真端点同构
    （包括「这一条被改名」「这一条是垃圾」「这一条带 `% ⚠ 待核实` 注释」这些病变）。
    可选的 `"__missing__"` 键：请求的 key 不在夹具里时回它（不写就走 normal 的答案）。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("顶层必须是对象：{bib key: AI 回复文本}")
    out: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(f"键与值都必须是字符串（问题项：{k!r}）")
        out[k] = v
    if not out:
        raise ValueError("夹具是空的")
    return out


def _tokens(max_tokens: int | None, reasoning_on: bool) -> tuple[int, int, int]:
    """(prompt, completion, reasoning)。两条规则，都只依赖请求本身，结果照样可预测：

    ① `thinking={"type":"disabled"}` 时推理 token 记 0，**并从 completion 里扣掉**
       （官方文档 2026-10：推理 token 按**输出**计费，也就是算在 completion 里）。
       为什么让它随 thinking 变：「设置」页那个三态开关只有这样才**在 mock 日志与
       逐条 tokens 行里都看得见** —— 关掉思考模式之后，逐条那行不再出现「（含推理 r）」。
       否则人没有任何办法确认那个开关真的传到了端点（GUI 自己不会显示它发了什么）。
    ② 请求里给了更小的 `max_tokens` 就夹一下：体检 ② 的 `max_tokens=8` 不会回出
       40 token 这种自相矛盾的数，而 ⑪ 的 2048 不会被夹，验收算术保持干净。
    """
    reasoning = _BASE_REASONING_TOKENS if reasoning_on else 0
    # 推理算在输出里：推理少多少，completion 就少多少（真端点也是这个口径）
    completion = _BASE_COMPLETION_TOKENS - (_BASE_REASONING_TOKENS - reasoning)
    if isinstance(max_tokens, int) and max_tokens > 0:
        completion = min(completion, max_tokens)
        reasoning = min(reasoning, completion)
    return _BASE_PROMPT_TOKENS, completion, reasoning


def _request_key(user: str) -> str:
    """从请求的 prompt 里取出这条文献的 bib key（引擎 ⑪ 的 prompt 形如 `@article{key,`）。"""
    m = _KEY_RE.search(user or "")
    return m.group(2) if m else ""


def _answer_text(user: str, mode: str) -> str:
    """把请求里那条条目抄成一份「建议」。刻意标着 mock —— 别让人当真建议看。"""
    m = _KEY_RE.search(user or "")
    etype = m.group(1) if m else "article"
    key = m.group(2) if m else "unknown"
    head = "% mock_llm_server：本条回复由**本地假端点**生成，仅用于 GUI 验收，不是真实核实结果\n"
    if mode == "length":
        # 半截：模拟 max_tokens 把输出切在字段值中间（真端点不报错，只把 finish_reason 标 length）
        return (head
                + f"@{etype}{{{key},\n"
                + "  author    = {示例作者 and 另一位作者},\n"
                + "  title     = {示例题名，这一行故意没写完就被 max_tokens 切")
    if mode == "content_filter":
        return head + f"@{etype}{{{key},\n  %% 正文被端点安全过滤拦下，字段未生成\n"
    if mode == "insufficient_system_resource":
        return head + f"@{etype}{{{key},\n  %% 端点资源不足，生成被中断\n"
    return (head
            # 第 69 条：任务书第三节第 11 条要求「每条核实过的条目上方写 `% 来源：`」。
            # mock 也照这个形状回一行 —— 否则「来源标注能不能穿过合并进到
            # reference.bib」这条就永远只有单元测试、没有端到端证据。
            # ⚠️ 用保留域 `.invalid`，一眼就看得出**这是假的**，不会被人当成真来源。
            + "% 来源：https://example.invalid/mock-source（mock 假来源，仅用于验收）\n"
            + f"@{etype}{{{key},\n"
            + "  author    = {示例作者},\n"
            + "  title     = {示例题名（mock）},\n"
            + "  journal   = {示例刊名},\n"
            + "  volume    = {1},\n"
            + "  number    = {1},\n"
            + "  pages     = {1--2},\n"
            + "  year      = {2026},\n"
            + "  userf     = {cn}\n"
            + "}\n")


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 + 每次都写 Content-Length：URLSession 才能复用连接，
    # 「体检」那种连发 3 个请求的场景不会因为半开连接卡住。
    protocol_version = "HTTP/1.1"
    server_version = "mock-llm/1.0"

    # ── 基础设施 ─────────────────────────────────────────────────────────

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        """屏蔽 BaseHTTPRequestHandler 的默认日志：本文件的日志格式是给验收看的。"""
        return

    def _peer(self) -> str:
        """调用方地址。**只可能是 127.0.0.1**（服务器只绑回环）——
        把它打进日志，「GUI 真的调用了本 mock」与「没有任何外部主机参与」就同时有了证据。"""
        return self.client_address[0] if self.client_address else "?"

    def _send(self, code: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str, param: str | None = None) -> None:
        """OpenAI 形状的错误体：GUI 的 `原始错误：` 那一行会原样显示它。"""
        self._send(code, {"error": {
            "message": message,
            "type": "invalid_request_error" if code == 400 else "server_error",
            "param": param,
            "code": None,
        }})

    def _read_body(self) -> bytes:
        """**一定**读完请求体（哪怕后面要报错）：不读完会污染 keep-alive 上的下一个请求。"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        return self.rfile.read(n) if n > 0 else b""

    # ── 路由 ─────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/v1/models":
            with STATE.lock:
                STATE.n_requests += 1
                STATE.n_by_path[path] = STATE.n_by_path.get(path, 0) + 1
                mode = STATE.mode
            STATE.note(f"[req #{STATE.n_requests}] GET {path} ← {self._peer()} → 200"
                       f" 返回 {len(MODELS)} 个模型"
                       f"（当前模式 {mode}；本端点只是尽力而为，不提供也不算失败）")
            self._send(200, {"object": "list", "data": [{"id": m, "object": "model",
                                                         "owned_by": "mock"} for m in MODELS]})
            return
        if path == "/__mode":
            with STATE.lock:
                cur, slow = STATE.mode, STATE.slow_seconds
            self._send(200, {"mode": cur, "slow_seconds": slow, "modes": list(MODES),
                             "hint": _MODE_HINT.get(cur, "")})
            return
        if path == "/__stats":
            with STATE.lock:
                self._send(200, {"mode": STATE.mode, "slow_seconds": STATE.slow_seconds,
                                 "n_requests": STATE.n_requests,
                                 "n_by_path": dict(STATE.n_by_path),
                                 "n_by_finish": dict(STATE.n_by_finish),
                                 "replies_fixture": STATE.replies_path,
                                 "n_replies": len(STATE.replies),
                                 "n_replay_hit": STATE.n_replay_hit,
                                 "n_replay_miss": STATE.n_replay_miss,
                                 # responses 通道收到的工具类型计数：`{"web_search": N}`。
                                 # 这就是「`tools:[{"type":"web_search"}]` 真的发出去了吗」的证据
                                 # ——引擎的开关状态不是证据，端点收到的字节才是。
                                 "n_tools_requested": dict(STATE.n_tools_requested),
                                 # 端点**实际收到**的 user 消息（最近 20 条，每条截断 4000 字符）：
                                 # 用于证明「引擎的请求里到底带了什么」（如第 68 条的源稿原文、
                                 # 第 69 条的 protocol/tools）。
                                 "last_user_prompts": list(STATE.recent_prompts),
                                 "log": list(STATE.log_lines[-40:])})
            return
        if path == "/":
            self._send(200, {"service": "mock_llm_server（本地 OpenAI 兼容假端点）",
                             "endpoints": ["GET /v1/models", "POST /v1/chat/completions",
                                           "POST /v1/responses",
                                           "GET|POST /__mode", "GET /__stats"],
                             "modes": list(MODES),
                             "replies_fixture": STATE.replies_path,
                             "bind": f"{HOST}:{self.server.server_port}"})
            return
        with STATE.lock:
            STATE.n_requests += 1
        STATE.note(f"[req #{STATE.n_requests}] GET {path} ← {self._peer()} → 404（本 mock 只服务 /v1/models、"
                   "/v1/chat/completions、/v1/responses 与 /__* 控制面）")
        self._error(404, f"未知路径 {path}（mock 只实现了 /v1/models、"
                         "/v1/chat/completions 与 /v1/responses）")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        raw = self._read_body()

        if path == "/__mode":
            self._set_mode(raw)
            return
        if path not in ("/v1/chat/completions", "/v1/responses"):
            with STATE.lock:
                STATE.n_requests += 1
            STATE.note(f"[req #{STATE.n_requests}] POST {path} ← {self._peer()} → 404")
            self._error(404, f"未知路径 {path}（mock 只实现了 /v1/models、"
                             "/v1/chat/completions 与 /v1/responses）")
            return

        try:
            req = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(req, dict):
                raise ValueError("顶层不是对象")
        except Exception as e:  # noqa: BLE001
            with STATE.lock:
                STATE.n_requests += 1
            STATE.note(f"[req #{STATE.n_requests}] POST {path} ← {self._peer()} → 400 请求体不是合法 JSON（{e}）")
            self._error(400, f"请求体不是合法 JSON：{e}")
            return
        if path == "/v1/responses":
            self._responses(path, req)
        else:
            self._chat(path, req)

    # ── 控制面 ───────────────────────────────────────────────────────────

    def _set_mode(self, raw: bytes) -> None:
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:  # noqa: BLE001
            self._error(400, f"控制请求体不是合法 JSON：{e}")
            return
        mode = (body or {}).get("mode")
        if mode not in MODES:
            self._error(400, f"未知模式 {mode!r}；可用：{'、'.join(MODES)}", param="mode")
            return
        if mode == "replay" and not STATE.replies:
            # replay 没有夹具就什么都不会回：宁可当场 400 说清楚，
            # 也不要让人对着「空回复」猜是不是引擎坏了。
            self._error(400, "replay 模式需要启动时加 --replies <夹具.json>"
                             "（本次启动没有夹具）", param="mode")
            return
        with STATE.lock:
            old = STATE.mode
            STATE.mode = mode
            if isinstance(body.get("slow_seconds"), (int, float)):
                STATE.slow_seconds = float(body["slow_seconds"])
            slow = STATE.slow_seconds
        STATE.note(f"[ctl] 模式切换 {old} → {mode}（{_MODE_HINT[mode]}；slow_seconds={slow}）")
        self._send(200, {"ok": True, "mode": mode, "slow_seconds": slow,
                         "hint": _MODE_HINT[mode]})

    # ── 主通道 ───────────────────────────────────────────────────────────

    def _record_prompt(self, key: str, model: str, user: str,
                       protocol: str, tools: list) -> None:
        """记下端点**实际收到**的这次请求（只留内存）。

        `tools` 是请求里 `tools` 数组的**类型名列表**（如 `["web_search"]`）——
        验收要证明「开关真的把工具发出去了」，这是唯一的证据来源（引擎的开关状态不是证据）。
        ⚠️ 第 70 条起工具名**可配置**（`SLCN_LLM_SEARCH_TOOL`），所以这里的 `web_search`
        只表示「打的**正好是那个默认名字**」；「有没有带工具」请看 `tools` 列表是否非空
        （或 `n_tools_requested` 里出现了什么名字）。
        """
        with STATE.lock:
            STATE.recent_prompts.append({
                "key": key, "model": model, "user": user[:4000],
                "protocol": protocol, "tools": list(tools),
                "web_search": "web_search" in tools,
            })
            del STATE.recent_prompts[:-20]
            for t in tools:
                STATE.n_tools_requested[t] = STATE.n_tools_requested.get(t, 0) + 1

    def _chat(self, path: str, req: dict) -> None:
        with STATE.lock:
            STATE.n_requests += 1
            n = STATE.n_requests
            STATE.n_by_path[path] = STATE.n_by_path.get(path, 0) + 1
            mode = STATE.mode
            slow = STATE.slow_seconds

        model = req.get("model", "")
        has_rf = "response_format" in req
        has_th = "thinking" in req
        has_mt = "max_tokens" in req
        mt = req.get("max_tokens") if isinstance(req.get("max_tokens"), int) else None
        user = ""
        msgs = req.get("messages")
        if isinstance(msgs, list):
            for m in msgs:
                if isinstance(m, dict) and m.get("role") == "user":
                    user = str(m.get("content") or "")
        # 请求特征先打出来：人就是靠这一行回答「GUI 到底发了什么字段」
        feat = (f"model={model!r} response_format={'有' if has_rf else '无'}"
                f" thinking={json.dumps(req['thinking'], ensure_ascii=False) if has_th else '无'}"
                f" max_tokens={mt if has_mt else '无'}")
        # chat 通道没有服务端工具（`tools` 在 chat 里是 function calling，须客户端执行），
        # 所以第三、四个参数固定是 "chat" / []。
        self._record_prompt(_request_key(user), model, user, "chat", [])
        if mode == "slow" and slow > 0:
            STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 慢速模式，先睡 {slow}s 再回答"
                       "（练超时：Swift 体检每请求 15s、引擎默认 60s）")
            time.sleep(slow)

        if mode == "reject_json" and has_rf:
            STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 400 拒绝了 response_format")
            self._error(400, "Unrecognized request argument supplied: response_format"
                             "（mock 的 reject_json 模式）", param="response_format")
            return
        if mode == "reject_thinking" and has_th:
            STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 400 拒绝了 thinking")
            self._error(400, "Unknown parameter: 'thinking'（mock 的 reject_thinking 模式）",
                        param="thinking")
            return
        if mode == "http500":
            STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 500 服务故障（mock 的 http500 模式）")
            self._error(500, "mock 的 http500 模式：端点故障，请稍后再试")
            return

        finish = {"length": "length", "content_filter": "content_filter",
                  "insufficient_system_resource": "insufficient_system_resource"}.get(mode, "stop")
        # thinking 关掉 → 推理 token 记 0（见 _tokens 的说明）。判据只看请求里那个字段的 type，
        # 与引擎 slcn/llm.py 的 `_THINKING_OFF` 口径一致：`{"type":"disabled"}` 就是关。
        th = req.get("thinking")
        thinking_off = isinstance(th, dict) and str(th.get("type", "")).lower() == "disabled"
        # stop 模式也是「非思考」：用它对照「有推理」与「没推理」两种逐条 tokens 行
        reasoning_on = (mode != "stop") and not thinking_off
        prompt_t, completion_t, reasoning_t = _tokens(mt, reasoning_on)
        if mode == "replay":
            # 夹具回放（2026-10-15 加）：按请求里的 bib key 取 canned 回复。
            # 夹具里没有这个 key 时**不编一条假条目**（那会被引擎并进 reference.bib，
            # 污染验收证据），只回一句话——引擎侧会把它记成「回复里没有可用条目」。
            key = _request_key(user)
            with STATE.lock:
                rep = STATE.replies.get(key)
                if rep is not None:
                    STATE.n_replay_hit += 1
                else:
                    STATE.n_replay_miss += 1
            content = rep if rep is not None else (
                f"（mock replay 夹具未覆盖 `{key or '（没读出 key）'}` 这一条："
                "夹具只回放 tools/fixtures 里写好的那几条，这里故意不给 bibtex）")
        else:
            content = _answer_text(user, mode)
        body = {
            "id": f"chatcmpl-mock-{n}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model or "mock-model",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": content},
                         "finish_reason": finish}],
            "usage": {
                "prompt_tokens": prompt_t,
                "completion_tokens": completion_t,
                "total_tokens": prompt_t + completion_t,
                "completion_tokens_details": {"reasoning_tokens": reasoning_t},
            },
            # 非标准字段：只在 mock 的日志/排障里有意义，真端点不会有
            "mock_mode": mode,
        }
        with STATE.lock:
            STATE.n_by_finish[finish] = STATE.n_by_finish.get(finish, 0) + 1
            extra = ""
            if mode == "replay":
                extra = (f" replay={'命中夹具' if rep is not None else '夹具未覆盖'}"
                         f"（key={_request_key(user) or '—'}）")
        # 这一行就是「GUI 真的调用了 mock 吗」的证据：请求特征 + 回复的 finish_reason + token
        STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 200 finish_reason={finish}"
                   f" prompt={prompt_t} completion={completion_t} reasoning={reasoning_t}"
                   f" 回复 {len(content.encode('utf-8'))}B（模式 {mode}{extra}）")
        self._send(200, body)

    # ── Responses 通道（`POST /v1/responses`）──────────────────────────────
    #
    # 为什么 mock 也要有这条路：引擎的 Responses 解析（`_responses_text` /
    # `_responses_finish` / `_responses_usage_from` / 检索证据）与探针的判据**从来没有
    # 对着任何端点跑过**。真端点（DeepSeek）拿不到 key、也不该拿验收去烧额度，所以
    # 必须有一个能**按剧本**把三种世界复现出来的本地端点：
    #   · `responses_search`        —— 真支持检索（回 web_search_call + url_citation）；
    #   · `responses_ignore_search` —— DeepSeek **flash** 的真实行为：收到 tools 也**静默忽略**；
    #   · `responses_reject_search` —— 端点明确 400 拒绝。
    # 三种都必要：只测前一种，就会把「静默忽略」误报成「支持」（这正是本轮要修的病）。

    def _responses(self, path: str, req: dict) -> None:
        with STATE.lock:
            STATE.n_requests += 1
            n = STATE.n_requests
            STATE.n_by_path[path] = STATE.n_by_path.get(path, 0) + 1
            mode = STATE.mode
            slow = STATE.slow_seconds

        model = req.get("model", "")
        # instruments：responses 的请求体字段名与 chat 不同（instructions/input/
        # max_output_tokens/reasoning），各自读出来打在一行里——「GUI 到底发了什么」
        # 全靠这一行与 /__stats 里的 last_user_prompts。
        tools_raw = req.get("tools")
        tools = [str(t.get("type", "")) for t in tools_raw
                 if isinstance(t, dict)] if isinstance(tools_raw, list) else []
        # 「这个请求带没带检索工具」= tools 非空，**不**钉死名字 `web_search`。
        # 为什么（第 70 条）：工具名已经可配置（`SLCN_LLM_SEARCH_TOOL`）。如果这里只认
        # `web_search`，那么用自定义名字（如 `web_search_preview`）跑 `responses_search`
        # 剧本时端点永远不回检索证据，「换个名字也照样按证据判支持」这条验收就没法做——
        # 而那个剧本扮演的正是「你发什么检索工具我就认什么」的支持检索的端点。
        # 另外两个剧本（ignore / reject）本来就只看「有没有 tools」，不受影响。
        has_search = bool(tools)
        mo = req.get("max_output_tokens")
        mt = mo if isinstance(mo, int) else None
        insp = req.get("instructions")
        inp = req.get("input")
        # `input` 可以是字符串，也可以是 item 列表（引擎只发字符串；这里两种都收，
        # 免得界面/别的工具用列表形式时 mock 记不到 user 文本）。
        if isinstance(inp, str):
            user = inp
        elif isinstance(inp, list):
            user = json.dumps(inp, ensure_ascii=False)
        else:
            user = ""
        feat = (f"protocol=responses model={model!r}"
                f" tools={json.dumps(tools, ensure_ascii=False) if tools else '无'}"
                f" max_output_tokens={mt if mt is not None else '无'}"
                f" reasoning={json.dumps(req.get('reasoning'), ensure_ascii=False) if 'reasoning' in req else '无'}"
                f" instructions={'有' if insp else '无'}")
        self._record_prompt(_request_key(user), model, user, "responses", tools)

        if mode == "slow" and slow > 0:
            STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 慢速模式，先睡 {slow}s 再回答")
            time.sleep(slow)

        if mode == "http500":
            STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 500 服务故障")
            self._error(500, "mock 的 http500 模式：端点故障，请稍后再试")
            return
        if mode == "responses_reject_search" and tools:
            # 「端点明确不支持」：文案照 OpenAI/DeepSeek 那类错误写，探针要把原话展示给编辑。
            STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 400 拒绝了 tools/web_search")
            self._error(400, "Unsupported tool type: 'web_search'"
                             "（mock 的 responses_reject_search 模式：本端点不实现服务端检索工具）",
                        param="tools")
            return

        # 检索证据：**只有 `responses_search` 这一个模式**会回 `web_search_call`。
        # `responses_ignore_search` 刻意什么都不回（DeepSeek flash 的静默忽略）——探针必须
        # 据此报「不支持」，如果本 mock 在 ignore 模式下也回检索证据，那条判据就白测了。
        search_item = None
        if mode == "responses_search" and has_search:
            search_item = {
                "type": "web_search_call",
                "id": f"ws_mock_{n}",
                "status": "completed",
                "action": {"type": "search", "query": "2025 年诺贝尔物理学奖 官方"},
            }

        # 正文：请求里带 bib 条目（`@type{key,`）时沿用 chat 那套「把条目抄回来」；
        # 否则（探针那种自由提问）给一段**标明是 mock** 的文字，不冒充真答案。
        # replay 模式与 chat 通道**同一份夹具**：夹具没覆盖的 key 不编假条目
        # （否则验收会以为「这条是端点核对过的」——污染证据）。
        if mode == "replay":
            key = _request_key(user)
            with STATE.lock:
                rep = STATE.replies.get(key)
                if rep is not None:
                    STATE.n_replay_hit += 1
                else:
                    STATE.n_replay_miss += 1
            content = rep if rep is not None else (
                f"（mock replay 夹具未覆盖 `{key or '（没读出 key）'}` 这一条："
                "夹具只回放 tools/fixtures 里写好的那几条，这里故意不给 bibtex）")
        elif _KEY_RE.search(user or ""):
            content = _answer_text(user, mode)
        else:
            content = ("% mock_llm_server：本条回复由**本地假端点**生成，仅用于验收，"
                       "不是真实核实结果\n")
            content += ("（mock 回答）已按你的要求作答。"
                        + ("检索证据见本条回复的 web_search_call 项。" if search_item
                           else "本端点没有执行任何检索。"))

        # status / incomplete_details：把 chat 的 finish_reason 映射成 Responses 的形状，
        # 这样引擎侧 `_responses_finish()` 的**每一条映射**（stop / length / content_filter）
        # 都能在本地被真正跑到。
        status, incomplete = "completed", None
        if mode == "length":
            status, incomplete = "incomplete", {"reason": "max_output_tokens"}
        elif mode == "content_filter":
            status, incomplete = "incomplete", {"reason": "content_filter"}
        elif mode == "insufficient_system_resource":
            # responses 没有这个 reason（只有 max_output_tokens / content_filter）：
            # 给一个**未知** reason，练引擎侧「不认识的原因原样带上、绝不当成 stop」。
            status, incomplete = "incomplete", {"reason": "insufficient_system_resource"}

        th = req.get("reasoning")
        thinking_off = isinstance(th, dict) and str(th.get("effort", "")).lower() == "none"
        reasoning_on = (mode != "stop") and not thinking_off
        prompt_t, completion_t, reasoning_t = _tokens(mt, reasoning_on)

        output: list[dict] = []
        if search_item is not None:
            output.append(search_item)
        msg: dict = {
            "type": "message", "id": f"msg_mock_{n}", "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content, "annotations": []}],
        }
        if search_item is not None:
            # url_citation 注解：OpenAI 官方文档里「引用了来源」的形态。引擎侧把它收进
            # LAST_SEARCH.urls（日志里给编辑看「它引了哪些页面」）。
            msg["content"][0]["annotations"] = [{
                "type": "url_citation", "start_index": 0, "end_index": 1,
                "url": "https://www.nobelprize.org/prizes/physics/2025/summary/",
                "title": "The Nobel Prize in Physics 2025（mock 来源）",
            }]
        output.append(msg)

        body = {
            "id": f"resp_mock_{n}",
            "object": "response",
            "created_at": int(time.time()),
            "status": status,
            "model": model or "mock-model",
            "output": output,
            "error": None,
            "incomplete_details": incomplete,
            "usage": {
                "input_tokens": prompt_t,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": completion_t,
                "output_tokens_details": {"reasoning_tokens": reasoning_t},
                "total_tokens": prompt_t + completion_t,
            },
            "mock_mode": mode,
        }
        with STATE.lock:
            STATE.n_by_finish[status] = STATE.n_by_finish.get(status, 0) + 1
        STATE.note(f"[req #{n}] POST {path} ← {self._peer()} {feat} → 200 status={status}"
                   f" web_search_call={'有' if search_item else '无'}"
                   f" prompt={prompt_t} completion={completion_t} reasoning={reasoning_t}"
                   f" 回复 {len(content.encode('utf-8'))}B（模式 {mode}）")
        self._send(200, body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mock_llm_server.py",
        description="本地 OpenAI 兼容「假端点」（GUI 验收用；纯标准库、只绑 127.0.0.1、不联网）",
        epilog=(
            "模式：\n  " + "\n  ".join(f"{m:<30}{_MODE_HINT[m]}" for m in MODES) + "\n\n"
            "运行时切换：curl -s -XPOST 127.0.0.1:PORT/__mode -d '{\"mode\":\"length\"}'\n"
            "看统计（含请求日志尾巴）：curl -s 127.0.0.1:PORT/__stats\n"
            "  其中 last_user_prompts = 端点**实际收到**的 user 消息（最近 20 条），"
            "用来核对引擎到底发了什么\n"
            "GUI「设置」页：API 地址 http://127.0.0.1:PORT/v1 ｜ 模型名 mock-model ｜ 密钥随意\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"监听端口（只绑 127.0.0.1；默认 {DEFAULT_PORT}）")
    ap.add_argument("--mode", choices=MODES, default=None,
                    help="启动时的模式（默认 normal；给了 --replies 就默认 replay；"
                         "运行中可用 POST /__mode 改）")
    ap.add_argument("--slow-seconds", type=float, default=3.0,
                    help="slow 模式的睡眠秒数（默认 3；测 15s/60s 超时请开到 20 以上）")
    ap.add_argument("--replies", metavar="夹具.json",
                    help="replay 模式的夹具：{bib key: AI 回复文本}。"
                         "给这个参数且没显式 --mode 时，启动模式就是 replay")
    a = ap.parse_args(argv)

    if not (1 <= a.port <= 65535):
        print(f"❌ 端口 {a.port} 不在 1–65535 范围内", file=sys.stderr)
        return 2

    if a.replies:
        try:
            STATE.replies = load_replies(a.replies)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"❌ 读不了夹具 {a.replies}：{e}", file=sys.stderr)
            return 2
        STATE.replies_path = os.path.abspath(a.replies)

    mode = a.mode or ("replay" if STATE.replies else "normal")
    if mode == "replay" and not STATE.replies:
        print("❌ replay 模式必须配 --replies <夹具.json>", file=sys.stderr)
        return 2
    STATE.mode = mode
    STATE.slow_seconds = a.slow_seconds

    httpd = ThreadingHTTPServer((HOST, a.port), Handler)   # Threading：slow 模式不挡住其它请求
    httpd.daemon_threads = True
    print(f"mock LLM 端点已启动：http://{HOST}:{a.port}/v1"
          f"（当前模式 {STATE.mode}：{_MODE_HINT[STATE.mode]}）", flush=True)
    if STATE.replies:
        print(f"  夹具 {STATE.replies_path}：{len(STATE.replies)} 条"
              f"（键：{'、'.join(sorted(STATE.replies))}）", flush=True)
    print(f"  只绑回环 {HOST}，不做任何出站请求 —— 验收全程离线、不花额度、不外发稿件内容。", flush=True)
    print(f"  GUI「设置」页填：API 地址 http://{HOST}:{a.port}/v1 ｜ 模型名 mock-model ｜ 密钥随意", flush=True)
    print(f"  切模式：curl -s -XPOST {HOST}:{a.port}/__mode -d '{{\"mode\":\"length\"}}'"
          f" ｜ 看统计：curl -s {HOST}:{a.port}/__stats", flush=True)
    print("  Ctrl-C 结束。下面是逐请求日志（GUI 每发一个请求这里就会多一行）：", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
