"""Protocol-byte and authorization tests; network is replaced in a child process."""

import base64
from email.message import Message
import hashlib
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import urllib.request
from urllib.response import addinfourl


ENGINE = Path(__file__).resolve().parents[2] / "Engine"
if not (ENGINE / "convert.py").is_file() or not (ENGINE / "slcn/compilefix/l2_request.py").is_file():
    raise ImportError("Run this test from the repository after applying the L2 transport candidate.")
sys.path.insert(0, str(ENGINE))

FLET_APP = Path(__file__).resolve().parents[1] / "app"
if not FLET_APP.is_dir():
    FLET_APP = Path(__file__).resolve().parents[2].parent / "flet" / "sources-flet" / "app"
if FLET_APP.is_dir():
    sys.path.insert(0, str(FLET_APP))

from slcn.compilefix.l2_request import (  # noqa: E402
    DEFAULT_TIMEOUT,
    REPLY_LIMIT,
    FrozenRequest,
    RequestError,
    fingerprint_api_key,
    prepare_request,
    send_request,
)

from services import engine_bridge as flet_engine_bridge  # noqa: E402


IDENTITY = {"candidate_sha256": "c" * 64, "task_id": "task-1", "round": 1}
SCOPE = {"evidence_ids": ["e1", "e2"]}
TEST_KEY = "test-only-not-real"
COMMON = {
    "base": "https://api.example.invalid/v1",
    "model": "model-test",
    "protocol": "chat",
    "ai_enabled": True,
    "compile_fix_enabled": True,
    "article_budget_tokens": 30000,
    "batch_budget_tokens": 1000000,
    "max_tokens": 2048,
    "thinking": {"type": "disabled"},
}


def settings_for(protocol):
    return dict(COMMON, protocol=protocol, web_search=True, search_tool="web_search_preview",
                responses_path="/v1/responses")


def frozen(protocol, api_key=TEST_KEY):
    fingerprint = fingerprint_api_key(api_key)
    return prepare_request(identity=IDENTITY, request_id="request-1", round_number=1,
                           scope=SCOPE, settings=settings_for(protocol),
                           system="系统：只处理所列证据。", user="修复 \u03b1。",
                           key_configured=fingerprint is not None,
                           credential_fingerprint=fingerprint)


class FakeResponse:
    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return self.stream.read(size)


CHAT_REPLY = b'{"choices":[{"message":{"content":"answer"},"finish_reason":"stop"}]}'
RESPONSES_REPLY = (b'{"status":"completed","output":[{"type":"web_search_call"},'
                   b'{"type":"message","content":[{"type":"output_text","text":"answer"}]}]}')


def child_capture():
    sample = json.load(sys.stdin)
    request = FrozenRequest.from_record(sample["request"])
    captured = []
    claimed = []

    def fake_urlopen(req, timeout):
        captured.append({"body_b64": base64.b64encode(req.data).decode("ascii"),
                         "sha256": hashlib.sha256(req.data).hexdigest(), "url": req.full_url,
                         "authorization": req.get_header("Authorization"),
                         "content_type": req.get_header("Content-type"),
                         "method": req.get_method(), "timeout": timeout})
        return FakeResponse(CHAT_REPLY if request.protocol == "chat" else RESPONSES_REPLY)

    result = send_request(
        request, authorization_id=request.authorization_id, compile_fix_terms_ack=True,
        identity=request.identity, scope=request.scope, settings=sample["settings"],
        api_key=TEST_KEY, consume=lambda rid, sha: claimed.append([rid, sha]) or True,
        response_limit=REPLY_LIMIT, _test_urlopen=fake_urlopen)
    print(json.dumps({"captured": captured, "claimed": claimed, "status": result["status"],
                      "source_text": result["source_text"],
                      "source_b64": base64.b64encode(result["source_bytes"]).decode("ascii")}))


