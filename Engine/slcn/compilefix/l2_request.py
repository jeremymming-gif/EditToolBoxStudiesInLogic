"""Frozen, explicitly authorized L2 requests and single-attempt transport.

This module intentionally has no dependency on ``slcn.llm``.  A caller must
provide every effective setting and a credential binding at preparation, then
the same key at send time. This module never reads credentials from the process
environment.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .files import REPLY_LIMIT, decode_json
from .model import MAX_DIAGNOSTICS
from .state import MAX_ROUNDS


class RequestError(ValueError):
    """A safe-to-display validation or pre-send failure."""


_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_ID = re.compile(r"[A-Za-z0-9_-]+")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_SCHEMA = "compilefix-l2-request-v2"
_DEFAULT_SEARCH_TOOL = "web_search"
_DEFAULT_RESPONSES_PATH = "/responses"
_DEFAULT_MAX_TOKENS = 2048  # Existing R3 request default.
DEFAULT_TIMEOUT = 60  # urllib socket operation timeout, not a total deadline.


def _canonical(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise RequestError("请求身份或范围不是有效的 JSON 数据") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint_api_key(api_key: str | None) -> str | None:
    """Return a non-reversible, domain-separated fingerprint, never the key."""
    if api_key is None or api_key == "":
        return None
    if not isinstance(api_key, str) or not re.fullmatch(r"[!-~]+", api_key):
        raise RequestError("显式 API key 格式无效")
    return _sha(b"compilefix-key-fingerprint-v1\0" + api_key.encode("ascii"))


def _valid_credential_binding(configured, fingerprint) -> bool:
    if configured is False:
        return fingerprint is None
    return (configured is True and isinstance(fingerprint, str)
            and _FINGERPRINT.fullmatch(fingerprint) is not None)


def _safe_url(base: str, protocol: str, path: str) -> str:
    if (not isinstance(base, str) or not base or base != base.strip()
            or re.search(r"[^\x21-\x7e]|[?#@\\]", base)):
        raise RequestError("API base 必须是显式且不含凭据/查询参数的地址")
    base = base.rstrip("/")
    try:
        parsed = urlsplit(base)
        host, _port = parsed.hostname, parsed.port
    except ValueError:
        raise RequestError("API 地址无效") from None
    if not host or (parsed.scheme != "https" and not (
            parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"})):
        raise RequestError("API 地址须使用 HTTPS；仅允许 loopback HTTP")
    if protocol == "chat":
        return base + "/chat/completions"
    if (not isinstance(path, str) or not path.strip("/")
            or re.search(r"[^\x21-\x7e]|[?#@\\\"'{},]", path) or "://" in path):
        raise RequestError("Responses 路径无效")
    return base + "/" + path.strip("/")


def _validate_settings(settings: dict) -> dict:
    if type(settings) is not dict:
        raise RequestError("必须显式提供请求设置对象")
    required = {"base", "model", "protocol", "ai_enabled", "compile_fix_enabled",
                "article_budget_tokens", "batch_budget_tokens"}
    if not required.issubset(settings):
        raise RequestError("请求设置不完整")
    if settings["ai_enabled"] is not True or settings["compile_fix_enabled"] is not True:
        raise RequestError("AI 与编译修复开关必须同时显式开启")
    if not isinstance(settings["model"], str) or not settings["model"].strip():
        raise RequestError("模型必须显式设置")
    protocol = settings["protocol"]
    if protocol not in ("chat", "responses"):
        raise RequestError("不支持的请求协议")
    for key in ("article_budget_tokens", "batch_budget_tokens"):
        if type(settings[key]) is not int or settings[key] < 0:
            raise RequestError("预算设置必须是非负整数")
    max_tokens = settings.get("max_tokens", _DEFAULT_MAX_TOKENS)
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        raise RequestError("输出 token 上限必须为正整数或未设置")
    web_search = settings.get("web_search", False)
    if type(web_search) is not bool:
        raise RequestError("联网工具开关必须显式为布尔值")
    search_tool = settings.get("search_tool") or _DEFAULT_SEARCH_TOOL
    if not isinstance(search_tool, str) or not _TOOL_NAME.fullmatch(search_tool):
        raise RequestError("检索工具类型名无效")
    path = settings.get("responses_path") or _DEFAULT_RESPONSES_PATH
    url = _safe_url(settings["base"], protocol, path)
    thinking = settings.get("thinking")
    if type(thinking) is bool:
        thinking = {"type": "enabled" if thinking else "disabled"}
    if thinking is not None and (type(thinking) is not dict or set(thinking) != {"type"}
                                 or thinking["type"] not in ("disabled", "enabled")):
        raise RequestError("思考模式设置无效")
    if protocol == "responses" and thinking == {"type": "enabled"}:
        thinking = None  # Responses uses the service default for enabled.
    return {
        "base": settings["base"].rstrip("/"), "model": settings["model"],
        "protocol": protocol, "url": url,
        "web_search": web_search if protocol == "responses" else False,
        "search_tool": search_tool if protocol == "responses" and web_search else None,
        "max_tokens": max_tokens, "thinking": thinking,
        "article_budget_tokens": settings["article_budget_tokens"],
        "batch_budget_tokens": settings["batch_budget_tokens"],
    }


def _validate_scope(scope: dict) -> dict:
    if type(scope) is not dict or set(scope) != {"evidence_ids"}:
        raise RequestError("范围必须仅含 evidence_ids")
    ids = scope["evidence_ids"]
    if (type(ids) is not list or not 1 <= len(ids) <= MAX_DIAGNOSTICS or any(
            not isinstance(item, str) or not _ID.fullmatch(item) for item in ids)
            or len(ids) != len(set(ids))):
        raise RequestError("证据范围必须是非空、无重复的 ID 列表")
    return json.loads(_canonical(scope).decode("utf-8"))


def _payload(config: dict, system: str, user: str) -> bytes:
    if config["protocol"] == "chat":
        payload = {"model": config["model"], "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}], "temperature": 0}
        if config["max_tokens"] is not None:
            payload["max_tokens"] = config["max_tokens"]
        if isinstance(config["thinking"], dict):
            payload["thinking"] = config["thinking"]
    else:
        payload = {"model": config["model"], "instructions": system,
                   "input": user, "temperature": 0}
        if config["max_tokens"] is not None:
            payload["max_output_tokens"] = config["max_tokens"]
        if (isinstance(config["thinking"], dict)
                and str(config["thinking"].get("type", "")).lower() == "disabled"):
            payload["reasoning"] = {"effort": "none"}
        if config["web_search"]:
            payload["tools"] = [{"type": config["search_tool"]}]
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except UnicodeError:
        raise RequestError("请求提示不是有效的 UTF-8 文本") from None


def _authorization_id(request_id, round_number, identity, scope, target, model,
                      protocol, settings_fingerprint, credential_fingerprint,
                      body_sha256, summary):
    return _sha(_canonical([_SCHEMA, request_id, round_number, identity, scope, target,
                            model, protocol, settings_fingerprint, credential_fingerprint,
                            body_sha256, summary]))


@dataclass(frozen=True)
class FrozenRequest:
    """Reviewable request whose body bytes are exactly the later sent bytes."""

    request_id: str
    round_number: int
    identity: dict
    scope: dict
    target: str
    model: str
    protocol: str
    settings_fingerprint: str
    credential_fingerprint: str | None = field(repr=False)
    body: bytes = field(repr=False)
    body_sha256: str
    authorization_id: str
    summary: dict

    def to_record(self) -> dict:
        return deepcopy({
            "schema": _SCHEMA, "request_id": self.request_id,
            "round_number": self.round_number, "identity": self.identity,
            "scope": self.scope, "target": self.target, "model": self.model,
            "protocol": self.protocol,
            "settings_fingerprint": self.settings_fingerprint,
            "credential_fingerprint": self.credential_fingerprint,
            "body": self.body.decode("utf-8"), "body_sha256": self.body_sha256,
            "authorization_id": self.authorization_id, "summary": self.summary,
        })

    def _check_integrity(self):
        if (_sha(self.body) != self.body_sha256
                or type(self.summary) is not dict
                or not _valid_credential_binding(self.summary.get("key_configured"),
                                                 self.credential_fingerprint)
                or self.authorization_id != _authorization_id(
                    self.request_id, self.round_number, self.identity, self.scope,
                    self.target, self.model, self.protocol, self.settings_fingerprint,
                    self.credential_fingerprint, self.body_sha256, self.summary)):
            raise RequestError("冻结请求完整性校验失败")

    @classmethod
    def from_record(cls, record: dict) -> "FrozenRequest":
        if type(record) is not dict or record.get("schema") != _SCHEMA:
            raise RequestError("冻结请求记录版本无效")
        try:
            if not isinstance(record["body"], str):
                raise RequestError("冻结请求必须保存 UTF-8 文本")
            value = cls(record["request_id"], record["round_number"],
                        deepcopy(record["identity"]), deepcopy(record["scope"]), record["target"],
                        record["model"], record["protocol"], record["settings_fingerprint"],
                        record["credential_fingerprint"],
                        record["body"].encode("utf-8"), record["body_sha256"],
                        record["authorization_id"], deepcopy(record["summary"]))
            value._check_integrity()
            return value
        except (KeyError, UnicodeError, TypeError, RecursionError) as exc:
            raise RequestError("冻结请求记录不完整") from exc


def prepare_request(*, identity: dict, request_id: str, round_number: int,
                    scope: dict, settings: dict, system: str, user: str,
                    key_configured: bool, credential_fingerprint: str | None) -> FrozenRequest:
    if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
        raise RequestError("请求 ID 无效")
    if type(round_number) is not int or not 1 <= round_number <= MAX_ROUNDS:
        raise RequestError("请求轮次超出允许范围")
    if type(identity) is not dict or not identity:
        raise RequestError("必须提供 taskbook identity")
    identity_copy = json.loads(_canonical(identity))
    scope = _validate_scope(scope)
    if not isinstance(system, str) or not isinstance(user, str):
        raise RequestError("请求提示必须为文本")
    config = _validate_settings(settings)
    if not _valid_credential_binding(key_configured, credential_fingerprint):
        raise RequestError("API key 指纹与配置状态不一致")
    body = _payload(config, system, user)
    settings_fingerprint = _sha(_canonical(config))
    body_sha256 = _sha(body)
    summary = {
        "base": config["base"], "target": config["url"], "model": config["model"],
        "key_configured": key_configured,
        "protocol": config["protocol"],
        "tools": {"web_search": config["web_search"], "search_tool": config["search_tool"]},
        "max_tokens": config["max_tokens"], "thinking": deepcopy(config["thinking"]),
        "article_budget_tokens": config["article_budget_tokens"],
        "batch_budget_tokens": config["batch_budget_tokens"],
        "identity_sha256": _sha(_canonical(identity_copy)),
        "scope_sha256": _sha(_canonical(scope)), "body_sha256": body_sha256,
        "body_bytes": len(body),
    }
    authorization_id = _authorization_id(request_id, round_number, identity_copy, scope,
                                         config["url"], config["model"], config["protocol"],
                                         settings_fingerprint, credential_fingerprint,
                                         body_sha256, summary)
    return FrozenRequest(request_id, round_number, identity_copy, scope, config["url"],
                         config["model"], config["protocol"], settings_fingerprint,
                         credential_fingerprint, body, body_sha256, authorization_id, summary)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _declared_response_length(response, limit: int) -> int | None:
    headers = getattr(response, "headers", None)
    values = headers.get_all("Content-Length") if headers is not None else None
    if values is None:
        return None
    if headers.get_all("Transfer-Encoding") is not None:
        raise RequestError("HTTP 回件同时声明传输编码和正文长度")
    lengths = set()
    for value in values:
        value = value.strip(" \t")
        # Reject combined fields: urllib does not frame a body from a comma list.
        if not re.fullmatch(r"[0-9]+", value):
            raise RequestError("HTTP 回件声明的正文长度无效")
        lengths.add(value.lstrip("0") or "0")
    if len(lengths) != 1:
        raise RequestError("HTTP 回件声明的正文长度冲突")
    (length,) = lengths
    if len(length) > len(str(limit)) or int(length) > limit:
        raise RequestError("HTTP 回件超过本次上限")
    return int(length)


def send_request(frozen: FrozenRequest, *, authorization_id: str,
                 compile_fix_terms_ack: bool, identity: dict, scope: dict,
                 settings: dict, api_key: str, consume, response_limit: int,
                 timeout: float = DEFAULT_TIMEOUT, _test_urlopen=None) -> dict:
    """Consume once, send the exact body once, and classify any lost reply unknown.

    The raw HTTP reply may not exceed REPLY_LIMIT. ``timeout`` is urllib's
    socket operation timeout and does not set a deadline for the whole request.
    ``_test_urlopen`` is a Python-only test hook, not request or CLI input.
    """
    if not isinstance(frozen, FrozenRequest):
        raise RequestError("冻结请求完整性校验失败")
    frozen._check_integrity()
    config = _validate_settings(settings)
    current_scope = _validate_scope(scope)
    if (authorization_id != frozen.authorization_id or compile_fix_terms_ack is not True
            or _canonical(identity) != _canonical(frozen.identity)
            or _canonical(current_scope) != _canonical(frozen.scope)
            or config["url"] != frozen.target or config["model"] != frozen.model
            or config["protocol"] != frozen.protocol
            or _sha(_canonical(config)) != frozen.settings_fingerprint):
        raise RequestError("授权、身份、范围或请求设置已变化；必须重新准备并确认")
    fingerprint = fingerprint_api_key(api_key)
    if fingerprint is None:
        raise RequestError("必须显式提供 API key；不会继承父进程旧 key")
    if frozen.credential_fingerprint is None or not hmac.compare_digest(
            fingerprint, frozen.credential_fingerprint):
        raise RequestError("API key 已变化；必须重新准备并确认请求")
    if type(timeout) not in {int, float} or not 0 < timeout < math.inf:
        raise RequestError("网络超时必须显式为正数")
    if type(response_limit) is not int or not 0 < response_limit <= REPLY_LIMIT:
        raise RequestError(f"HTTP 回件上限须为 1 至 {REPLY_LIMIT} 字节")
    if _test_urlopen is None:
        open_once = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
    else:
        open_once = _test_urlopen
    if not callable(open_once) or not callable(consume):
        raise RequestError("缺少传输或一次性授权消费回调")
    request = urllib.request.Request(
        frozen.target, data=frozen.body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST")
    if consume(frozen.request_id, frozen.body_sha256) is not True:
        raise RequestError("请求授权已消费或无法持久化消费状态")
    try:
        with open_once(request, timeout=timeout) as response:
            try:
                expected_length = _declared_response_length(response, response_limit)
            except RequestError as exc:
                return {"status": "unknown", "error": str(exc)}
            chunks = []
            remaining = response_limit + 1
            while remaining:
                size = min(8192, remaining)
                chunk = response.read(size)
                if type(chunk) is not bytes or len(chunk) > size:
                    return {"status": "unknown", "error": "HTTP 回件读取无效"}
                if chunk == b"":
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if remaining == 0:
                return {"status": "unknown", "error": "HTTP 回件超过本次上限"}
            if expected_length is not None and expected_length != response_limit + 1 - remaining:
                return {"status": "unknown", "error": "HTTP 回件与声明的正文长度不符"}
            raw = b"".join(chunks)
            data = decode_json(raw)
            if frozen.protocol == "chat":
                source = data["choices"][0]["message"]["content"]
                finish = data["choices"][0].get("finish_reason")
                if not isinstance(source, str) or not source.strip() or finish != "stop":
                    return {"status": "unknown", "error": "Chat 回件无可用完整文本"}
            else:
                if data.get("status") != "completed":
                    return {"status": "unknown", "error": "Responses 回件未完整结束"}
                source = "".join(
                    part.get("text", "")
                    for item in data.get("output", []) if isinstance(item, dict)
                    and item.get("type") == "message"
                    for part in item.get("content", []) if isinstance(part, dict)
                    and part.get("type") == "output_text")
                if not source.strip():
                    return {"status": "unknown", "error": "Responses 回件无可用文本"}
            return {"status": "reply", "source_text": source, "source_bytes": raw}
    except urllib.error.HTTPError as exc:
        exc.close()
        return {"status": "unknown", "error": "服务端未返回可用的成功回件"}
    except Exception:  # Do not disclose exception text from headers or provider data.
        return {"status": "unknown", "error": "请求已发出但未收到可用回件"}
