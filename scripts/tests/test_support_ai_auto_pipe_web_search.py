"""
tests/test_support_ai_auto_pipe_web_search.py
-------------------------------------------------
14日目: 13日目④で「web_search.py本体+単体テストまで」で区切っていたWeb検索を、
support_ai_auto_pipe.Pipeのパイプラインへ実際に結線したことのユニットテスト。

検証したい設計上の約束事:
  - クライアント(static/index.html)がtext_inputへ載せてくる"web_search": true を
    最後のuserメッセージから読み取り、ONのときだけweb_search.search()を呼ぶこと
  - 検索結果(web_search.format_for_prompt())がsystemプロンプトへ記憶文脈と
    連結される形で差し込まれること(記憶文脈を上書きしない。CODEルートの
    CODE_ACTION_SYSTEM_PROMPTも同様に上書きしない)
  - 応答の末尾に出典(タイトル+URL)一覧が追加されること。ヒット0件なら追加しないこと
  - Valve web_search_enabled=False、またはクライアントのweb_searchフラグOFF/未指定の
    ときは検索を一切呼ばないこと
  - CLARIFYルートは検索を呼ばないこと(早期returnのため)
  - 検索失敗(web_search.search()が例外)でもPipe本体が止まらないこと
  - ストリーミング経路(FAST/DEEP)でも出典がトークン列の最後に追加され、
    記憶への書き戻し(_remember)には出典を含めないこと(debug_prefixと同じ扱い)

実際のSearXNG・requestsは一切呼ばない。support_ai_auto_pipe.web_searchをフェイクに差し替える。
"""

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
OPENWEBUI_PIPE_DIR = SCRIPTS_DIR / "openwebui_pipe"
for p in (SCRIPTS_DIR, OPENWEBUI_PIPE_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import router  # noqa: E402
import support_ai_auto_pipe  # noqa: E402
from fakes import NoopMemoryStore, RecordingMemoryStore  # noqa: E402
from web_search import SearchResult  # noqa: E402


def make_body(text: str, chat_id: str = "chat-1", web_search: bool | None = None, stream=None) -> dict:
    message: dict = {"role": "user", "content": text}
    if web_search is not None:
        message["web_search"] = web_search
    body: dict = {"chat_id": chat_id, "messages": [message]}
    if stream is not None:
        body["stream"] = stream
    return body


class WebSearchPipeTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_generate = support_ai_auto_pipe.generate
        self._orig_generate_stream = getattr(support_ai_auto_pipe, "generate_stream", None)
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        self._orig_web_search = support_ai_auto_pipe.web_search

        router.ensure_model_ready = lambda route: None
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.generate = self._orig_generate
        if self._orig_generate_stream is not None:
            support_ai_auto_pipe.generate_stream = self._orig_generate_stream
        support_ai_auto_pipe.memory_store = self._orig_memory_store
        support_ai_auto_pipe.web_search = self._orig_web_search

    def set_route(self, route: str):
        router.call_phi4 = lambda system_prompt, user_text: '{"route": "%s"}' % route

    def set_generate(self, text="(全文応答)", recorder=None):
        def fake_generate(model, prompt, **kwargs):
            if recorder is not None:
                recorder.append({"model": model, "prompt": prompt, **kwargs})
            return text

        support_ai_auto_pipe.generate = fake_generate

    def set_fake_web_search(self, results, search_calls=None, raise_error=None):
        class FakeWebSearch:
            @staticmethod
            def search(query, limit=5, **kw):
                if search_calls is not None:
                    search_calls.append({"query": query, "limit": limit})
                if raise_error is not None:
                    raise raise_error
                return results

            @staticmethod
            def format_for_prompt(rs):
                if not rs:
                    return ""
                return "WEB_CONTEXT:" + ",".join(r.title for r in rs)

        support_ai_auto_pipe.web_search = FakeWebSearch()


class TestWebSearchNonStreaming(WebSearchPipeTestCase):
    def test_web_search_key_absent_defaults_to_false(self):
        """後方互換: web_searchキー自体が無い(既存呼び出し元)は検索しない。"""
        search_calls = []
        self.set_fake_web_search([], search_calls=search_calls)
        self.set_route("FAST")
        self.set_generate("回答")

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("今日の東京の天気は?"))

        self.assertEqual(search_calls, [])

    def test_web_search_off_does_not_call_search(self):
        search_calls = []
        self.set_fake_web_search([], search_calls=search_calls)
        self.set_route("FAST")
        self.set_generate("回答")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("今日の東京の天気は?", web_search=False))

        self.assertEqual(search_calls, [])
        self.assertNotIn("出典", result)

    def test_web_search_on_calls_search_with_user_text(self):
        search_calls = []
        self.set_fake_web_search(
            [SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ")],
            search_calls=search_calls,
        )
        self.set_route("FAST")
        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "回答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("今日の東京の天気は?", web_search=True))

        self.assertEqual(len(search_calls), 1)
        self.assertEqual(search_calls[0]["query"], "今日の東京の天気は?")
        self.assertIn("WEB_CONTEXT:天気予報", captured["system"])

    def test_citations_are_appended_to_reply(self):
        self.set_fake_web_search(
            [SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ")]
        )
        self.set_route("FAST")
        self.set_generate("今日は晴れです")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("今日の東京の天気は?", web_search=True))

        self.assertIn("今日は晴れです", result)
        self.assertIn("天気予報", result)
        self.assertIn("https://example.com/a", result)

    def test_no_results_adds_no_citation_footer(self):
        self.set_fake_web_search([])
        self.set_route("FAST")
        self.set_generate("回答")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("今日の東京の天気は?", web_search=True))

        self.assertEqual(result, "[route: FAST]\n回答")

    def test_web_search_combines_with_memory_context(self):
        support_ai_auto_pipe.memory_store = RecordingMemoryStore(
            hits=[
                {
                    "content": "過去の会話",
                    "date": "2026-08-01",
                    "role": "user",
                    "route": "FAST",
                    "_distance": 0.1,
                }
            ]
        )
        self.set_fake_web_search(
            [SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ")]
        )
        self.set_route("FAST")
        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "回答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("今日の東京の天気は?", web_search=True))

        self.assertIn("過去の会話", captured["system"])
        self.assertIn("WEB_CONTEXT:天気予報", captured["system"])

    def test_web_search_valve_disabled_skips_search_even_if_flag_true(self):
        search_calls = []
        self.set_fake_web_search([], search_calls=search_calls)
        self.set_route("FAST")
        self.set_generate("回答")

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.web_search_enabled = False
        result = pipe.pipe(make_body("今日の東京の天気は?", web_search=True))

        self.assertEqual(search_calls, [])
        self.assertNotIn("出典", result)

    def test_web_search_failure_does_not_crash_pipe(self):
        self.set_fake_web_search([], raise_error=RuntimeError("searxng down"))
        self.set_route("FAST")
        self.set_generate("回答")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("今日の東京の天気は?", web_search=True))

        self.assertIn("回答", result)

    def test_code_route_combines_action_prompt_with_web_context(self):
        self.set_fake_web_search(
            [SearchResult(title="最新のPythonバージョン", url="https://example.com/py", snippet="3.13")]
        )
        self.set_route("CODE")
        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "コード回答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("最新のPythonのバージョンで動くコードを書いて", web_search=True))

        self.assertIn(support_ai_auto_pipe.CODE_ACTION_SYSTEM_PROMPT, captured["system"])
        self.assertIn("WEB_CONTEXT:最新のPythonバージョン", captured["system"])

    def test_clarify_route_never_calls_search(self):
        search_calls = []
        self.set_fake_web_search([], search_calls=search_calls)
        self.set_route("CLARIFY")
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: "聞き返し文"

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("あれ、どうすればいい?", web_search=True))

        self.assertEqual(search_calls, [])


