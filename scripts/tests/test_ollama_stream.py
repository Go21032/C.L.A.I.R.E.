"""
tests/test_ollama_stream.py
-----------------------------
9日目ノート(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)④に対応する
`ollama_client.generate_stream()` のユニットテスト。

8日目に観測した「回答テキストが出てから音声が鳴り始めるまで約1分」問題の根本原因は、
`generate()`が`"stream": False`固定で全文生成完了まで待つ構造だったこと。
本テストは「トークンが分割されて届くこと」「連結すると`generate()`と同じ全文になること」
「途中でエラーになった場合に例外が出ること」を、実際のOllamaを起動せずに検証する。

Ollama本体は起動しないため、`urllib.request.urlopen`をフェイクに差し替える
(既存テストがrouter/generateをフェイク関数に差し替えているのと同じ方針)。
"""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ollama_client  # noqa: E402
from ollama_client import OllamaError  # noqa: E402


class FakeResponse:
    """/api/generate が stream=True のとき返すNDJSONレスポンスのフェイク。

    urllibのレスポンスオブジェクトは「1行ずつイテレートできる」「close()できる」
    という2点だけを本番コードから使うため、その2点だけを再現する。
    """

    def __init__(self, lines: list[bytes], raise_at: int | None = None, error=None):
        self._lines = lines
        self._raise_at = raise_at
        self._error = error or urllib.error.URLError("simulated connection reset")
        self.closed = False

    def __iter__(self):
        for i, line in enumerate(self._lines):
            if self._raise_at is not None and i == self._raise_at:
                raise self._error
            yield line
        if self._raise_at is not None and self._raise_at >= len(self._lines):
            raise self._error

    def close(self):
        self.closed = True

    # urllib.request.urlopen の戻り値は with文でも使えるため、
    # 本番実装が with を使う実装に変わっても壊れないようにしておく。
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def ndjson(*chunks: dict) -> list[bytes]:
    return [json.dumps(c, ensure_ascii=False).encode("utf-8") + b"\n" for c in chunks]


def token_chunks(tokens: list[str]) -> list[bytes]:
    """トークン列を Ollama の stream=True 応答(NDJSON)の形に変換する。"""
    chunks = [{"model": "m", "response": t, "done": False} for t in tokens]
    chunks.append({"model": "m", "response": "", "done": True, "done_reason": "stop"})
    return ndjson(*chunks)


