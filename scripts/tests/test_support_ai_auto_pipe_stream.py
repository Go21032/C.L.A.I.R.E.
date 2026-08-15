"""
tests/test_support_ai_auto_pipe_stream.py
-------------------------------------------
9日目ノート④後半:`support_ai_auto_pipe.Pipe.pipe()`に`Iterator[str]`を返す経路を
追加したことに対するユニットテスト。

検証したい設計上の約束事:
  1. FAST/DEEPだけがストリーミング対象。CODE(ACTIONブロックの解析に全文が必要)と
     タスク呼び出し(6日目⑧-2)とCLARIFYは従来どおり`str`を返す。
  2. ストリーミングするかどうかは `body["stream"]` に従う(Open WebUIの流儀)。
     Valve `streaming_mode` で "always" / "off" に固定もできる。
  3. **記憶DBへの書き戻し(`_remember`)がストリーミング経路でも必ず行われる**。
     ここを落とすと「音声で話した内容が記憶に残らない」という実害が出る(④の厳守事項)。
  4. 途中でOllamaが落ちても例外を投げずにエラー文をyieldする
     (例外を投げるとUI側が無反応になるため)。

既存テストと同じく、Ollama呼び出し・記憶レイヤーはすべてフェイクに差し替える。
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
from ollama_client import OllamaError  # noqa: E402


def make_body(
    text: str,
    chat_id: str = "chat-1",
    stream: bool | None = True,
    images: list[str] | None = None,
) -> dict:
    message: dict = {"role": "user", "content": text}
    if images:
        message["images"] = images
    body: dict = {"chat_id": chat_id, "messages": [message]}
    if stream is not None:
        body["stream"] = stream
    return body


class StreamPipeTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_generate = support_ai_auto_pipe.generate
        self._orig_generate_stream = getattr(support_ai_auto_pipe, "generate_stream", None)
        self._orig_memory_store = support_ai_auto_pipe.memory_store

        router.ensure_model_ready = lambda route: None
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.generate = self._orig_generate
        if self._orig_generate_stream is not None:
            support_ai_auto_pipe.generate_stream = self._orig_generate_stream
        support_ai_auto_pipe.memory_store = self._orig_memory_store

    def set_route(self, route: str):
        router.call_phi4 = lambda system_prompt, user_text: '{"route": "%s"}' % route

    def set_stream_tokens(self, tokens, recorder: list | None = None):
        def fake_generate_stream(model, prompt, **kwargs):
            if recorder is not None:
                recorder.append({"model": model, "prompt": prompt, **kwargs})
            return iter(tokens)

        support_ai_auto_pipe.generate_stream = fake_generate_stream

    def set_generate(self, text="(全文応答)", recorder: list | None = None):
        def fake_generate(model, prompt, **kwargs):
            if recorder is not None:
                recorder.append({"model": model, "prompt": prompt, **kwargs})
            return text

        support_ai_auto_pipe.generate = fake_generate


class TestStreamingRoutes(StreamPipeTestCase):
    def test_fast_route_returns_iterator_and_yields_tokens_in_order(self):
        self.set_route("FAST")
        self.set_stream_tokens(["こん", "にちは", "。"])
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("今日の天気を教えて"))

        self.assertFalse(isinstance(result, str), "ストリーミング時はstrではなくIteratorを返すこと")
        chunks = list(result)
        self.assertEqual("".join(chunks[1:]), "こんにちは。")

    def test_debug_prefix_is_yielded_before_any_token(self):
        self.set_route("DEEP")
        self.set_stream_tokens(["本文"])
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        chunks = list(pipe.pipe(make_body("来月の旅行の計画を立てて")))

        self.assertEqual(chunks[0], "[route: DEEP]\n")
        self.assertEqual("".join(chunks), "[route: DEEP]\n本文")

    def test_debug_prefix_off_yields_only_tokens(self):
        self.set_route("FAST")
        self.set_stream_tokens(["あ", "い"])
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.show_route_debug_prefix = False
        chunks = list(pipe.pipe(make_body("今日の天気を教えて")))

        self.assertEqual(chunks, ["あ", "い"])

    def test_target_model_and_memory_context_are_passed_to_generate_stream(self):
        recorder: list = []
        self.set_route("DEEP")
        self.set_stream_tokens(["ok"], recorder)
        self.set_generate()
        support_ai_auto_pipe.memory_store = RecordingMemoryStore(
            hits=[{"date": "2026-08-01", "content": "休みは水曜日"}]
        )

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("来月の旅行の計画を立てて")))

        self.assertEqual(len(recorder), 1)
        self.assertEqual(recorder[0]["model"], "gemma4:26b")
        self.assertIn("休みは水曜日", recorder[0]["system"])


class TestNonStreamingRoutes(StreamPipeTestCase):
    def test_code_route_never_streams_even_when_requested(self):
        self.set_route("CODE")
        self.set_stream_tokens(["絶対に使われてはいけない"])
        self.set_generate("devstralの全文応答")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("このスクリプトのバグを直して実装しといて"))

        self.assertIsInstance(result, str)
        self.assertIn("devstralの全文応答", result)

    def test_clarify_route_never_streams(self):
        self.set_route("CLARIFY")
        self.set_stream_tokens(["絶対に使われてはいけない"])
        self.set_generate("何について知りたいですか?")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("あれ、どうすればいい?"))

        self.assertIsInstance(result, str)
        self.assertIn("何について知りたいですか?", result)

    def test_task_call_never_streams(self):
        self.set_route("FAST")
        self.set_stream_tokens(["絶対に使われてはいけない"])
        self.set_generate('{"title": "Cat Name Discussion"}')

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(
            make_body("### Task:\nGenerate a concise title."),
            __metadata__={"chat_id": "chat-1", "task": "title_generation"},
        )

        self.assertIsInstance(result, str)
        self.assertEqual(result, '{"title": "Cat Name Discussion"}')

    def test_body_without_stream_flag_keeps_returning_str(self):
        """後方互換: `stream`を指定しない既存の呼び出しは従来どおりstrを返す。"""
        self.set_route("FAST")
        self.set_stream_tokens(["使われてはいけない"])
        self.set_generate("従来どおりの全文")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("今日の天気を教えて", stream=None))

        self.assertIsInstance(result, str)
        self.assertIn("従来どおりの全文", result)

    def test_streaming_mode_off_valve_disables_streaming(self):
        self.set_route("FAST")
        self.set_stream_tokens(["使われてはいけない"])
        self.set_generate("全文")

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.streaming_mode = "off"
        result = pipe.pipe(make_body("今日の天気を教えて", stream=True))

        self.assertIsInstance(result, str)

    def test_streaming_mode_always_valve_streams_without_body_flag(self):
        """Open WebUIが`body["stream"]`を渡さない版だった場合の逃げ道。"""
        self.set_route("FAST")
        self.set_stream_tokens(["あ", "い"])
        self.set_generate("使われてはいけない")

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.streaming_mode = "always"
        result = pipe.pipe(make_body("今日の天気を教えて", stream=None))

        self.assertFalse(isinstance(result, str))
        self.assertEqual("".join(list(result)[1:]), "あい")


class TestStreamingMemoryWriteBack(StreamPipeTestCase):
    def test_remember_is_called_once_with_joined_text_after_stream_ends(self):
        store = RecordingMemoryStore()
        support_ai_auto_pipe.memory_store = store
        self.set_route("FAST")
        self.set_stream_tokens(["私は", "C.L.A.I.R.E.", "です。"])
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        stream = pipe.pipe(make_body("あなたは誰?", chat_id="chat-voice-1"))

        # まだ読み切っていない時点では書き戻されていないこと
        self.assertEqual(store.append_calls, [])

        list(stream)

        self.assertEqual(len(store.append_calls), 2)
        self.assertEqual(store.append_calls[0]["role"], "user")
        self.assertEqual(store.append_calls[0]["text"], "あなたは誰?")
        self.assertEqual(store.append_calls[1]["role"], "assistant")
        self.assertEqual(store.append_calls[1]["text"], "私はC.L.A.I.R.E.です。")
        self.assertEqual(store.append_calls[1]["chat_id"], "chat-voice-1")

    def test_remember_does_not_include_debug_prefix(self):
        store = RecordingMemoryStore()
        support_ai_auto_pipe.memory_store = store
        self.set_route("FAST")
        self.set_stream_tokens(["本文だけ"])
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("今日の天気を教えて")))

        self.assertEqual(store.append_calls[1]["text"], "本文だけ")

    def test_remember_still_runs_when_consumer_stops_early(self):
        """音声UIで途中中断した場合でも、そこまでの応答を記憶に残す。"""
        store = RecordingMemoryStore()
        support_ai_auto_pipe.memory_store = store
        self.set_route("FAST")
        self.set_stream_tokens(["前半", "後半"])
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        stream = pipe.pipe(make_body("今日の天気を教えて"))
        next(stream)  # debug prefix
        next(stream)  # "前半"
        stream.close()

        self.assertEqual(len(store.append_calls), 2)
        self.assertEqual(store.append_calls[1]["text"], "前半")


class TestImageForcedRouting(StreamPipeTestCase):
    """11日目④-1: 画像添付時はルーターを経由せず強制的にDEEPへルーティングされること。"""

    def test_image_attachment_forces_deep_without_calling_router_model(self):
        phi4_calls: list = []

        def fake_call_phi4(system_prompt, user_text):
            phi4_calls.append(user_text)
            return '{"route": "FAST"}'  # 呼ばれてしまった場合に誤りが分かるようFASTを返す

        router.call_phi4 = fake_call_phi4
        recorder: list = []
        self.set_stream_tokens(["青い箱", "が写っています"], recorder)
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        chunks = list(pipe.pipe(make_body("この画像は何?", images=["QUJD"])))

        self.assertEqual(phi4_calls, [])  # ルーター(gemma4-e4b-cpu)は一切呼ばれない
        self.assertEqual(chunks[0], "[route: DEEP]\n")
        self.assertEqual(recorder[0]["model"], "gemma4:26b")

    def test_images_are_forwarded_to_generate_stream(self):
        self.set_route("DEEP")  # 分類自体は通常経路でも、images付きなら渡ること自体を確認
        recorder: list = []
        self.set_stream_tokens(["ok"], recorder)
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("この画像は何?", images=["QUJD", "RUZH"])))

        self.assertEqual(recorder[0]["images"], ["QUJD", "RUZH"])

    def test_no_images_omits_images_kwarg_value(self):
        """後方互換の確認: 画像添付が無い従来の呼び出しはimages=Noneのまま渡る
        (generate_stream側でリクエストボディにimagesキー自体が付かない)。"""
        self.set_route("FAST")
        recorder: list = []
        self.set_stream_tokens(["こんにちは"], recorder)
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("今日の天気を教えて")))

        self.assertIsNone(recorder[0]["images"])

    def test_image_attachment_adds_table_format_hint_to_system(self):
        # 12日目追記→13日目改訂: ストリーミング経路でも、画像添付ターンにはTABLE_FORMAT_SYSTEM_PROMPTが
        # systemへ足されること(手書き表などをMarkdownのパイプ表で出力させ、Obsidianノートへ
        # コピペしたときに正しく表として描画させるための指示)。
        self.set_route("DEEP")
        recorder: list = []
        self.set_stream_tokens(["ok"], recorder)
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("この表を読み取って", images=["QUJD"])))

        self.assertIn(support_ai_auto_pipe.TABLE_FORMAT_SYSTEM_PROMPT, recorder[0]["system"])

    def test_no_images_does_not_add_table_format_hint_to_system(self):
        self.set_route("FAST")
        recorder: list = []
        self.set_stream_tokens(["こんにちは"], recorder)
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("今日の天気を教えて")))

        self.assertIsNone(recorder[0]["system"])


class TestThinkModeDisabledForTargetModels(StreamPipeTestCase):
    """12日目①-2で判明した「FAST(gpt-oss:20b)/DEEP(gemma4:26b)が実運用で異常に遅い」問題への対応。

    実機調査(12日目ノート「①-2で分かったFASTの遅延問題の解決」参照)で、
    router.pyのROUTER_MODEL呼び出し(call_phi4)は7日目に`think=False`固定済みだったが、
    FAST/DEEP/CODEの実際の応答生成モデル(generate_stream/generate)には`think`が
    一切渡されておらずOllama既定(thinkingモード有効)のままだったことが直接検証で判明した。
    実測(Ollama /api/generateへの直接A/Bテスト): gemma4:26bはthink未指定で32.38秒
    → think=Falseで1.23秒(約26倍)。gpt-oss:20bはthink未指定で約5〜6秒の初手thinking遅延
    → think="low"(gpt-oss固有の文字列指定、reasoning effort最小)で1.13秒。
    この後方互換テストは、route別に正しいthink値がgenerate_stream/generateへ渡ることを保証する。
    """

    def test_fast_route_streams_with_low_reasoning_effort(self):
        self.set_route("FAST")
        recorder: list = []
        self.set_stream_tokens(["こんにちは"], recorder)
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("今日の天気を教えて")))

        self.assertEqual(recorder[0]["think"], "low")

    def test_deep_route_streams_with_thinking_disabled(self):
        self.set_route("DEEP")
        recorder: list = []
        self.set_stream_tokens(["本文"], recorder)
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        list(pipe.pipe(make_body("来月の旅行の計画を立てて")))

        self.assertEqual(recorder[0]["think"], False)


class TestStreamingErrorHandling(StreamPipeTestCase):
    def test_ollama_error_on_open_yields_error_text_instead_of_raising(self):
        self.set_route("FAST")

        def failing_generate_stream(model, prompt, **kwargs):
            raise OllamaError("接続できませんでした")

        support_ai_auto_pipe.generate_stream = failing_generate_stream
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        text = "".join(pipe.pipe(make_body("今日の天気を教えて")))

        self.assertIn("[error]", text)
        self.assertIn("接続できませんでした", text)

    def test_ollama_error_midway_yields_error_text_and_keeps_partial(self):
        self.set_route("FAST")

        def flaky_generate_stream(model, prompt, **kwargs):
            yield "途中まで"
            raise OllamaError("ストリームが切れました")

        support_ai_auto_pipe.generate_stream = flaky_generate_stream
        self.set_generate()

        pipe = support_ai_auto_pipe.Pipe()
        text = "".join(pipe.pipe(make_body("今日の天気を教えて")))

        self.assertIn("途中まで", text)
        self.assertIn("[error]", text)

    def test_router_classification_error_prefix_is_yielded_when_streaming(self):
        def failing_call_phi4(system_prompt, user_text):
            raise OllamaError("分類に失敗")

        router.call_phi4 = failing_call_phi4
        self.set_stream_tokens(["聞き返し"])
        self.set_generate("聞き返し文")

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("これ"))

        # 分類失敗のフォールバック先はCLARIFY=非ストリーミング経路
        self.assertIsInstance(result, str)
        self.assertIn("[error]", result)
        self.assertIn("[route: CLARIFY]", result)


if __name__ == "__main__":
    unittest.main()
