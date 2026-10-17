r"""可选的大模型通道：处理 CrossRef 覆盖不到的文献（中文、集刊、学位论文）。

设计原则
--------
1. **用户自带 API**：只走 **OpenAI 兼容**接口，因此同一套代码能接
   DeepSeek / OpenAI / 通义 / Moonshot / 智谱等云端服务，也能接本机自建服务
   （LM Studio 之类，同样提供 OpenAI 兼容端点）。
   ⚠️ 2026-10-14：**本项目的本地模型试点已退役**（本机 Ollama 与三个本地模型已卸载，
   理由与实测数字见 `README.md` 第 50 条）——本模块保留并继续服务**云端 / 自建端点**通道。

2. **默认只做「抽取」，不做「补全」**：
   - 抽取模式：从条目原文里把刊名/卷/期/页抽成结构化字段 —— **纯提取，无幻觉**
   - 补全模式（需显式开启）：让模型补出缺失字段，结果一律标注
     「AI 建议，须核实」。参考文献是学术事实，**不能让模型凭记忆编**
     —— 编错的卷号比空缺更糟。

3. **不联网也一样能用**：没配 API 时自动跳过，只报规则通道的结果。

配置方式（任选其一）
--------------------
    export SLCN_LLM_BASE="https://api.deepseek.com/v1"
    export SLCN_LLM_KEY="sk-..."
    export SLCN_LLM_MODEL="deepseek-chat"

本机/自建端点的例子（OpenAI 兼容，密钥通常可留空）：
    export SLCN_LLM_BASE="http://127.0.0.1:1234/v1"
    export SLCN_LLM_MODEL="<本地服务的模型名>"

两条通道（**默认 chat，逐字节向后兼容**）
----------------------------------------
    SLCN_LLM_PROTOCOL=chat|responses     （默认 chat）
    SLCN_LLM_WEB_SEARCH=1                （默认关；只在 responses 通道生效）
    SLCN_LLM_SEARCH_TOOL=web_search      （默认 web_search；第 70 条）
    SLCN_LLM_RESPONSES_PATH=/responses   （默认 /responses；第 70 条）

  · `chat`（默认）＝ `{base}/chat/completions`。**不设这四个环境变量时，URL 与请求体与
    从前逐字节相同** —— 这是硬要求：老调用方（`convert.py` ⑪）与所有既有套件都钉着它。
  · `responses` ＝ `{base}/responses`（标准 OpenAI Responses 协议；DeepSeek 官方
    「使用 Responses API」页写明其 `base_url` 为 `https://api.deepseek.com`）。开了
    `SLCN_LLM_WEB_SEARCH` 才加 `tools:[{"type":"web_search"}]`。

**两个「未来兼容」旋钮**（第 70 条：把硬编码换成配置，将来的服务商改动不必改代码）：
  · `SLCN_LLM_SEARCH_TOOL` ＝ `tools[].type` 用的工具类型名（默认 `web_search`）。
    为什么要可配：DeepSeek 的 flash 仍忽略内置检索工具（v4-pro 已支持，见下），
    各家网关的实现也各不相同，名字未必都叫 `web_search` —— OpenAI 自己就有
    `web_search_preview`。到时候只需把这一项改掉，不必重新构建。
  · `SLCN_LLM_RESPONSES_PATH` ＝ 拼在 base 之后的路径（默认 `/responses`）。
    同一条理由：将来的路径可能不是 `/responses`。缺前导 `/` 会自动补上（并出一声）。
  ⚠️ 两者**默认值就是今天的硬编码值**，所以不设时 URL 与请求体逐字节不变；
  它们的空值 / 认不出的值都按「用默认」处理（`resolve_search_tool` /
  `resolve_responses_path`），绝不因为一个手滑的配置打灭整条 `--verify-refs`。

⚠️ **联网检索的能力边界（2026-10-17 更新；别把「发了工具」当成「真的检索了」）**：
  · OpenAI 的 Responses `web_search` 是**服务端内置工具**，真会联网，并回来
    `web_search_call` 输出项 + `output_text` 上的 `url_citation` 注解
    （https://developers.openai.com/api/docs/guides/tools-web-search ）。
  · **DeepSeek 按模型区分**（2026-10-17 官方文档 + 本机实测）：Responses 内置
    `web_search` 在 **`deepseek-v4-pro`** 上**真的会检索**（返回 `web_search_call` +
    来源 URL，`/responses` 与 `/v1/responses` 两个端点都通）；在 `deepseek-flash` 上
    仍被**静默忽略**（HTTP 200、没有 `web_search_call`）。官方兼容性明细仍把
    `file_search` / `code_interpreter` 等其它内置工具列为「忽略」。
  · DeepSeek 另提供**独立的 Search / Fetch API**（`POST /v1/tools/search`、
    `POST /v1/tools/fetch`；自研搜索引擎；结果带 `authority` / `rerank_score` /
    `date` / `summary`，支持 `site:` / `lang:` / `after:` 等语法；按成功请求计费，
    Search / Fetch 各 ¥5 / 1000 次、账号级 QPS 各 5）。本模块**不直接调用**它：
    那是 `slcn/search.py`（P1 接入，给「文献核实」供检索证据）的事——
    不依赖模型内置工具、也不受模型选择影响。
  · 所以「HTTP 200」既可能表示「真检索了」，也可能表示「啥也没干」：判据永远是
    **回复里有没有 `web_search_call`**。`probe_web_search()` 只信这个证据，
    不信状态码，也不信「工具名写在请求里」。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_TIMEOUT = 60

# `SLCN_LLM_MAX_TOKENS` 没设时的输出封顶。为什么要有默认值：思维链/啰嗦模型会把
# 输出预算耗在推理和解释上，返回被截在半句，调用方只看到「返回内容不完整」，
# 根本看不出是输出预算的问题（当年 `aistruct` 试点踩过）。
# 为什么是 2048（而不是试点时的 1024）：现行调用方（`convert.py` ⑪）要的是
# **整份 bibtex 文本**（`refmark.FORMAT_MD` 第 9 条），一条条目就要几百 token，
# 1024 会把后面的条目直接截掉半句——而截断只表现为「代码块残缺」，看不出真因。
_DEFAULT_MAX_TOKENS = 2048

# 最近一次调用的**失败原因**（HTTP 状态 + 响应体片段 / 异常名）。空串 = 上次成功。
#
# 为什么要有这个：`chat()` 的契约是「失败一律返回 None，不抛异常」（调用方靠它回退），
# 但**只返回 None 会把原因整个吞掉** —— 实测端点上下文不够时直接回
# `HTTP 400 exceed_context_size_error`，而调用方只看到「调用失败/超时」，
# 排查时完全看不出是上下文超限（2026-10-14 的一次 A/B 就因此白跑了一轮）。
# 所以：返回值契约不变，但**把原因写进 stderr 并留在 LAST_ERROR 里**。
LAST_ERROR = ""

# 最近一次**成功调用**的 token 用量。键固定含 `prompt_tokens` / `completion_tokens` /
# `total_tokens` / `reasoning_tokens`（响应里缺的补 0），端点额外给的整数字段原样留着；
# 失败时是空 dict {}。
#
# 为什么要有这个：`chat()` 只返回内容字符串，调用方（`convert.py` 的 `--verify-refs`）
# 想让编辑看到「这一期跑掉多少额度」，否则只能去端点后台翻账单；而各家端点的 usage
# 字段并不一致（有的多给 cached_tokens 之类），所以这里**只归一化、不过滤**——
# 只认整数字段（`true`/`1.5` 这类不算 token 数）。
# 为什么单列 `reasoning_tokens`：官方文档（2026-10，https://api-docs.deepseek.com/
# zh-cn/api/create-chat-completion ）把它放在 `usage.completion_tokens_details.
# reasoning_tokens`，即**思维链 token 按输出计费**。不单列出来，编辑只看到
# 「completion tokens 很高」却不知道钱烧在推理上——而 ⑪ 是纯抽取，根本不需要推理。
LAST_USAGE: dict[str, int] = {}

# 最近一次**成功调用**的结束原因（`choices[0].finish_reason`，已 strip；缺失 / None /
# 非字符串 → ""）。失败时同样是 ""——每次调用开头就清空，绝不残留上一次的值。
#
# 为什么要有这个：`max_tokens` 封顶后端点**不会报错**——它照样回 200 和一段内容，
# 只是把最后半句切掉、把 finish_reason 标成 "length"。调用方（`convert.py` ⑪）拿到的
# 是个「看起来正常」的返回值，会把残缺的 bibtex 当完整建议追加进任务书，编辑完全看不出
# 这条被截断过（假完整比明显缺失更危险）。把结束原因暴露出来，调用方才有判据去标注/告警。
# ⚠️ 2026-10：官方文档给的取值不止 stop/length，还有 `content_filter` / `tool_calls` /
# `insufficient_system_resource` / `aborted`——它们同样是「这条回复不算完整」，
# 所以调用方的判据是「非 stop 且非空即标注」，而不是只盯 `length`。
# 为什么统一成字符串（而不是 None）：各家端点给的值不一（stop / length / tool_calls /
# content_filter …），老端点甚至不给这个字段；定成「字符串，未知即空串」后，调用方只需
# 比对 `in ("", "stop")`，不必先做 None 检查，也不会被 `0`/`true` 这类值污染判据。
LAST_FINISH: str = ""

# 最近一次**失败**的 HTTP 状态码（0 = 没拿到 HTTP 响应：超时/网络/未配置/解析失败）。
#
# 为什么单列：`LAST_ERROR` 是给人看的中文串，判据（探针、界面）却需要**机器可判**的
# 状态码——「端点明确拒绝（4xx）」与「服务故障（5xx）/连不上」是两件完全不同的事，
# 前者可判「不支持这个能力」，后者只能说「这次没成」。从错误文案里正则抠状态码太脆。
LAST_HTTP: int = 0

# 最近一次失败的**响应体片段**（原样，最多 400 字符；没拿到就是空串）。
# 与 `LAST_ERROR` 分开：错误串是拼给日志看的（带地址、模型名），体是端点原话，
# 体检/探针要把端点原话原样展示给编辑（「告诉我它到底说了什么」）。
LAST_BODY: str = ""

# 最近一次**成功调用**的**检索证据**（responses 通道）：`{"n_calls": int, "queries": [...],
# "urls": [...]}`，每次调用开头清空。
#
# 为什么必须有它：`tools:[{"type":"web_search"}]` 只是一个**请求**，不代表端点真的检索了。
# 唯一诚实的证据是回复里有没有 `web_search_call` 输出项（OpenAI 官方文档：用了 web_search
# 的回复一定包含 `web_search_call` + `url_citation` 注解）。有的端点/模型会**静默忽略**这个
# 工具（见模块头），于是 `n_calls == 0` —— 调用方据此如实说「本次未发生检索」，
# 绝不能让「发了工具」长得像「已联网核实」。
LAST_SEARCH: dict = {"n_calls": 0, "queries": [], "urls": []}

# ── 协议与检索开关（默认 chat / 关：不设环境变量时请求体与从前逐字节相同）──────
DEFAULT_PROTOCOL = "chat"
PROTOCOLS = ("chat", "responses")
_SEARCH_ON = {"1", "true", "yes", "on"}

# responses 通道的**两个未来兼容旋钮**（第 70 条）：工具类型名与路径。
#
# 为什么要有这两个常量、而不是把今天的取值直接写在 `responses()` 里：默认值**就是**
# 向后兼容本身 —— 不设环境变量时 URL 与请求体必须与从前逐字节相同（既有套件钉着这条）。
# 另外，把「今天用的名字」集中成一个常量，第 70 条的理由才写得住：
# 将来服务商改了工具名或路径，配置一改即可，不必重新构建（见模块头）。
DEFAULT_SEARCH_TOOL = "web_search"
DEFAULT_RESPONSES_PATH = "/responses"

# 工具类型名允许的形状：JSON 里的一个「名字」，必须以字母/数字开头，其余只允许
# `_ - . :`。为什么**不**去校验「是不是已知工具名」：将来服务商怎么命名，我们不可能预知
# （第 70 条存在的理由就是这个），所以只排除**结构上不可能成立**的值 —— 空白、引号、
# 花括号、逗号这些一旦进了 `{"type": …}` 就会变形或被服务商当成别的参数。
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def resolve_search_tool(raw: str | None) -> str:
    """把一处配置（环境变量 / CLI）规整成可用的工具类型名。

    未设 / 空 → `DEFAULT_SEARCH_TOOL`（= 与从前逐字节相同的那个值）；
    认不出（含空白 / 引号 / 花括号 …）→ 默认值 + stderr 一条说明。为什么出声、
    为什么不抛异常：与 `protocol_from_env()` 同一条纪律 —— 一个手滑的配置不该打灭
    整条 `--verify-refs`，但**也不能静默**（用户会以为自定义的工具名生效了）。
    """
    t = (raw or "").strip()
    if not t:
        return DEFAULT_SEARCH_TOOL
    if _TOOL_NAME_RE.match(t):
        return t
    print(f"[llm] 检索工具名 {raw!r} 不是可用的工具类型名"
          f"（只允许字母/数字与 _ - . :，且不以符号开头；入口 "
          f"SLCN_LLM_SEARCH_TOOL / --llm-search-tool），"
          f"按默认 {DEFAULT_SEARCH_TOOL} 处理", file=sys.stderr, flush=True)
    return DEFAULT_SEARCH_TOOL


def search_tool_from_env() -> str:
    """`SLCN_LLM_SEARCH_TOOL` → responses 通道 `tools:[{"type": …}]` 里的类型名。"""
    return resolve_search_tool(os.environ.get("SLCN_LLM_SEARCH_TOOL"))


def resolve_responses_path(raw: str | None) -> str:
    """把一处配置规整成拼在 base 之后的路径（`{base}{path}`）。

    未设 / 空 → `DEFAULT_RESPONSES_PATH`（= 从前的 `{base}/responses`，逐字节相同）；
    **不带前导 `/` 自动补一个**（用户写 `responses` 或 `v1/responses` 是最常见的手滑，
    补一刀比让它静默 404 好）；末尾 `/` 去掉（`{base}/responses/` 会被不少端点回
    307/308，而 urllib 对 POST 的重定向处理不可靠，可能把请求体丢掉）；
    含空白 / `://` / 引号 / 花括号的，结构上不可能是路径 → 默认值 + 出声。
    """
    t = (raw or "").strip()
    if not t:
        return DEFAULT_RESPONSES_PATH
    if (any(ch.isspace() for ch in t) or "://" in t
            or any(ch in t for ch in "\"'{},")):
        print(f"[llm] responses 路径 {raw!r} 不像一个路径"
              f"（不许含空白 / :// / 引号 / 花括号 / 逗号；入口 "
              f"SLCN_LLM_RESPONSES_PATH / --llm-responses-path），"
              f"按默认 {DEFAULT_RESPONSES_PATH} 处理", file=sys.stderr, flush=True)
        return DEFAULT_RESPONSES_PATH
    norm = "/" + t.strip("/")
    if norm != t:
        # 不静默：用户写的与真正打出去的地址不一样，必须能看出来（探针结果里也有 endpoint）。
        print(f"[llm] responses 路径 {raw!r} 规整为 {norm!r}"
              "（补前导 / 或去末尾 /）", file=sys.stderr, flush=True)
    return norm


def responses_path_from_env() -> str:
    """`SLCN_LLM_RESPONSES_PATH` → responses 通道拼在 base 之后的路径。"""
    return resolve_responses_path(os.environ.get("SLCN_LLM_RESPONSES_PATH"))


# responses 通道的**输出上限**字段名（OpenAI 规范）与 chat 的不同：
#   chat → `max_tokens`；responses → `max_output_tokens`。
# 名字写错会被端点静默忽略（当成没封顶），表现与「截断」一模一样，所以固定成一个常量。
_PROTOCOL_MAX_OUTPUT_FIELD = "max_output_tokens"


def protocol_from_env() -> str:
    """`SLCN_LLM_PROTOCOL` → 通道名。未设/空 → `chat`；认不出的值 → `chat` + stderr 一条说明。

    为什么认不出也要退回 `chat` 而不是报错：一个手滑的配置不该让整条 `--verify-refs`
    挂掉；但也**不能静默**（用户会以为 responses 生效了，而请求其实走的是 chat）。
    """
    raw = os.environ.get("SLCN_LLM_PROTOCOL", "").strip()
    key = raw.lower()
    if not key:
        return DEFAULT_PROTOCOL
    if key in PROTOCOLS:
        return key
    print(f"[llm] SLCN_LLM_PROTOCOL={raw!r} 不是可识别的通道"
          f"（{'/'.join(PROTOCOLS)}），按默认 {DEFAULT_PROTOCOL} 处理",
          file=sys.stderr, flush=True)
    return DEFAULT_PROTOCOL


def web_search_from_env() -> bool:
    """`SLCN_LLM_WEB_SEARCH` → 是否加服务端检索工具。**默认关**（未设/空 → False）。

    默认关是硬要求：开了它请求体就多出 `tools` 字段，而现行调用方（`convert.py` ⑪）
    与既有套件都靠「不设时逐字节相同」这条保证。
    """
    return os.environ.get("SLCN_LLM_WEB_SEARCH", "").strip().lower() in _SEARCH_ON


def _usage_from(d: dict) -> dict[str, int]:
    """把响应里的 `usage` 归一成 `dict[str, int]`：标准字段缺失补 0，额外整数字段保留。"""
    u = d.get("usage")
    u = u if isinstance(u, dict) else {}

    def _int(v: object) -> int:
        # ⚠️ bool 是 int 的子类：`"cached": true` 不该被记成 token 用量 1
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    out = {k: _int(u.get(k)) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    # 推理 token 在**嵌套**字段里（官方文档 2026-10：`completion_tokens_details.
    # reasoning_tokens`，取值缺失/非整数一律记 0）。所以它进不了下面「额外整数字段」
    # 的循环（那里只收顶层整数），必须单独取一次。
    details = u.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    out["reasoning_tokens"] = _int(details.get("reasoning_tokens")) or _int(u.get("reasoning_tokens"))
    for k, v in u.items():
        if k not in out and isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out


def _finish_from(d: dict) -> str:
    """取 `choices[0].finish_reason` 并规整成字符串（缺失 / None / 非字符串 / 结构不对 → ""）。

    为什么这么防御：这个字段只用来**帮调用方识别截断**，绝不能因为它缺失或类型怪
    就让一次成功调用变成失败（`chat()` 的返回值契约不能被动摇）。非字符串一律当
    「端点没给」——把 `5` 或 `true` 原样当原因，会污染调用方 `== "length"` 的判据。
    """
    choices = d.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    fr = first.get("finish_reason") if isinstance(first, dict) else None
    return fr.strip() if isinstance(fr, str) else ""


# ── Responses 通道的解析（回复形状与 chat 完全不同，但结果要归一成**同一组**全局量）──
#
# 目标：`convert.py` ⑪ 一行都不用改就能同时吃两条通道 —— 它只读 LAST_FINISH /
# LAST_USAGE（键名固定为 prompt_tokens / completion_tokens / total_tokens /
# reasoning_tokens），所以 responses 的 input_tokens / output_tokens /
# output_tokens_details.reasoning_tokens 必须**映射**成那几个名字，而不是另立一套。

def _responses_text(d: dict) -> str:
    """把 `output[]` 里所有 `message` 项的 `output_text` 内容块拼成一段文本。

    为什么只取 `output_text`：`output[]` 里还可能混着 `reasoning`（思维链）与
    `function_call` / `web_search_call`（工具调用）项。思维链**不是**答案，把它拼进
    正文会让 bibtex 解析（`slcn/aibib.py` 只认围栏里的条目）多出一堆噪声；工具调用
    项更不是文本。结构不对（不是 dict / content 不是 list）一律跳过，绝不让一个
    奇怪 item 把整次成功调用打成失败。
    """
    out = d.get("output")
    parts: list[str] = []
    for item in out if isinstance(out, list) else []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                t = part.get("text")
                if isinstance(t, str):
                    parts.append(t)
    return "".join(parts)


def _responses_finish(d: dict) -> str:
    """把 responses 的 `status` / `incomplete_details.reason` 映射成 chat 那套结束原因。

    映射表（**刻意映射成 chat 的取值**，调用方的判据「非 stop 且非空即标注」因此原样成立）：

    | responses | LAST_FINISH |
    |---|---|
    | `status=completed` | `stop`（干净收尾） |
    | `status=incomplete` + reason `max_output_tokens` | `length`（＝被输出上限截断；⑪ 里那句「调大 SLCN_LLM_MAX_TOKENS」的建议照旧成立） |
    | `status=incomplete` + reason `content_filter` | `content_filter` |
    | `status=incomplete` + 别的原因 / 缺 reason | `incomplete`（不认识的取值原样带上，调用方会照原样显示） |
    | `status=failed` | `failed` |
    | 缺 status / 非字符串 | `""`（老端点没给这个字段：不能当异常，会误报） |
    """
    status = d.get("status")
    if not isinstance(status, str):
        return ""
    s = status.strip().lower()
    if s == "completed":
        return "stop"
    if s == "failed":
        return "failed"
    if s == "incomplete":
        det = d.get("incomplete_details")
        reason = det.get("reason") if isinstance(det, dict) else None
        r = reason.strip().lower() if isinstance(reason, str) else ""
        if r == "max_output_tokens":
            return "length"
        if r == "content_filter":
            return "content_filter"
        return r or "incomplete"
    # in_progress（非流式响应里不该出现）等未知状态：原样带上，绝不假装是 stop
    return s


def _responses_usage_from(d: dict) -> dict[str, int]:
    """把 responses 的 `usage` 归一成**与 chat 同一组键**（另加原生字段，只归一化不过滤）。

    · `input_tokens` → `prompt_tokens`；`output_tokens` → `completion_tokens`；
      `total_tokens` 同名；`output_tokens_details.reasoning_tokens` → `reasoning_tokens`
      （**按输出计费**，与 chat 的 `completion_tokens_details.reasoning_tokens` 同一个含义）。
    · 原生字段（`input_tokens` / `output_tokens` / `input_tokens_details.cached_tokens` …）
      整数值原样保留，端点看账单时用得上；非整数（`true` / `1.5`）一律不收——bool 是
      int 的子类，`"cached": true` 不该被记成 1 个 token（与 `_usage_from` 同一条纪律）。
    """
    u = d.get("usage")
    u = u if isinstance(u, dict) else {}

    def _int(v: object) -> int:
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    inp, out = _int(u.get("input_tokens")), _int(u.get("output_tokens"))
    out = {"prompt_tokens": inp, "completion_tokens": out,
           "total_tokens": _int(u.get("total_tokens"))}
    details = u.get("output_tokens_details")
    details = details if isinstance(details, dict) else {}
    out["reasoning_tokens"] = (_int(details.get("reasoning_tokens"))
                               or _int(u.get("reasoning_tokens")))
    for k, v in u.items():
        if k not in out and isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    # 缓存命中在**嵌套**字段里，与 chat 的 completion_tokens_details 同理，单独取一次。
    ind = u.get("input_tokens_details")
    if isinstance(ind, dict) and "cached_tokens" in ind:
        out["cached_tokens"] = _int(ind.get("cached_tokens"))
    return out


def _responses_search_evidence(d: dict) -> dict:
    """数回复里的**检索证据**：`web_search_call` 输出项 + `url_citation` 注解。

    这是「端点到底有没有真的联网」的唯一机器判据（OpenAI 官方文档：用了 web_search 的
    回复**一定**包含 `web_search_call` 输出项，并通常在 `output_text` 上带 `url_citation`
    注解）。有的端点（如 DeepSeek 的 flash）会静默忽略该工具 → 这里数出来是 0；
    DeepSeek 的 v4-pro 会真检索、能数到 `web_search_call`。

    同时把能取到的检索词与来源 URL 收出来（日志里给编辑看「它查了什么、引了哪些页面」），
    取不到就留空——**不做任何猜测**。
    """
    n_calls = 0
    queries: list[str] = []
    urls: list[str] = []
    out = d.get("output")
    for item in out if isinstance(out, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            n_calls += 1
            action = item.get("action")
            if isinstance(action, dict):
                q = action.get("query")
                if isinstance(q, str) and q.strip():
                    queries.append(q.strip())
                qs = action.get("queries")
                if isinstance(qs, list):
                    queries += [str(x).strip() for x in qs if isinstance(x, str)]
        if item.get("type") == "message":
            for part in item.get("content") if isinstance(item.get("content"), list) else []:
                if not isinstance(part, dict):
                    continue
                for ann in (part.get("annotations")
                            if isinstance(part.get("annotations"), list) else []):
                    if isinstance(ann, dict) and ann.get("type") == "url_citation":
                        u = ann.get("url")
                        if isinstance(u, str) and u.strip():
                            urls.append(u.strip())
    return {"n_calls": n_calls, "queries": queries, "urls": urls}


@dataclass
class LLMConfig:
    base: str = ""
    key: str = ""
    model: str = ""
    # 通道与检索开关。**默认必须是 chat / False** —— 老调用方（`LLMConfig(base, key, model)`）
    # 直接构造时拿到的就是旧行为，不必知道有这两个字段。
    protocol: str = DEFAULT_PROTOCOL
    web_search: bool = False
    # 第 70 条的两个「未来兼容」旋钮：`tools[].type` 的名字与 responses 的路径。
    # **默认值就是今天的硬编码值** —— 直接按位置构造（老调用方 / 既有套件）拿到的 URL
    # 与请求体与从前逐字节相同。
    search_tool: str = DEFAULT_SEARCH_TOOL
    responses_path: str = DEFAULT_RESPONSES_PATH

    @property
    def ready(self) -> bool:
        return bool(self.base and self.model)

    @property
    def uses_web_search(self) -> bool:
        """本次**实际**会不会带检索工具：只有 responses 通道才有服务端工具这东西。

        chat 通道带 `tools` 对 OpenAI 兼容端点意味着 **function calling**（要在客户端
        自己执行），不是检索——把开关理解成「chat 也能检索」是错的，所以这里收口。
        """
        return self.protocol == "responses" and self.web_search


def config_from_env(
    base: str | None = None, key: str | None = None, model: str | None = None,
    protocol: str | None = None, web_search: bool | None = None,
    search_tool: str | None = None, responses_path: str | None = None,
) -> LLMConfig:
    """拼配置。`protocol` / `web_search` / `search_tool` / `responses_path` 为 None 时读环境变量。

    四个连接参数（base/key/model）保持旧签名——老调用方按位置传参不受影响；
    新增的四个只以关键字出现（且默认 None = 读环境变量/默认），所以**旧调用逐字节不变**。
    `search_tool` / `responses_path`（第 70 条）传空串同样按「用默认」处理
    —— 界面上的「留空」语义与引擎一致（见 `resolve_search_tool`）。
    """
    return LLMConfig(
        base=(base or os.environ.get("SLCN_LLM_BASE", "")).rstrip("/"),
        key=key or os.environ.get("SLCN_LLM_KEY", ""),
        model=model or os.environ.get("SLCN_LLM_MODEL", ""),
        protocol=(protocol if protocol is not None else protocol_from_env()),
        web_search=(web_search if web_search is not None else web_search_from_env()),
        search_tool=(search_tool_from_env() if search_tool is None
                     else resolve_search_tool(search_tool)),
        responses_path=(responses_path_from_env() if responses_path is None
                        else resolve_responses_path(responses_path)),
    )


def available(cfg: LLMConfig | None = None) -> bool:
    return (cfg or config_from_env()).ready


# `SLCN_LLM_JSON` 明确写这几个值才算「开」（大小写不敏感）——JSON 模式是**可选扩展**，
# 默认**关**：现行唯一的调用方（`convert.py` ⑪）要的是**整份 bibtex 文本**
# （`refmark.FORMAT_MD` 第 9 条），不是 JSON 对象。默认开有两个实害：
# ① 与规范自相矛盾，模型被逼着把 bibtex 塞进 JSON 字符串，任务书里的建议跟着变形；
# ② 端点会要求 prompt 里出现 "json" 字样（DeepSeek 直接
#    `HTTP 400 Prompt must contain the word 'json'`），把可选参数变成默认故障源。
_JSON_ON = {"1", "true", "yes"}

# `SLCN_LLM_THINKING` 的取值口径（大小写不敏感，两端空白先 strip）。未设 / 空 → 不发该字段。
# ⚠️ 为什么「不发」是默认（而不是显式发 enabled）：官方文档（2026-10，
# https://api-docs.deepseek.com/zh-cn/api/create-chat-completion ）写明 `thinking`
# **默认就是 enabled**；本项目唯一的调用方（⑪ 参考文献核实预跑）是纯抽取/规范化，
# 不需要思维链，显式发 enabled 只等于把默认值抄一遍，却会让请求体不再与从前逐字节
# 相同——老端点/自建服务多收到一个不认识的字段，可能直接 400（`response_format`
# 当年就是这么把整条通道打挂的）。要关就由用户显式 `0`。
_THINKING_OFF = {"0", "false", "no", "off", "none"}
_THINKING_ON = {"1", "true", "yes", "on"}


def request_params_from_env() -> tuple[dict | None, int | None, dict | None]:
    """从环境变量读 `chat()` 的三个可选请求参数，返回 `(response_format, max_tokens, thinking)`。

    环境变量名沿用当年试点脚本用过的 `SLCN_LLM_JSON` / `SLCN_LLM_MAX_TOKENS`：
    **改名会让用户既有的配置静默失效**（表现为「参数没生效」而不是报错），所以固定不变。
    集中在这一个函数里读，是为了让「默认值到底是多少」只有一个出处，
    调用方（`convert.py` 的 `--verify-refs`）不再各写一份口径。

    `SLCN_LLM_JSON`（默认**关**，**留给将来需要结构化 JSON 返回的调用**）：
        - 未设 / `0` / `false` / `no` / 空 → `None`（不带该字段）——
          现行调用方要的是 bibtex 文本，带这个字段只会招来 400；
        - `1` / `true` / `yes`（大小写不敏感）→ `{"type": "json_object"}`，
          让端点保证返回是 JSON，省掉「模型先解释一段、再裹 ```json 围栏」的丢弃。

    `SLCN_LLM_MAX_TOKENS`：
        - 未设 → 2048（`_DEFAULT_MAX_TOKENS`，防啰嗦模型把预算耗在推理上被截半句）；
        - `0` / 负数 / 空（`export SLCN_LLM_MAX_TOKENS=`）→ `None`（= 不封顶，
          端点对 `<=0` 的解释各不相同，索性不带这个字段，让端点用自己的默认上下文）；
        - 非整数 → 2048，并在 stderr 留**一条**说明（不抛异常：一个手滑的配置
          不该让整条 `--verify-refs` 挂掉；同时**不能静默**，否则用户以为封顶生效了）。

    `SLCN_LLM_THINKING`（默认**不发这个字段**，三态；官方文档 2026-10：`thinking`
    默认 enabled、思考模式下 `temperature` **无效果**、推理 token **按输出计费**）：
        - 未设 / 空 → `None`（请求体与从前逐字节相同）；
        - `0` / `false` / `no` / `off` / `none` → `{"type": "disabled"}`——
          ⑪ 是纯抽取/规范化，本来就不需要思维链：关掉才谈得上「temperature=0 的
          确定性」，推理 token 也不会吃掉 `max_tokens`（思考默认 64K，非思考 8K）；
        - `1` / `true` / `yes` / `on` → `{"type": "enabled"}`（显式表态，值同默认）；
        - 其它值 → `None`，并在 stderr 留**一条**说明（与 MAX_TOKENS 同一条纪律：
          不抛异常，也不静默）。
    """
    raw_json = os.environ.get("SLCN_LLM_JSON", "")
    response_format: dict | None = (
        {"type": "json_object"} if raw_json.strip().lower() in _JSON_ON else None
    )

    # 取值大小写不敏感；但**报错要报用户原样写的值**（转成小写再报，用户会以为
    # 自己填错了大小写）。
    raw_thinking = os.environ.get("SLCN_LLM_THINKING", "").strip()
    thinking_key = raw_thinking.lower()
    if not thinking_key:
        thinking: dict | None = None
    elif thinking_key in _THINKING_OFF:
        thinking = {"type": "disabled"}
    elif thinking_key in _THINKING_ON:
        thinking = {"type": "enabled"}
    else:
        # 不抛异常、也不静默：与 SLCN_LLM_MAX_TOKENS 的非整数处理同一个理由。
        thinking = None
        print(f"[llm] SLCN_LLM_THINKING={raw_thinking!r} 不是可识别的开关值"
              f"（0/false/no/off/none 或 1/true/yes/on），按「不发该字段」处理",
              file=sys.stderr, flush=True)

    raw_max = os.environ.get("SLCN_LLM_MAX_TOKENS")
    if raw_max is None:
        return response_format, _DEFAULT_MAX_TOKENS, thinking
    raw_max = raw_max.strip()
    if not raw_max:
        return response_format, None, thinking
    try:
        n = int(raw_max)
    except ValueError:
        print(f"[llm] SLCN_LLM_MAX_TOKENS={raw_max!r} 不是整数，按默认 "
              f"{_DEFAULT_MAX_TOKENS} 处理（要「不封顶」请设为 0）",
              file=sys.stderr, flush=True)
        return response_format, _DEFAULT_MAX_TOKENS, thinking
    return response_format, (None if n <= 0 else n), thinking


def json_mode_rejected() -> bool:
    """最近一次失败是否**因为端点不认 `response_format`**（= 可以不带它重试一次）。

    为什么要有这个：`response_format` 是**可选**扩展，老端点/自建服务常直接
    400 掉这个字段。若不给调用方一条「判据 + 降级」的路，一个可选参数就能让整条
    `--verify-refs` 全灭——那正是升级最不该有的副作用。

    判据刻意**只认 HTTP 400**（认成 500/超时之类会把网络故障误当参数问题，
    白跑一倍请求），且 `LAST_ERROR` 里要出现 `response_format` 或 `json`
    （各家端点的错误文案不一：`response_format` / `json_object` / `JSON mode`）。
    """
    e = LAST_ERROR
    if "HTTP 400" not in e:
        return False
    low = e.lower()
    return "response_format" in low or "json" in low


def thinking_rejected() -> bool:
    """最近一次失败是否**因为端点不认 `thinking` 字段**（= 可以不带它重试一次）。

    与 `json_mode_rejected()` 同一条思路：`thinking` 是可选扩展，端点不认时
    会给 400（有的文案是 `Unknown parameter: 'thinking'`，有的是
    `thinking is not supported`），不能让一个可选参数把整条 `--verify-refs` 打灭。

    判据同样**只认 HTTP 400**（500/超时是服务故障，重试也白搭），且 `LAST_ERROR`
    里要出现 `thinking`。**刻意比 json 那条更窄**：`json` 是个到处出现的词
    （提示词、错误体里都可能有），而 `thinking` 出现在 400 里的理由基本只有一个
    ——「这个字段我不认识」。宁可漏判（用户看到失败原因后自己关掉
    `SLCN_LLM_THINKING`），也不要把上下文超限之类的真失败当成参数问题重试一遍。
    """
    return "HTTP 400" in LAST_ERROR and "thinking" in LAST_ERROR.lower()


def _fail(why: str, http: int = 0, body: str = "") -> None:
    """记下失败原因（stderr + LAST_ERROR / LAST_HTTP / LAST_BODY），返回值仍由调用处给 None。

    `http` / `body` 是**机器可判**的那一半：探针与「体检」⑤ 靠它们区分「端点明确拒绝
    （4xx ⇒ 这个能力不支持）」与「服务故障 / 连不上（这次没成）」——从中文错误串里
    正则抠状态码太脆，所以在这里显式落下来。
    """
    global LAST_ERROR, LAST_HTTP, LAST_BODY
    LAST_ERROR = why
    LAST_HTTP = http
    LAST_BODY = body
    print(f"[llm] 调用失败：{why}", file=sys.stderr, flush=True)


def chat(
    cfg: LLMConfig,
    system: str,
    user: str,
    timeout: int = DEFAULT_TIMEOUT,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    thinking: dict | None = None,
) -> str | None:
    """一次 OpenAI 兼容的对话调用。失败返回 None，不抛异常（原因见 LAST_ERROR / stderr）。

    `response_format` / `max_tokens` / `thinking` 是**可选**扩展，默认全是 None：

    - `response_format={"type": "json_object"}` 让端点保证返回是 JSON（避免模型
      加解释文字或 markdown 围栏，调用方只能整篇丢弃）；
    - `max_tokens` 给输出封顶，避免思维链/啰嗦模型把额度耗在推理上，返回被截在
      半句——报告里只会看到「JSON 解析失败」，看不出是输出预算的问题。
    - `thinking={"type": "disabled"}` 关掉思维链。官方文档（2026-10，
      https://api-docs.deepseek.com/zh-cn/api/create-chat-completion ）写明该字段
      **默认 enabled**，而**思考模式下 `temperature` 没有效果**（本模块一直发
      `temperature: 0` 求确定性，这只在非思考模式成立），推理 token 还**按输出
      计费**（见 `LAST_USAGE["reasoning_tokens"]`）——纯抽取/规范化白烧额度，
      还可能把 `max_tokens` 吃光、返回半份 bibtex。

    **默认 None = 请求体与从前逐字节相同**：老调用方（`convert.py` 的参考文献
    通道）从前两个都不传；现在它**只传 `max_tokens` 与（可选的）`thinking`**（⑪ 用
    `SLCN_LLM_MAX_TOKENS` 给输出封顶，正是为了防截断），`response_format` 仍不传，
    其余行为一行都不变。
    输出长度用 OpenAI 规范的字段名
    `max_tokens`（而不是某些端点原生的 `num_predict`），因为本模块走的是
    `/v1/chat/completions`——兼容端点都认这个名字。

    成功时本次用量写进 `LAST_USAGE`（含 `reasoning_tokens`）、结束原因写进
    `LAST_FINISH`（调用方靠它识别**任何**非 `stop` 的收尾——截断、
    内容过滤、服务端资源不足、被中断等，见 `LAST_FINISH` 的说明）；
    失败时两者都清空成初值（返回值契约不变）。
    """
    global LAST_ERROR, LAST_USAGE, LAST_FINISH, LAST_HTTP, LAST_BODY, LAST_SEARCH
    # 每次调用先清空用量与结束原因：失败（含未配置、异常、HTTP 错误）时它们自然保持
    # 初值，不必在每个 return None 处各写一遍——漏写一处就会把上次的值当成本次的。
    # LAST_HTTP / LAST_BODY / LAST_SEARCH 同理（chat 通道不会有检索证据，必须清掉上次的）。
    LAST_USAGE = {}
    LAST_FINISH = ""
    LAST_HTTP = 0
    LAST_BODY = ""
    LAST_SEARCH = {"n_calls": 0, "queries": [], "urls": []}
    if not cfg.ready:
        LAST_ERROR = "未配置：base/model 为空（见 config_from_env）"
        return None
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    # 只在显式要求时加字段：默认请求体保持字节级不变（老调用方依赖这一点）
    if response_format is not None:
        payload["response_format"] = response_format
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if thinking is not None:
        payload["thinking"] = thinking
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.base}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.key or 'none'}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        # 先取内容：`choices`/`message` 结构不对时这里就抛，直接走下面的失败分支，
        # 于是 LAST_FINISH 不会被写成「半截成功」的原因（失败路径必须是空的）。
        content = d["choices"][0]["message"]["content"]
        LAST_ERROR = ""
        LAST_USAGE = _usage_from(d)
        LAST_FINISH = _finish_from(d)
        return content
    except urllib.error.HTTPError as e:
        # ⚠️ HTTP 错误体和超时**必须分开报**：前者是「端点明确拒绝」（如上下文超限），
        #    后者才是真的网络/超时。以前两者都并成一句「调用失败或超时」。
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", "replace").strip()[:400]
        except Exception:  # noqa: BLE001  读错误体失败也不能影响回退
            pass
        _fail(f"HTTP {e.code} {e.reason}｜{cfg.base}/chat/completions"
              f"（model={cfg.model}）｜{snippet or '(响应体为空)'}",
              http=e.code, body=snippet)
        return None
    except Exception as e:  # noqa: BLE001
        _fail(f"{type(e).__name__}: {e}｜{cfg.base}/chat/completions（model={cfg.model}）")
        return None


def responses(
    cfg: LLMConfig,
    system: str,
    user: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_tokens: int | None = None,
    thinking: dict | None = None,
    web_search: bool | None = None,
) -> str | None:
    """一次 **Responses API**（默认 `{base}/responses`，路径可配）调用。失败返回 None，不抛异常。

    何时用：`cfg.protocol == "responses"`（顶层入口是 `complete()`，调用方通常不必直接
    调它）。为什么不把这条路并进 `chat()`：`chat()` 的请求体**逐字节**被既有套件与老
    调用方钉着，改它风险最大、收益为零——两条通道各写一个函数，共用 `LAST_*` 这一组
    出口，调用方（`convert.py` ⑪）一行都不用改。

    请求体（字段名严格照 DeepSeek「使用 Responses API」与 OpenAI Responses 规范）：
        `model` / `instructions`（= 我们的 system）/ `input`（= 我们的 user）/
        `temperature`；
        `max_output_tokens`（**不是** `max_tokens`：写错会被静默忽略 → 表现为「没封顶」）；
        `reasoning={"effort": …}`（见下）；
        `tools=[{"type":<cfg.search_tool>}]`（**只在 `web_search=True` 时**，见模块头的能力
        边界与第 70 条：工具名默认 `web_search`，可用 `SLCN_LLM_SEARCH_TOOL` 改）。

    `thinking` 的映射（**不知道为什么这么映射，就会以为「开启」没生效**）：
        · `{"type":"disabled"}` → `reasoning={"effort":"none"}`（**精确**等价：关掉思考）；
        · 其它（含 `enabled` 与 None）→ **不发 `reasoning` 字段**。为什么 `enabled` 也
          不发：Responses 的 `effort` 只有 none/low/high/max，没有「模型默认」这一档；
          猜一个 `high` 会**静默改变**成本与行为（chat 通道的 `{"type":"enabled"}` 却正好
          等于默认值）。宁可少发一个字段，也不替用户做这个决定——这与「不许静默假装」
          是同一条纪律。

    ⚠️ 与 chat 通道的一处**能力差异**：Responses 用 `text.format` 做 JSON 模式，不是 chat
    的 `response_format`；本函数**不收** `response_format`（现行调用方要的是 bibtex 文本，
    不是 JSON；多一个入口就多一处要维护的判据）。要 JSON 请在提示词里要求。
    """
    global LAST_ERROR, LAST_USAGE, LAST_FINISH, LAST_HTTP, LAST_BODY, LAST_SEARCH
    LAST_USAGE = {}
    LAST_FINISH = ""
    LAST_HTTP = 0
    LAST_BODY = ""
    LAST_SEARCH = {"n_calls": 0, "queries": [], "urls": []}
    if not cfg.ready:
        LAST_ERROR = "未配置：base/model 为空（见 config_from_env）"
        return None

    endpoint = f"{cfg.base}{cfg.responses_path}"
    payload: dict = {
        "model": cfg.model,
        "instructions": system,
        "input": user,
        "temperature": 0,
    }
    if max_tokens is not None:
        payload[_PROTOCOL_MAX_OUTPUT_FIELD] = max_tokens
    if isinstance(thinking, dict) and str(thinking.get("type", "")).lower() == "disabled":
        payload["reasoning"] = {"effort": "none"}
    # 检索开关：`web_search=None` 时看 cfg（配置即契约），显式传参只给探针/测试用。
    want_search = cfg.uses_web_search if web_search is None else bool(web_search)
    if want_search:
        # 工具类型名走配置（第 70 条）：默认 `web_search` 与从前逐字节相同；
        # 将来服务商换成别的名字（`web_search_preview` 之类）只需改配置。
        payload["tools"] = [{"type": cfg.search_tool}]

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.key or 'none'}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        # 先取文本再写状态：结构不对时这里就抛，直接走失败分支，LAST_FINISH 不会被写成
        # 「半截成功」的原因（失败路径必须是空的——与 chat 同一条纪律）。
        text = _responses_text(d)
        LAST_ERROR = ""
        LAST_USAGE = _responses_usage_from(d)
        LAST_FINISH = _responses_finish(d)
        LAST_SEARCH = _responses_search_evidence(d)
        return text
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", "replace").strip()[:400]
        except Exception:  # noqa: BLE001
            pass
        _fail(f"HTTP {e.code} {e.reason}｜{endpoint}（model={cfg.model}）"
              f"｜{snippet or '(响应体为空)'}", http=e.code, body=snippet)
        return None
    except Exception as e:  # noqa: BLE001
        _fail(f"{type(e).__name__}: {e}｜{endpoint}（model={cfg.model}）")
        return None


def complete(
    cfg: LLMConfig,
    system: str,
    user: str,
    timeout: int = DEFAULT_TIMEOUT,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    thinking: dict | None = None,
) -> str | None:
    """按 `cfg.protocol` 选通道的**统一入口**：`chat`（默认）/ `responses`。

    ⚠️ `chat` 分支是**原样转发**（一个字段都不加），所以默认配置下的请求体与从前
    逐字节相同——「不改老行为」这条保证只在这里成立一次，别在别处再分叉一份。
    `response_format` 只对 chat 有意义（Responses 用 `text.format`），responses 分支
    收下但不用，见 `responses()` 的说明。

    默认 `response_format=None` 时两条通道都不受影响。调用方（`convert.py` ⑪）
    显式传 `response_format=None`（要的是 bibtex 文本），与从前完全一致。
    """
    if cfg.protocol == "responses":
        return responses(cfg, system, user, timeout=timeout,
                         max_tokens=max_tokens, thinking=thinking)
    return chat(cfg, system, user, timeout=timeout,
                response_format=response_format, max_tokens=max_tokens, thinking=thinking)


# ── 能力探针：这个端点到底会不会真的联网检索？────────────────────────────────
#
# 为什么探针不能只看 HTTP 状态码：**有的端点/模型（如 DeepSeek 的 deepseek-flash）
# 会静默忽略 `tools:[{"type":"web_search"}]`**（官方兼容性表把 web_search 列为「忽略」，
# 并写明不支持的参数不报错；同厂 `deepseek-v4-pro` 则会真检索）。于是「HTTP 200」既可
# 能表示「真检索了」，也可能表示「啥也没干」——只看状态码就会把后者报成「支持」，
# 正是用户最反对的「静默假装」。所以判据是**回复里有没有检索证据**。

# 探针的提问：必须是一个「不检索就答不准」的问法，并且**明确要求给出官方来源 URL**——
# 这样既能触发工具的调用决定，返回的 `url_citation` 也顺便证明它真读了页面。
# 为什么不用「今天有什么新闻」这类：结果每天都在变，人没法复核探针本身对不对；
# 用「某年某奖授予谁」这种**事后确定**的事实，即使模型凭记忆答对，也**没有检索证据**
# （n_calls == 0）→ 探针照样如实报「不支持」。这就是我们要的区分力。
PROBE_QUERY = (
    "请使用联网检索工具查证一个问题，并在回答里给出你实际打开过的官方来源 URL："
    "2025 年诺贝尔物理学奖授予了哪几位科学家、分别因什么贡献？"
)
PROBE_SYSTEM = ("你是核实助手。必须先用联网检索工具查到官方来源，再作答；"
                "回答末尾列出检索到的来源 URL。查不到就明说「未查到」，不要凭记忆作答。")
# 探针的输出上限：只要它搜完给几行结论，不需要长文（省额度）。
PROBE_MAX_OUTPUT_TOKENS = 512

# 判词（中文，直接给界面/人看）。三态**不许混**：支持 / 不支持 / 其他错误。
_PROBE_VERDICT_CN = {
    "supported": "支持",
    "unsupported": "不支持",
    "error": "其他错误",
}


def probe_web_search(base: str, key: str = "", model: str = "",
                     timeout: int = DEFAULT_TIMEOUT,
                     query: str = PROBE_QUERY,
                     search_tool: str | None = None,
                     responses_path: str | None = None) -> dict:
    """探测 `{base}{responses_path}` **是否真的会联网检索**，返回一份可审计的结论 dict。

    三种判词（`verdict` 是机器可判的键，`verdict_cn` 是给界面/人的中文）：

    | verdict | 什么时候 | 编辑该看到的话 |
    |---|---|---|
    | `supported` | HTTP 200 **且**回复里有 `web_search_call`（检索证据） | 「这个端点支持检索 → 会真的联网查官方来源」 |
    | `unsupported` | ① HTTP 4xx（端点**明确拒绝**，如不认 `tools` / 工具名）；或 ② HTTP 200 但**没有**检索证据（如 DeepSeek flash 的静默忽略） | 「不支持 → 模型只能凭记忆」 |
    | `error` | 连不上 / 超时 / 5xx / 回复不是合法 JSON / 没配置 base+model | 「这次没测出来」（**不能**当成「不支持」，也不能当成「支持」） |

    ⚠️ 判据**永远**是「回复里有没有检索证据」，不是状态码（第 69 条；DeepSeek 会 200
    但什么都没干）。第 70 条起这里**用的就是配置里的路径与工具名**：探针必须和真实转换
    发同一种请求，否则「体检说支持、转换却打另一个地址」正是最坏的假证据。
    `search_tool` / `responses_path` 传 None 时读环境变量（空值同样按默认，见
    `resolve_search_tool` / `resolve_responses_path`）。

    返回字段（都留着，界面与文档要能对账）：`verdict` / `verdict_cn` / `detail`（中文
    一句话，含端点原话）/ `http_code` / `body`（端点响应体片段）/ `error`（LAST_ERROR
    全文）/ `endpoint` / `search_tool` / `model` / `n_search_calls` / `queries` / `urls` /
    `reply_chars`。

    ⚠️ 路径只拼一次 `{base}{路径}`（**不猜**要不要补 `/v1`）：DeepSeek 文档给的
    Responses base_url 是 `https://api.deepseek.com`（不带 /v1），OpenAI 是
    `https://api.openai.com/v1`——两种写法都被这一条规则覆盖，`endpoint` 字段里能看到
    实际打的地址。端点 404 时请把地址改成服务商文档写的那一个（或改
    `SLCN_LLM_RESPONSES_PATH`，第 70 条）。
    """
    base = (base or "").rstrip("/")
    tool = search_tool_from_env() if search_tool is None else resolve_search_tool(search_tool)
    path = (responses_path_from_env() if responses_path is None
            else resolve_responses_path(responses_path))
    endpoint = f"{base}{path}"
    res: dict = {
        "verdict": "error", "verdict_cn": _PROBE_VERDICT_CN["error"], "detail": "",
        "http_code": 0, "body": "", "error": "",
        "endpoint": endpoint, "search_tool": tool, "model": model,
        "n_search_calls": 0, "queries": [], "urls": [], "reply_chars": 0,
    }
    if not base or not model:
        res["detail"] = "没配置：base/model 为空（先在「设置」页填写 API 地址与模型名）"
        return res

    cfg = LLMConfig(base=base, key=key, model=model,
                    protocol="responses", web_search=True,
                    search_tool=tool, responses_path=path)
    out = responses(cfg, PROBE_SYSTEM, query, timeout=timeout,
                    max_tokens=PROBE_MAX_OUTPUT_TOKENS, web_search=True)
    res["http_code"] = LAST_HTTP
    res["body"] = LAST_BODY
    res["error"] = LAST_ERROR

    if out is None:
        # 4xx ⇒ 端点**明确**拒绝这个请求（多数情况下就是「不认 tools / 这个工具名」）。
        if 400 <= LAST_HTTP < 500:
            res["verdict"] = "unsupported"
            res["detail"] = (f"端点明确拒绝（HTTP {LAST_HTTP}）："
                             f"{LAST_BODY or LAST_ERROR or '（响应体为空）'}"
                             "——按「不支持」处理：模型只能凭记忆。")
        else:
            res["detail"] = (f"没测出来（{LAST_ERROR or '未拿到 HTTP 响应'}）："
                             "请检查地址是否可达、密钥是否正确、模型名是否存在。"
                             "这一次既不能算「支持」也不能算「不支持」。")
        res["verdict_cn"] = _PROBE_VERDICT_CN[res["verdict"]]
        return res

    ev = dict(LAST_SEARCH or {})
    res["n_search_calls"] = int(ev.get("n_calls", 0))
    res["queries"] = list(ev.get("queries", []))
    res["urls"] = list(ev.get("urls", []))
    res["reply_chars"] = len(out)
    if res["n_search_calls"] > 0:
        res["verdict"] = "supported"
        res["detail"] = (f"端点真的检索了（收到 {res['n_search_calls']} 个 web_search_call"
                         f"{'，' + str(len(res['urls'])) + ' 个来源 URL' if res['urls'] else ''}）"
                         f"——会真的联网查官方来源（本次请求的工具名 `{tool}`）。")
    else:
        # ⚠️ 最关键的一格：HTTP 200 不等于检索过。DeepSeek 官方文档把 web_search 列为
        #    「忽略」，所以这里必须按「不支持」报，绝不能让编辑以为已经联网核实了。
        res["verdict"] = "unsupported"
        res["detail"] = (f"端点返回 200，但回复里**没有任何检索证据**（没有 web_search_call）"
                         f"——它把工具 `{tool}` **静默忽略了**（例如 DeepSeek 的 "
                         "`deepseek-flash`：兼容性表仍把 web_search 列为「忽略」、不报错；"
                         "同厂 `deepseek-v4-pro` 则会真检索）。"
                         "按「不支持」处理：模型只能凭记忆。")
    res["verdict_cn"] = _PROBE_VERDICT_CN[res["verdict"]]
    return res


def probe_cli(argv: list[str] | None = None) -> int:
    """`python -m slcn.llm --probe …`：给界面与人的**唯一一份**探针实现。

    为什么做成 CLI 而不是让 Swift 自己发这个请求：协议（路径、`tools` 形状、检索证据的
    判据）只能有**一份**实现，否则界面说「支持」而引擎跑出来没检索，编辑不知道信谁。

    `--json` 时 **stdout 只有一行 JSON**（给人看的中文一律走 stderr），界面直接解析它；
    不给 `--json` 就打印中文清单，人看。

    退出码：`0` 支持 / `1` 不支持 / `2` 其他错误或用法错误 ——
    脚本据此判读（与 checkbib.py 一样，**同一个 0/1 在不同模式含义不同**这件事要写清）。
    """
    ap = argparse.ArgumentParser(
        prog="python -m slcn.llm",
        description="SLCN LLM 通道的小工具（当前只有能力探针）",
        epilog=("退出码：0 支持 / 1 不支持 / 2 其他错误或用法错误。\n"
                "密钥请用环境变量 SLCN_LLM_KEY（不要写进命令行：argv 在 ps 里对同机"
                "所有用户可见）。"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--probe", action="store_true",
                    help="探测 {base}{路径} 是否真的会联网检索（默认路径 /responses）")
    ap.add_argument("--base", default=None, help="base URL（缺省读 SLCN_LLM_BASE）")
    ap.add_argument("--key", default=None, help="API 密钥（缺省读 SLCN_LLM_KEY；**别写进命令行**）")
    ap.add_argument("--model", default=None, help="模型名（缺省读 SLCN_LLM_MODEL）")
    # 第 70 条：探针必须与真实转换发同一种请求，否则「体检说支持」不算证据。
    ap.add_argument("--llm-search-tool", default=None,
                    help="tools[].type 的工具名（缺省读 SLCN_LLM_SEARCH_TOOL / 默认 web_search）")
    ap.add_argument("--llm-responses-path", default=None,
                    help="responses 的路径（缺省读 SLCN_LLM_RESPONSES_PATH / 默认 /responses）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="超时秒数（默认 60）")
    ap.add_argument("--json", action="store_true", help="stdout 只输出一行 JSON（给界面解析）")
    a = ap.parse_args(argv)

    if not a.probe:
        ap.error("目前只实现了 --probe（例：python -m slcn.llm --probe --base URL --model M）")

    base = (a.base or os.environ.get("SLCN_LLM_BASE", "")).strip()
    key = a.key if a.key is not None else os.environ.get("SLCN_LLM_KEY", "")
    model = (a.model or os.environ.get("SLCN_LLM_MODEL", "")).strip()
    res = probe_web_search(base, key, model, timeout=a.timeout,
                           search_tool=a.llm_search_tool,
                           responses_path=a.llm_responses_path)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, sort_keys=True), flush=True)
    else:
        print(f"检索探针：{res['verdict_cn']}（{res['endpoint']}，"
              f"工具 {res['search_tool']}，模型 {res['model'] or '—'}）")
        print(f"  {res['detail']}")
        if res["error"]:
            print(f"  端点原话：{res['body'] or res['error']}")
        if res["urls"]:
            print("  来源 URL：" + "、".join(res["urls"][:5]))
    return {"supported": 0, "unsupported": 1}.get(res["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(probe_cli())


_EXTRACT_SYS = """你是学术编辑助手，负责把参考文献条目整理成 BibLaTeX 字段。

