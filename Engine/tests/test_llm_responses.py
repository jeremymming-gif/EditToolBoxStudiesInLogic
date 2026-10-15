#!/usr/bin/env python3.14
"""Responses 通道 + 联网检索开关 + 能力探针（第 69 条）。

为什么要有这一套：用户 2026-10-15 指出「工具说会优先查官方来源，可它发的请求**不带任何
检索参数**，模型只能凭记忆」——这是任务书规范与本工具实现之间的矛盾。本轮加了
`{base}/responses` 通道与 `tools:[{"type":"web_search"}]`，但**真端点里有一个陷阱**：

  **DeepSeek 会静默忽略 `web_search`**（官方「使用 Responses API」兼容性表的 Tools 行列明
  `web_search` / `file_search` / … 属「忽略」，且「不支持的参数会被静默忽略、不会报错」；
  官方新闻也写着「目前，API 暂不支持搜索功能」）。于是「HTTP 200」既可能是「真检索了」，
  也可能是「啥也没干」。只看状态码就会把后者报成「支持」——正是本轮要消灭的「静默假装」。

所以本套件把三件事都钉死（全部只打 127.0.0.1 上的本地小 server，**不联网**、不花额度）：

  ① **请求形状**：`{base}/responses`（带/不带 `/v1` 都对）、`instructions` / `input` /
     `max_output_tokens`（**不是** `max_tokens`）/ `reasoning` / `tools`；开关关着时
     **一个字段都不多**——`complete()` 默认路径与 `chat()` 的请求体**逐字节相同**。
  ② **回复解析**：`output[]` 取 `output_text`（跳过思维链与工具项）、usage 归一成
     chat 那组键（含 reasoning）、`status`/`incomplete_details` → `LAST_FINISH`
     （completed→stop、max_output_tokens→length、content_filter、failed、未知原因原样带上）。
  ③ **探针判据**：三种世界各测一次——真检索（有 `web_search_call`）→ 支持；
     **200 但没有检索证据**（DeepSeek 的静默忽略）→ **不支持**；`tools` 被 400 → 不支持。

外加两条与「可审计」直接相关的：`% 来源：…` 注释**能活着穿过合并**、
`channel_provenance()` 在「开了检索但 0 次检索」时**必须**写成「凭记忆」。

用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_llm_responses.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import convert  # noqa: E402
from slcn import aibib, llm, refmark  # noqa: E402

FAILS = []
RAWS = []       # 端点收到的**原始请求体字节**（逐字节比对用）
BODIES = []     # 解析后的请求体
PATHS = []      # 端点收到的路径


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ── 本地假端点：可切换三种回复（真检索 / 静默忽略 / 成功但无文本）与 400 ──────
class H(BaseHTTPRequestHandler):
    mode = "search"      # search | ignore | notext | ok | http400 | badjson
    last = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8")
        RAWS.append(raw)
        PATHS.append(self.path)
        BODIES.append(json.loads(raw))
        H.last = BODIES[-1]

        if H.mode == "http400":
            body = json.dumps({"error": {
                "message": "Unsupported tool type: 'web_search'（本地假端点）",
                "type": "invalid_request_error", "param": "tools", "code": None}}).encode()
            self.send_response(400)
        elif H.mode == "badjson":
            body = b"not-json"
            self.send_response(200)
        else:
            body = json.dumps(_reply(H.mode)).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _reply(mode):
    """按模式造一份 Responses 形状的回复。`mode` 决定「有没有检索证据」。"""
    tools = H.last.get("tools") or []
    has_search_tool = any(isinstance(t, dict) and t.get("type") == "web_search" for t in tools)
    output = []
    if mode == "search" and has_search_tool:
        output.append({"type": "web_search_call", "id": "ws_1", "status": "completed",
                       "action": {"type": "search", "query": "2025 诺奖 物理 官方"}})
    if mode == "ok2":       # 两个 message + 一个 reasoning 项：验证只拼 output_text
        output.append({"type": "reasoning", "content": [
            {"type": "reasoning_text", "text": "先想一下……"}]})
        output.append({"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "AAA"}]})
        output.append({"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "BBB"}]})
        status, incomplete = "completed", None
    elif mode == "notext":
        status, incomplete = "completed", None
    else:
        output.append({"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "% mock 回复\n@article{K,\n}\n",
             "annotations": [{"type": "url_citation", "url": "https://example.org/official",
                              "title": "官方页面"}]}]})
        status, incomplete = "completed", None
    if mode == "incomplete_len":
        status, incomplete = "incomplete", {"reason": "max_output_tokens"}
    elif mode == "incomplete_cf":
        status, incomplete = "incomplete", {"reason": "content_filter"}
    elif mode == "incomplete_unknown":
        status, incomplete = "incomplete", {"reason": "insufficient_system_resource"}
    elif mode == "failed":
        status, incomplete = "failed", None
    elif mode == "nostatus":
        status, incomplete = None, None
    return {
        "id": "resp_1", "object": "response", "created_at": 0,
        "status": status, "model": "mock-model", "output": output,
        "error": None, "incomplete_details": incomplete,
        "usage": {"input_tokens": 11, "input_tokens_details": {"cached_tokens": 3},
                  "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 2},
                  "total_tokens": 18},
    }


srv = HTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE_V1 = f"http://127.0.0.1:{PORT}/v1"
BASE_NO_V1 = f"http://127.0.0.1:{PORT}"

# ── (a) 环境变量口径：默认 chat / 关，认不出的值退回默认且**出声** ──────────
print("\n── (a) 协议与检索开关的环境变量口径 ──")


def env_with(**kv):
    """临时设置/删除环境变量后取协议与检索开关。"""
    old = {k: os.environ.get(k) for k in ("SLCN_LLM_PROTOCOL", "SLCN_LLM_WEB_SEARCH")}
    for k in old:
        os.environ.pop(k, None)
    for k, v in kv.items():
        if v is not None:
            os.environ[k] = v
    try:
        return llm.protocol_from_env(), llm.web_search_from_env()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


check("(a) 都不设 → chat + 检索关（默认值就是向后兼容）", env_with() == ("chat", False),
      repr(env_with()))
check("(a) SLCN_LLM_PROTOCOL=responses", env_with(SLCN_LLM_PROTOCOL="responses")[0] == "responses")
check("(a) 协议大小写不敏感", env_with(SLCN_LLM_PROTOCOL="Responses")[0] == "responses")
check("(a) SLCN_LLM_WEB_SEARCH 认 1/true/yes/on（大小写不敏感）",
      all(env_with(SLCN_LLM_WEB_SEARCH=v)[1] for v in ("1", "true", "YES", "On")))
check("(a) SLCN_LLM_WEB_SEARCH=0/空 → 关（默认关，不能让手滑的值把它打开）",
      not any(env_with(SLCN_LLM_WEB_SEARCH=v)[1] for v in ("0", "false", "", "no")))
_old_proto = os.environ.get("SLCN_LLM_PROTOCOL")
os.environ["SLCN_LLM_PROTOCOL"] = "gpt5-turbo"
_p, _ = llm.protocol_from_env(), None
check("(a) 认不出的协议 → 退回 chat（一个手滑的配置不该打灭整条通道）", _p == "chat", _p)
if _old_proto is None:
    os.environ.pop("SLCN_LLM_PROTOCOL", None)
else:
    os.environ["SLCN_LLM_PROTOCOL"] = _old_proto

_cfg = llm.config_from_env("http://x/v1", "k", "m", protocol="responses", web_search=True)
check("(a) config_from_env 收下协议与开关（且 base 的尾斜杠仍被 rstrip）",
      (_cfg.protocol, _cfg.web_search, _cfg.base) == ("responses", True, "http://x/v1"))
check("(a) uses_web_search：chat 通道下**永远**为假（chat 的 tools 是 function calling，不是检索）",
      llm.LLMConfig(base="b", model="m", protocol="chat", web_search=True).uses_web_search is False
      and llm.LLMConfig(base="b", model="m", protocol="responses", web_search=True).uses_web_search is True)

# ── (b) 请求形状 ─────────────────────────────────────────────────────────
print("\n── (b) responses 请求形状（含 base 带/不带 /v1）──")
cfg = llm.LLMConfig(base=BASE_V1, key="mock", model="mock-model", protocol="responses")

H.mode = "ok"
out = llm.complete(cfg, "SYS", "USER", timeout=10)
b = BODIES[-1]
check("(b) base 带 /v1 → 打到 `/v1/responses`", PATHS[-1] == "/v1/responses", PATHS[-1])
check("(b) 默认请求体字段 = model/instructions/input/temperature（**没有** tools）",
      set(b) == {"model", "instructions", "input", "temperature"}, repr(sorted(b)))
check("(b) system→instructions、user→input（与 chat 的语义一一对应）",
      b["instructions"] == "SYS" and b["input"] == "USER")
check("(b) temperature 仍是 0（与 chat 同一口径：抽取任务求确定性）", b["temperature"] == 0)
check("(b) 默认**不带** max_output_tokens / reasoning / tools",
      not ({"max_output_tokens", "reasoning", "tools"} & set(b)))
check("(b) 有正文时返回值就是 output_text 本身（不含思维链）",
      out is not None and "@article{K," in out, repr(out))

cfg_nov1 = llm.LLMConfig(base=BASE_NO_V1, key="mock", model="mock-model", protocol="responses")
llm.complete(cfg_nov1, "SYS", "USER", timeout=10)
check("(b) base 不带 /v1 → 打到 `/responses`（DeepSeek 官方 base_url 就是这种写法）",
      PATHS[-1] == "/responses", PATHS[-1])

cfg_search = llm.LLMConfig(base=BASE_V1, key="mock", model="mock-model",
                           protocol="responses", web_search=True)
H.mode = "search"
out = llm.complete(cfg_search, "SYS", "USER", timeout=10, max_tokens=2048)
b = BODIES[-1]
check("(b) 开了检索 → 正好 tools=[{type:web_search}]",
      b.get("tools") == [{"type": "web_search"}], repr(b.get("tools")))
check("(b) 输出上限用 responses 的字段名 max_output_tokens（写成 max_tokens 会被静默忽略）",
      b.get("max_output_tokens") == 2048 and "max_tokens" not in b, repr(sorted(b)))
check("(b) thinking=disabled → reasoning={effort:none}（精确等价：关掉思考）",
      (lambda: (llm.complete(cfg_search, "SYS", "USER", timeout=10,
                             thinking={"type": "disabled"}), BODIES[-1]))()[1].get("reasoning")
      == {"effort": "none"})
check("(b) thinking=enabled → **不发** reasoning（effort 没有「默认」档，猜一个会静默改成本）",
      (lambda: (llm.complete(cfg_search, "SYS", "USER", timeout=10,
                             thinking={"type": "enabled"}), BODIES[-1]))()[1].get("reasoning") is None)
check("(b) 检索证据被收进 LAST_SEARCH（web_search_call + url_citation）",
      llm.LAST_SEARCH.get("n_calls") == 1
      and llm.LAST_SEARCH.get("urls") == ["https://example.org/official"],
      repr(llm.LAST_SEARCH))

# ── (c) **逐字节**向后兼容：开关关着时 complete() 与 chat() 的请求体一字不差 ──
print("\n── (c) 开关关着 → 请求体逐字节等于 chat（老调用方零影响）──")
cfg_chat = llm.LLMConfig(base=BASE_V1, key="mock", model="mock-model")
H.mode = "ok"
llm.chat(cfg_chat, "SYS", "USER", timeout=10, max_tokens=2048)
raw_chat = RAWS[-1]
path_chat = PATHS[-1]
llm.complete(cfg_chat, "SYS", "USER", timeout=10, max_tokens=2048)
check("(c) 默认协议下 complete() 打的是 /v1/chat/completions", PATHS[-1] == path_chat == "/v1/chat/completions")
check("(c) 请求体**逐字节相同**（这是「不改老行为」的机器证据）",
      RAWS[-1] == raw_chat, f"{RAWS[-1]!r} != {raw_chat!r}")
check("(c) LAST_SEARCH 在 chat 通道被清空（不能把上次 responses 的检索证据带过来）",
      llm.LAST_SEARCH == {"n_calls": 0, "queries": [], "urls": []}, repr(llm.LAST_SEARCH))

# ── (d) 回复解析：文本 / usage / 结束原因 ────────────────────────────────
print("\n── (d) 回复解析：文本、usage、status→LAST_FINISH ──")
H.mode = "ok2"
out = llm.complete(cfg, "SYS", "USER", timeout=10)
check("(d) 多个 message 的 output_text 拼接、思维链（reasoning_text）**不进**正文",
      out == "AAABBB", repr(out))
u = llm.LAST_USAGE
check("(d) usage 归一成 chat 那组键（prompt/completion/total/reasoning）",
      (u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens"),
       u.get("reasoning_tokens")) == (11, 7, 18, 2), repr(u))
check("(d) 原生字段与缓存命中保留（账单对账要它们）",
      u.get("input_tokens") == 11 and u.get("output_tokens") == 7 and u.get("cached_tokens") == 3,
      repr(u))
check("(d) status=completed → LAST_FINISH=stop（调用方的「干净收尾」判据原样成立）",
      llm.LAST_FINISH == "stop", repr(llm.LAST_FINISH))

for mode, want, why in (("incomplete_len", "length", "被 max_output_tokens 截断"),
                        ("incomplete_cf", "content_filter", "内容过滤"),
                        ("incomplete_unknown", "insufficient_system_resource", "未知原因原样带上"),
                        ("failed", "failed", "端点报失败")):
    H.mode = mode
    llm.complete(cfg, "SYS", "USER", timeout=10)
    check(f"(d) {mode} → LAST_FINISH={want}（{why}）",
          llm.LAST_FINISH == want, repr(llm.LAST_FINISH))

H.mode = "nostatus"
llm.complete(cfg, "SYS", "USER", timeout=10)
check("(d) 缺 status → LAST_FINISH 空串（老端点没给这个字段，不能当异常误报）",
      llm.LAST_FINISH == "", repr(llm.LAST_FINISH))

H.mode = "notext"
out = llm.complete(cfg, "SYS", "USER", timeout=10)
check("(d) 200 但没有 message 正文 → 返回空串（不是 None：这次调用本身是成功的）",
      out == "" and llm.LAST_ERROR == "", f"{out!r} / {llm.LAST_ERROR!r}")

H.mode = "http400"
out = llm.complete(cfg_search, "SYS", "USER", timeout=10)
check("(d) 4xx → 仍是 None（失败契约不变），LAST_HTTP/LAST_BODY 落下机器可判的证据",
      out is None and llm.LAST_HTTP == 400
      and "Unsupported tool type" in llm.LAST_BODY
      and "/responses" in llm.LAST_ERROR, llm.LAST_ERROR[:120])

H.mode = "badjson"
out = llm.complete(cfg, "SYS", "USER", timeout=10)
check("(d) 200 但响应不是合法 JSON → None + LAST_HTTP=0（归到「没测出来」，不是「不支持」）",
      out is None and llm.LAST_HTTP == 0, f"{out!r} / {llm.LAST_HTTP}")

# ── (e) 探针：三种世界三种判词 ────────────────────────────────────────────
print("\n── (e) 探针 probe_web_search（不信 200，只认检索证据）──")
H.mode = "search"
r = llm.probe_web_search(BASE_V1, "mock", "mock-model", timeout=10, query="随便问")
check("(e) 有 web_search_call → 支持", r["verdict"] == "supported" and r["verdict_cn"] == "支持",
      json.dumps(r, ensure_ascii=False)[:160])
check("(e) 支持时带上检索次数与来源 URL（编辑能看出它查了什么）",
      r["n_search_calls"] == 1 and r["urls"] == ["https://example.org/official"], repr(r["urls"]))

H.mode = "ignore"        # 收到 tools 也当没看见（DeepSeek 的真实行为）
r = llm.probe_web_search(BASE_V1, "mock", "mock-model", timeout=10, query="随便问")
check("(e) **200 但没有检索证据 → 不支持**（DeepSeek 静默忽略；只看状态码就会误报成支持）",
      r["verdict"] == "unsupported", json.dumps(r, ensure_ascii=False)[:200])
check("(e) 这一格的说明必须点名「静默忽略」（编辑要看得懂为什么会这样）",
      "静默忽略" in r["detail"] or "没有检索证据" in r["detail"], r["detail"][:120])

H.mode = "http400"
r = llm.probe_web_search(BASE_V1, "mock", "mock-model", timeout=10, query="随便问")
check("(e) HTTP 400 明确拒绝 → 不支持 + 状态码 + 端点原话",
      r["verdict"] == "unsupported" and r["http_code"] == 400
      and "Unsupported tool type" in r["body"], json.dumps(r, ensure_ascii=False)[:200])

r = llm.probe_web_search(f"http://127.0.0.1:{PORT + 1}/v1", "mock", "mock-model", timeout=2)
check("(e) 连不上 → 其他错误（**不能**算「不支持」，也不能算「支持」）",
      r["verdict"] == "error" and r["verdict_cn"] == "其他错误", json.dumps(r, ensure_ascii=False)[:160])

r = llm.probe_web_search("", "", "", timeout=2)
check("(e) 没配置 → 其他错误 + 中文原因（不是「不支持」）",
      r["verdict"] == "error" and "没配置" in r["detail"], repr(r["detail"]))
check("(e) 探针只打一个地址：`{base}/responses`（不猜要不要补 /v1）",
      r["endpoint"] == "/responses", repr(r["endpoint"]))

srv.shutdown()

# ── (f) `% 来源：` 注释必须活着穿过合并 ──────────────────────────────────
print("\n── (f) `% 来源：…` 注释穿过合并（可审计的来源标注）──")
ORIG = "@article{K1,\n  author = {T. Burge},\n  title = {X},\n  year = {1984}\n}\n"
REPLY = ("% 来源：https://www.worldcat.org/title/x （WorldCat，2026-10-15 查）\n"
         "@article{K1,\n  author = {T. Burge},\n  title = {X},\n  year = {1984}\n}\n")
res = aibib.merge(ORIG, {"K1": REPLY})
txt = res.text
check("(f) 来源注释随条目并入（一字不改）",
      "% 来源：https://www.worldcat.org/title/x （WorldCat，2026-10-15 查）" in txt, repr(txt))
check("(f) 来源注释落在条目**上方**（回读校验只认条目上方的注释）",
      txt.index("% 来源：") < txt.index("@article{K1,"), repr(txt.splitlines()[:3]))
check("(f) 未返回的条目逐字节不动（老保障不受影响）",
      aibib.merge(ORIG, {})[0].text == ORIG if isinstance(aibib.merge(ORIG, {}), tuple)
      else aibib.merge(ORIG, {}).text == ORIG)

# 写在围栏外/条目下方也要救回来（第 65 条的宽容：紧邻条目的 `%` 行都算这一条的）
REPLY_OUT = ("```bibtex\n@article{K1,\n  title = {X},\n}\n```\n"
             "% 来源：https://doi.org/10.1/xyz （DOI 解析页）\n")
res2 = aibib.merge(ORIG, {"K1": REPLY_OUT})
check("(f) 来源注释写在围栏外/条目下方也能收回来并挪到上方",
      "% 来源：https://doi.org/10.1/xyz （DOI 解析页）" in res2.text
      and res2.text.index("% 来源：") < res2.text.index("@article{K1,"), repr(res2.text))

# ── (g) 任务书：全字段口径 / 来源要求 / 通道说明 ────────────────────────
print("\n── (g) 任务书与逐条提示词 ──")
check("(g) 第一节写明核实范围是**全字段**（author/title/类型/journal/volume/number/"
      "pages/publisher/address/edition/year 一个都不许跳过）",
      "核实范围" in refmark.SPEC_MD and "一个字段都不许跳过" in refmark.SPEC_MD)
check("(g) 第一节写明「核实 ≠ 补全」：查不到照旧省略 + `% ⚠ 待核实：…`（铁律没被放宽）",
      "编错的卷号比空缺更糟" in refmark.SPEC_MD)
check("(g) 第三节新增第 11 条：每条核实过的条目上方写 `% 来源：`",
      "11." in refmark.FORMAT_MD and "% 来源：" in refmark.FORMAT_MD)
check("(g) 第 11 条要求「不许编一个看起来像 URL 的字符串」",
      "绝不许编一个看起来像 URL 的字符串" in refmark.FORMAT_MD)
check("(g) 第二节把「能用检索工具就必须用」写进规范",
      "能用检索工具就必须用" in refmark.SOURCES_MD)

_body = "\n  author = {X},\n  year = {2020}\n"
# 旧形状（第 68 条之前的老拼法）：`@type{key,` + body.strip() + `}`，一个字都不多。
_entry = f"@article{{K1,\n{_body.strip()}}}"
check("(g) entry_prompt 默认（search=False）与从前逐字节相同",
      refmark.entry_prompt("article", "K1", _body) == _entry,
      repr(refmark.entry_prompt("article", "K1", _body)))
check("(g) entry_prompt(search=True) 追加一行检索引导（且仍只有一条 bibtex）",
      refmark.entry_prompt("article", "K1", _body, search=True) == _entry + "\n" + refmark.SEARCH_PROMPT_LINE
      and refmark.SEARCH_PROMPT_LINE.count("@") == 0
      and "% 来源：" in refmark.SEARCH_PROMPT_LINE,
      repr(refmark.entry_prompt("article", "K1", _body, search=True)))

_md_plain = refmark.build(ORIG, "K")
_md_note = refmark.build(ORIG, "K", search_note="> 本次核实通道：responses + web_search（已启用联网检索）")
check("(g) 不传 search_note → 头部仍是「工具不做联网核实」（旧调用逐字节不变）",
      "工具**不做联网核实**" in _md_plain)
check("(g) 传了 search_note → 替换掉那句（开着检索时它不成立）",
      "已启用联网检索" in _md_note and "工具**不做联网核实**" not in _md_note)
check("(g) 任务书里点名了检索状态的可追溯性（来源标注 + 本次通道）",
      "本次核实通道" in _md_note and "% 来源：" in _md_note)

# ── (h) 通道说明（合并记录里那行）──────────────────────────────────────
print("\n── (h) channel_provenance：开了检索但 0 次检索必须写「凭记忆」──")
p_chat = convert.channel_provenance("chat（未启用检索：模型只能凭记忆）", "chat", "m", False, 0)
check("(h) 未启用检索 → 只写通道，不提检索证据",
      "未启用检索" in p_chat and "检索证据" not in p_chat, p_chat)
p_ok = convert.channel_provenance("responses + web_search（已启用联网检索）", "responses", "m", True, 3)
check("(h) 有检索证据 → 写明确属联网核实", "确属联网核实" in p_ok and "3 个" in p_ok, p_ok)
p_none = convert.channel_provenance("responses + web_search（已启用联网检索）", "responses", "m", True, 0)
check("(h) **开了开关但 0 次检索 → 明写「按凭记忆对待」**（不许让开关状态冒充已联网）",
      "凭记忆" in p_none and "0 个" in p_none, p_none)

_sec_plain = aibib.render_merge_section(aibib.merge(ORIG, {"K1": REPLY}))
_sec_prov = aibib.render_merge_section(aibib.merge(ORIG, {"K1": REPLY}), provenance=p_none)
check("(h) 不传 provenance → 合并记录与从前逐字节相同",
      _sec_plain == aibib.render_merge_section(aibib.merge(ORIG, {"K1": REPLY}))
      and "本次核实通道" not in _sec_plain)
check("(h) 传了 provenance → 写进「六、合并记录」（留档：事后能分清联网/凭记忆）",
      "本次核实通道" in _sec_prov and "凭记忆" in _sec_prov and _sec_prov.startswith("## 六、合并记录"))

print()
print("SUMMARY: %d FAIL" % len(FAILS) + ("  -> " + ", ".join(FAILS) if FAILS else "  (all PASS)"))
sys.exit(1 if FAILS else 0)