class TestWebSearchStreaming(WebSearchPipeTestCase):
    def test_citations_are_yielded_after_tokens(self):
        self.set_fake_web_search(
            [SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ")]
        )
        self.set_route("FAST")

        def fake_generate_stream(model, prompt, **kwargs):
            return iter(["今日は", "晴れです"])

        support_ai_auto_pipe.generate_stream = fake_generate_stream
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        chunks = list(pipe.pipe(make_body("今日の東京の天気は?", web_search=True, stream=True)))

        joined = "".join(chunks)
        self.assertIn("今日は晴れです", joined)
        self.assertIn("天気予報", joined)
        self.assertIn("https://example.com/a", joined)

    def test_citations_not_included_in_remembered_reply(self):
        store = RecordingMemoryStore()
        support_ai_auto_pipe.memory_store = store
        self.set_fake_web_search(
            [SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ")]
        )
        self.set_route("FAST")

        def fake_generate_stream(model, prompt, **kwargs):
            return iter(["今日は晴れです"])

        support_ai_auto_pipe.generate_stream = fake_generate_stream
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("今日の東京の天気は?", web_search=True, stream=True)))

        self.assertEqual(store.append_calls[1]["text"], "今日は晴れです")

    def test_no_search_results_yields_no_citation_footer_in_stream(self):
        self.set_fake_web_search([])
        self.set_route("FAST")

        def fake_generate_stream(model, prompt, **kwargs):
            return iter(["こんにちは"])

        support_ai_auto_pipe.generate_stream = fake_generate_stream
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        chunks = list(pipe.pipe(make_body("今日の東京の天気は?", web_search=True, stream=True)))

        self.assertEqual("".join(chunks[1:]), "こんにちは")


if __name__ == "__main__":
    unittest.main()