class TestGenerateStream(unittest.TestCase):
    def _patch_urlopen(self, response, recorder: list | None = None):
        def fake_urlopen(req, timeout=None):
            if recorder is not None:
                recorder.append(req)
            if isinstance(response, Exception):
                raise response
            return response

        return mock.patch.object(ollama_client.urllib.request, "urlopen", fake_urlopen)

    def test_yields_each_token_separately_in_order(self):
        resp = FakeResponse(token_chunks(["こん", "にちは", "。元気", "ですか?"]))
        with self._patch_urlopen(resp):
            tokens = list(ollama_client.generate_stream(model="m", prompt="p"))

        self.assertEqual(tokens, ["こん", "にちは", "。元気", "ですか?"])

    def test_joined_tokens_equal_full_text_of_generate(self):
        """連結すると generate() が返す全文と一致すること(④の完了条件)。"""
        full_text = "はい、私はC.L.A.I.R.E.です。よろしくお願いします。"
        stream_resp = FakeResponse(token_chunks(["はい、私は", "C.L.A.I.R.E.です。", "よろしくお願いします。"]))
        with self._patch_urlopen(stream_resp):
            streamed = "".join(ollama_client.generate_stream(model="m", prompt="p"))

        non_stream_resp = FakeResponse([json.dumps({"response": full_text, "done": True}).encode("utf-8")])
        non_stream_resp.read = lambda: json.dumps({"response": full_text, "done": True}).encode("utf-8")
        with self._patch_urlopen(non_stream_resp):
            at_once = ollama_client.generate(model="m", prompt="p")

        self.assertEqual(streamed, full_text)
        self.assertEqual(streamed, at_once)

    def test_stops_reading_after_done_true(self):
        lines = ndjson(
            {"response": "あ", "done": False},
            {"response": "い", "done": True},
            {"response": "この行は読まれてはいけない", "done": False},
        )
        resp = FakeResponse(lines)
        with self._patch_urlopen(resp):
            tokens = list(ollama_client.generate_stream(model="m", prompt="p"))

        self.assertEqual(tokens, ["あ", "い"])

    def test_skips_chunks_without_response_text(self):
        """空文字のresponseや、responseフィールドを持たない行はyieldしない
        (TTSへ空文字を投げても無音wavになるだけで無駄なため)。"""
        lines = ndjson(
            {"response": "", "done": False},
            {"done": False},
            {"response": "本文", "done": False},
            {"response": "", "done": True},
        )
        resp = FakeResponse(lines)
        with self._patch_urlopen(resp):
            tokens = list(ollama_client.generate_stream(model="m", prompt="p"))

        self.assertEqual(tokens, ["本文"])

    def test_ignores_blank_lines(self):
        lines = [b"\n", b"   \n"] + token_chunks(["ほ"])
        resp = FakeResponse(lines)
        with self._patch_urlopen(resp):
            tokens = list(ollama_client.generate_stream(model="m", prompt="p"))

        self.assertEqual(tokens, ["ほ"])

    def test_request_body_sets_stream_true_and_carries_generate_options(self):
        recorder: list = []
        resp = FakeResponse(token_chunks(["x"]))
        with self._patch_urlopen(resp, recorder):
            list(
                ollama_client.generate_stream(
                    model="gemma4-e4b-cpu",
                    prompt="自己紹介して",
                    system="あなたはC.L.A.I.R.E.です",
                    options={"temperature": 0},
                    think=False,
                    keep_alive=-1,
                )
            )

        self.assertEqual(len(recorder), 1)
        req = recorder[0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertIs(body["stream"], True)
        self.assertEqual(body["model"], "gemma4-e4b-cpu")
        self.assertEqual(body["prompt"], "自己紹介して")
        self.assertEqual(body["system"], "あなたはC.L.A.I.R.E.です")
        self.assertEqual(body["options"], {"temperature": 0})
        self.assertIs(body["think"], False)
        self.assertEqual(body["keep_alive"], -1)
        self.assertEqual(req.full_url, f"{ollama_client.DEFAULT_HOST}/api/generate")

    def test_optional_fields_are_omitted_when_not_given(self):
        recorder: list = []
        resp = FakeResponse(token_chunks(["x"]))
        with self._patch_urlopen(resp, recorder):
            list(ollama_client.generate_stream(model="m", prompt="p"))

        body = json.loads(recorder[0].data.decode("utf-8"))
        self.assertNotIn("system", body)
        self.assertNotIn("options", body)
        self.assertNotIn("think", body)

    def test_connection_failure_raises_ollama_error_at_call_time(self):
        """接続失敗は generate() と同じ流儀(OllamaError)で、しかも
        イテレートを待たずに呼び出した時点で送出されること。

        遅延ジェネレータのままだと、Ollama未起動でも generate_stream() の
        呼び出し自体は成功してしまい、呼び出し側のtry/exceptが素通りする。
        """
        with self._patch_urlopen(urllib.error.URLError("connection refused")):
            with self.assertRaises(OllamaError) as ctx:
                ollama_client.generate_stream(model="m", prompt="p")

        self.assertIn("m", str(ctx.exception))

    def test_connection_reset_midway_raises_ollama_error(self):
        lines = ndjson({"response": "途中", "done": False})
        resp = FakeResponse(lines, raise_at=1)  # 1行目を返した直後に切断
        with self._patch_urlopen(resp):
            stream = ollama_client.generate_stream(model="m", prompt="p")
            self.assertEqual(next(stream), "途中")
            with self.assertRaises(OllamaError):
                next(stream)

    def test_broken_json_line_raises_ollama_error(self):
        lines = [b'{"response": "ok", "done": false}\n', "{壊れたJSON\n".encode("utf-8")]
        resp = FakeResponse(lines)
        with self._patch_urlopen(resp):
            with self.assertRaises(OllamaError):
                list(ollama_client.generate_stream(model="m", prompt="p"))

    def test_error_field_in_stream_raises_ollama_error(self):
        lines = ndjson(
            {"response": "書きかけ", "done": False},
            {"error": "model requires more system memory"},
        )
        resp = FakeResponse(lines)
        with self._patch_urlopen(resp):
            with self.assertRaises(OllamaError) as ctx:
                list(ollama_client.generate_stream(model="m", prompt="p"))

        self.assertIn("more system memory", str(ctx.exception))

    def test_response_is_closed_after_normal_iteration(self):
        resp = FakeResponse(token_chunks(["a", "b"]))
        with self._patch_urlopen(resp):
            list(ollama_client.generate_stream(model="m", prompt="p"))

        self.assertTrue(resp.closed, "ストリームを読み切った後はレスポンスを閉じること")

    def test_response_is_closed_when_consumer_stops_early(self):
        """呼び出し側が途中でやめた場合(音声UIの割り込み等)もソケットを閉じること。"""
        resp = FakeResponse(token_chunks(["a", "b", "c"]))
        with self._patch_urlopen(resp):
            stream = ollama_client.generate_stream(model="m", prompt="p")
            self.assertEqual(next(stream), "a")
            stream.close()

        self.assertTrue(resp.closed)


class TestGenerateBackwardCompatibility(unittest.TestCase):
    """④の厳守事項:既存の generate() の挙動を変えないこと。"""

    def test_generate_still_requests_stream_false(self):
        recorder: list = []
        payload = json.dumps({"response": "全文", "done": True}).encode("utf-8")

        class OneShotResponse:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        def fake_urlopen(req, timeout=None):
            recorder.append(req)
            return OneShotResponse()

        with mock.patch.object(ollama_client.urllib.request, "urlopen", fake_urlopen):
            result = ollama_client.generate(model="m", prompt="p")

        body = json.loads(recorder[0].data.decode("utf-8"))
        self.assertIs(body["stream"], False)
        self.assertEqual(result, "全文")


if __name__ == "__main__":
    unittest.main()