class L2RequestTests(unittest.TestCase):
    def test_roundtrip_preserves_frozen_body_bytes(self):
        request = frozen("chat")
        restored = FrozenRequest.from_record(json.loads(json.dumps(request.to_record())))
        self.assertEqual(restored.body, request.body)
        self.assertEqual(restored.body_sha256, hashlib.sha256(restored.body).hexdigest())

    def test_two_protocols_send_exact_frozen_bytes_in_subprocess(self):
        expected = {
            "chat": ('{"model": "model-test", "messages": [{"role": "system", '
                     '"content": "系统：只处理所列证据。"}, {"role": "user", "content": '
                     '"修复 α。"}], "temperature": 0, "max_tokens": 2048, '
                     '"thinking": {"type": "disabled"}}'),
            "responses": ('{"model": "model-test", "instructions": "系统：只处理所列证据。", '
                          '"input": "修复 α。", "temperature": 0, "max_output_tokens": 2048, '
                          '"reasoning": {"effort": "none"}, "tools": [{"type": "web_search_preview"}]}'),
        }
        for protocol in ("chat", "responses"):
            with self.subTest(protocol=protocol):
                request = frozen(protocol)
                sample = {"request": request.to_record(), "settings": settings_for(protocol)}
                proc = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--capture"],
                    input=json.dumps(sample), encoding="utf-8", capture_output=True,
                    check=True, timeout=30)
                result = json.loads(proc.stdout)
                self.assertEqual(len(result["captured"]), 1)
                self.assertEqual(result["claimed"], [[request.request_id, request.body_sha256]])
                captured = result["captured"][0]
                sent = base64.b64decode(captured["body_b64"])
                self.assertEqual(sent, expected[protocol].encode("utf-8"))
                self.assertEqual(sent, request.body)
                self.assertEqual(sent, sample["request"]["body"].encode("utf-8"))
                self.assertEqual(captured["sha256"], request.body_sha256)
                self.assertEqual(hashlib.sha256(sent).hexdigest(), request.body_sha256)
                self.assertEqual(captured["url"], request.target)
                self.assertEqual(captured["authorization"], f"Bearer {TEST_KEY}")
                self.assertEqual(captured["content_type"], "application/json")
                self.assertEqual(captured["method"], "POST")
                self.assertEqual(captured["timeout"], DEFAULT_TIMEOUT)
                self.assertNotIn(TEST_KEY.encode(), sent)
                self.assertNotIn(TEST_KEY, json.dumps(sample))
                self.assertEqual(result["status"], "reply")
                self.assertEqual(result["source_text"], "answer")
                self.assertEqual(base64.b64decode(result["source_b64"]),
                                 CHAT_REPLY if protocol == "chat" else RESPONSES_REPLY)

    def test_missing_ack_scope_change_and_missing_response_limit_fail_closed(self):
        request = frozen("chat")
        events = []
        settings = settings_for("chat")
        kwargs = dict(identity=IDENTITY, scope=SCOPE, settings=settings,
                      authorization_id=request.authorization_id, compile_fix_terms_ack=True,
                      api_key=TEST_KEY, consume=lambda *_: events.append("consume") or True,
                      response_limit=REPLY_LIMIT, _test_urlopen=lambda *_a, **_kw: events.append("open"))
        for change in ({"compile_fix_terms_ack": False}, {"compile_fix_terms_ack": "true"},
                       {"authorization_id": ""}, {"scope": {"evidence_ids": ["e1"]}},
                       {"identity": dict(IDENTITY, candidate_sha256="b" * 64)},
                       {"settings": dict(settings, base="https://changed.example.invalid")},
                       {"settings": dict(settings, compile_fix_enabled=False)},
                       {"settings": dict(settings, ai_enabled=False)},
                       {"settings": dict(settings, max_tokens=1024)},
                       {"api_key": ""}, {"api_key": "stale-fake-key"},
                       {"response_limit": None}, {"response_limit": REPLY_LIMIT + 1}):
            with self.subTest(change=change), patch.dict(os.environ, SLCN_LLM_KEY="stale-fake-key"):
                with self.assertRaises(RequestError):
                    send_request(request, **(kwargs | change))
        self.assertEqual(events, [])

    def test_record_rejects_body_and_summary_drift(self):
        request = frozen("chat")
        for field, value in (("body", "{}"), ("target", "https://changed.example.invalid"),
                             ("summary", {}), ("round_number", 2),
                             ("credential_fingerprint", fingerprint_api_key("other-test-key"))):
            with self.subTest(field=field), self.assertRaises(RequestError):
                FrozenRequest.from_record(request.to_record() | {field: value})

    def test_credential_presence_and_change_are_bound_without_storing_key(self):
        request = frozen("chat")
        changed = frozen("chat", "different-test-key")
        missing = frozen("chat", None)
        self.assertIs(request.summary["key_configured"], True)
        self.assertIs(missing.summary["key_configured"], False)
        self.assertIsNone(missing.to_record()["credential_fingerprint"])
        for prepared in (changed, missing):
            self.assertEqual(request.body, prepared.body)
            self.assertNotEqual(request.authorization_id, prepared.authorization_id)
        self.assertNotIn(TEST_KEY, json.dumps(request.to_record()))
        self.assertNotIn(request.credential_fingerprint, json.dumps(request.summary))
        with patch.dict(os.environ, SLCN_LLM_KEY=TEST_KEY):
            restored = FrozenRequest.from_record(missing.to_record())
            with self.assertRaises(RequestError):
                send_request(restored, authorization_id=restored.authorization_id,
                             compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                             settings=settings_for("chat"), api_key=TEST_KEY, response_limit=REPLY_LIMIT,
                             consume=lambda *_: self.fail("unconfigured request consumed"),
                             _test_urlopen=lambda *_a, **_kw: self.fail("unconfigured request sent"))

    def test_consume_callback_denial_blocks_second_open(self):
        # This exercises the transport callback contract, not R3 disk persistence.
        request = frozen("chat")
        calls = []
        consumed = set()

        def consume(rid, digest):
            key = (rid, digest)
            if key in consumed:
                return False
            consumed.add(key)
            return True

        kwargs = dict(authorization_id=request.authorization_id, compile_fix_terms_ack=True,
                      identity=IDENTITY, scope=SCOPE, settings=settings_for("chat"), api_key=TEST_KEY,
                      consume=consume, response_limit=REPLY_LIMIT,
                      _test_urlopen=lambda *_args, **_kw: calls.append(1) or FakeResponse(CHAT_REPLY))
        self.assertEqual(send_request(request, **kwargs)["status"], "reply")
        with self.assertRaises(RequestError):
            send_request(FrozenRequest.from_record(request.to_record()), **kwargs)
        self.assertEqual(calls, [1])

    def test_transport_exception_after_open_is_unknown_without_retry(self):
        request = frozen("chat")
        calls = []

        def fail_once(*_args, **_kwargs):
            calls.append(1)
            raise TimeoutError("simulated")

        result = send_request(request, authorization_id=request.authorization_id,
                              compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                              settings=settings_for("chat"), api_key=TEST_KEY,
                              consume=lambda *_: True, response_limit=REPLY_LIMIT,
                              _test_urlopen=fail_once)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(calls), 1)

    def test_raw_limit_and_truncation_return_unknown(self):
        request = frozen("chat")
        for raw in (b"x" * (REPLY_LIMIT + 1), CHAT_REPLY.replace(b'"stop"', b'"length"')):
            with self.subTest(length=len(raw)):
                result = send_request(request, authorization_id=request.authorization_id,
                                      compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                                      settings=settings_for("chat"), api_key=TEST_KEY,
                                      consume=lambda *_: True, response_limit=REPLY_LIMIT,
                                      _test_urlopen=lambda *_a, **_kw: FakeResponse(raw))
                self.assertEqual(result["status"], "unknown")
                self.assertNotIn("source_bytes", result)

    def test_short_reads_continue_to_eof_or_cap_before_parsing(self):
        request = frozen("chat")

        class ShortReader(FakeResponse):
            def __init__(self, *pieces):
                self.pieces = iter(pieces)
                self.read_sizes = []

            def read(self, size):
                self.read_sizes.append(size)
                piece = next(self.pieces, b"")
                if len(piece) > size:
                    raise AssertionError("fixture exceeds requested read size")
                return piece

        for tail, expected_status, expected_reads in (
                (b"", "reply", 2), (b"X", "unknown", 3), (b" " * 5, "unknown", 2)):
            with self.subTest(tail=tail):
                response = ShortReader(CHAT_REPLY, tail)
                result = send_request(request, authorization_id=request.authorization_id,
                                      compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                                      settings=settings_for("chat"), api_key=TEST_KEY, consume=lambda *_: True,
                                      response_limit=len(CHAT_REPLY) + 4,
                                      _test_urlopen=lambda *_a, **_kw: response)
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(len(response.read_sizes), expected_reads)
                if expected_status == "unknown":
                    self.assertNotIn("source_bytes", result)

    def test_http_response_rejects_early_eof_after_valid_json(self):
        request = frozen("chat")

        class InMemorySocket:
            def makefile(self, mode):
                header = f"HTTP/1.1 200 OK\r\nContent-Length: {len(CHAT_REPLY) + 1}\r\n\r\n"
                return io.BytesIO(header.encode("ascii") + CHAT_REPLY)

        response = HTTPResponse(InMemorySocket())
        response.begin()
        result = send_request(request, authorization_id=request.authorization_id,
                              compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                              settings=settings_for("chat"), api_key=TEST_KEY, consume=lambda *_: True,
                              response_limit=REPLY_LIMIT, _test_urlopen=lambda *_a, **_kw: response)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("长度不符", result["error"])
        self.assertNotIn("source_bytes", result)
        self.assertNotIn("source_text", result)

    def test_content_length_validation(self):
        request = frozen("chat")
        size = str(len(CHAT_REPLY))
        for values, transfer, status, reads_body in (
                ([], None, "reply", True), ([size], None, "reply", True),
                ([size, "0" + size], None, "reply", True),
                ([str(len(CHAT_REPLY) - 1)], None, "unknown", True),
                ([size, "0"], None, "unknown", False),
                ([f"{size}, {size}"], None, "unknown", False),
                (["-1"], None, "unknown", False), ([""], None, "unknown", False),
                (["0x10"], None, "unknown", False),
                ([str(REPLY_LIMIT + 1)], None, "unknown", False),
                (["9" * 5000], None, "unknown", False),
                ([size], "chunked", "unknown", False)):
            with self.subTest(values=[v[:32] for v in values], transfer=transfer):
                response = FakeResponse(CHAT_REPLY)
                response.headers = Message()
                for value in values:
                    response.headers["Content-Length"] = value
                if transfer is not None:
                    response.headers["Transfer-Encoding"] = transfer
                with patch.object(response, "read", wraps=response.read) as read:
                    result = send_request(request, authorization_id=request.authorization_id,
                                          compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                                          settings=settings_for("chat"), api_key=TEST_KEY,
                                          consume=lambda *_: True, response_limit=REPLY_LIMIT,
                                          _test_urlopen=lambda *_a, **_kw: response)
                self.assertEqual(result["status"], status)
                self.assertEqual(read.called, reads_body)
                if status == "unknown":
                    self.assertNotIn("source_bytes", result)

    def test_chat_requires_exact_stop(self):
        request = frozen("chat")
        for fields in ({}, {"finish_reason": None}, {"finish_reason": 4},
                       {"finish_reason": "length"}, {"finish_reason": "tool_calls"},
                       {"finish_reason": "STOP"}):
            with self.subTest(fields=fields):
                raw = json.dumps({"choices": [{"message": {"content": "answer"}, **fields}]}).encode()
                result = send_request(request, authorization_id=request.authorization_id,
                                      compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                                      settings=settings_for("chat"), api_key=TEST_KEY, consume=lambda *_: True,
                                      response_limit=REPLY_LIMIT,
                                      _test_urlopen=lambda *_a, **_kw: FakeResponse(raw))
                self.assertEqual(result["status"], "unknown")
                self.assertNotIn("source_bytes", result)

    def test_default_opener_does_not_forward_post_or_auth_on_redirect(self):
        request = frozen("chat")
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                requests = []

                class RedirectHTTPS(urllib.request.HTTPSHandler):
                    def https_open(self, req):
                        requests.append((req.get_method(), req.full_url, req.get_header("Authorization")))
                        headers = Message()
                        headers["Location"] = "https://redirect.example.invalid/other"
                        response = addinfourl(io.BytesIO(), headers, req.full_url, status)
                        response.msg = "Fake redirect"
                        return response

                with patch.object(urllib.request, "HTTPSHandler", RedirectHTTPS):
                    result = send_request(request, authorization_id=request.authorization_id,
                                          compile_fix_terms_ack=True, identity=IDENTITY, scope=SCOPE,
                                          settings=settings_for("chat"), api_key=TEST_KEY,
                                          consume=lambda *_: True, response_limit=REPLY_LIMIT)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(requests, [("POST", request.target, f"Bearer {TEST_KEY}")])