**铁律**：
1. **只做抽取，绝不编造**。原文里没有的信息，值写空字符串 ""。
   宁可留空，也不能凭记忆填一个「看起来对」的值——填错的卷号比空缺更糟。
2. 只输出 JSON，不要任何解释、不要 markdown 代码块围栏。
3. 字段不存在或无法确定时，用 ""。

输出 JSON 的结构：
{"author": "", "title": "", "journal": "", "booktitle": "", "volume": "",
 "number": "", "pages": "", "publisher": "", "address": "", "year": ""}

约定：
- pages 用双短横：112--119
- volume/number 用阿拉伯数字，去掉前导零（03 → 3）
- 书籍的出版地放 address，出版社放 publisher；中文条目常见「北京: 商务印书馆」
"""


def extract_fields(cfg: LLMConfig, entry_text: str) -> dict[str, str]:
    """让模型把一条参考文献抽成字段。**只抽取，不补全。**"""
    out = chat(cfg, _EXTRACT_SYS, entry_text)
    if not out:
        return {}
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return {k: str(v).strip() for k, v in d.items() if isinstance(v, (str, int))}


_FILL_SYS = """你是学术编辑助手。用户会给你一条中文参考文献，以及它当前已解析出的字段。

任务：根据**条目原文里确实写到的信息**，指出哪些字段可以补全。
**严禁凭记忆补充原文没有的信息**——如果原文没写卷号，就返回空字符串，
不要凭印象填。你的输出会被编辑逐条核实。

只输出 JSON，不要解释、不要 markdown 围栏：
{"suggest": {"字段名": "建议值"}, "missing": ["原文中缺失、需查证的字段名"], "note": ""}
"""


def suggest_completion(cfg: LLMConfig, entry_text: str, current: dict[str, str]) -> dict:
    """保守补全：仅当原文里确有线索时才建议，并另列「需查证」清单。"""
    user = f"条目原文：\n{entry_text}\n\n当前已解析字段：\n{json.dumps(current, ensure_ascii=False)}"
    out = chat(cfg, _FILL_SYS, user)
    if not out:
        return {}
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