def _retained_row(record_key, preview_id, status):
    return {"record_key": record_key, "task_id": "task-1", "preview_id": preview_id,
            "status": status, "expires_at": None, "has_reply": True}


class FletRetainedContractTests(unittest.TestCase):
    def test_retained_bridge_accepts_multiple_rows_and_only_returns_contract_fields(self):
        first = _retained_row("a" * 64, "preview-1", "delivered")
        second = _retained_row("b" * 64, "preview-2", "expired")
        payload = {"op": "compile-fix", "ok": True, "retained": [first, second]}

        result = flet_engine_bridge.compile_fix_retained_result(0, payload, task_id="task-1")
        self.assertEqual(result, [first, second])
        self.assertTrue(all(set(row) == {"record_key", "task_id", "preview_id", "status",
                                         "expires_at", "has_reply"} for row in result))

    def test_retained_bridge_rejects_unbound_duplicate_or_malformed_rows(self):
        valid = _retained_row("a" * 64, "preview-1", "delivered")
        malformed = (
            [{**valid, "task_id": "other-task"}],
            [valid, {**valid, "preview_id": "preview-2"}],
            [_retained_row("bad-key", "preview-1", "delivered")],
            [{**valid, "internal_path": "/private/request"}],
            [valid, {"record_key": "b" * 64}],
        )
        for rows in malformed:
            with self.subTest(rows=rows):
                payload = {"op": "compile-fix", "ok": True, "retained": rows}
                self.assertIsNone(flet_engine_bridge.compile_fix_retained_result(
                    0, payload, task_id="task-1"))


if __name__ == "__main__":
    if sys.argv[1:] == ["--capture"]:
        child_capture()
    else:
        unittest.main()
