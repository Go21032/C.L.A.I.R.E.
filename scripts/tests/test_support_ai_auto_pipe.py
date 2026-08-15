"""
tests/test_support_ai_auto_pipe.py
------------------------------------
openwebui_pipe/support_ai_auto_pipe.py の`Pipe`クラスに対するユニットテスト。

Open WebUI自体はまだこのPCに導入していないため、実際のOpen WebUI環境での
動作確認はできていない(詳細はsupport_ai_auto_pipe.py冒頭のdocstring参照)。
その代わり、Ollama呼び出し(router.call_phi4 / generate / router.ensure_model_ready)を
すべてフェイク関数に差し替えることで、「Pipe自体のルーティングロジック」
(chat_idごとのセッション保持・CLARIFY分岐・エラー時のフォールバック)が
正しいことをtests/test_router.pyと同じ方式で検証する。
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
from fakes import NoopMemoryStore  # noqa: E402
from ollama_client import OllamaError  # noqa: E402


def make_body(
    text: str,
    chat_id: str = "chat-1",
    images: list[str] | None = None,
    attached_document: str | None = None,
) -> dict:
    message: dict = {"role": "user", "content": text}
    if images:
        message["images"] = images
    if attached_document:
        message["attached_document"] = attached_document
    return {
        "chat_id": chat_id,
        "messages": [message],
    }


class TestSupportAiAutoPipe(unittest.TestCase):
    def setUp(self):
        # 各テストの前に、router側の副作用があるフェイク差し替え先を退避しておく
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_generate = support_ai_auto_pipe.generate
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        # ensure_model_readyは実際にollama stopをCLI経由で呼ぶため、
        # ユニットテストでは常に無害化(何もしない)しておく。
        router.ensure_model_ready = lambda route: None
        # 6日目: memory_storeを差し替えないと、このテストスイートが実際の
        # Ollama Embedding・本番LanceDB(D:\sapo_ai\rag_memory\db)を叩いてしまう
        # (実機事故が発生済み)。記憶レイヤーそのものの挙動は
        # test_support_ai_auto_pipe_memory.py で別途検証する。
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.generate = self._orig_generate
        support_ai_auto_pipe.memory_store = self._orig_memory_store

    def test_first_turn_classifies_and_calls_target_model(self):
        calls = {"phi4": 0, "target_model": None}

        def fake_call_phi4(system_prompt, user_text):
            calls["phi4"] += 1
            return '{"route": "DEEP"}'

        def fake_generate(model, prompt, **kwargs):
            calls["target_model"] = model
            return f"({model}からの応答)"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        body = make_body("来月の家族旅行のスケジュールを組んで")
        result = pipe.pipe(body)

        self.assertEqual(calls["phi4"], 1)
        self.assertEqual(calls["target_model"], "gemma4:26b")
        self.assertIn("[route: DEEP]", result)
        self.assertIn("gemma4:26bからの応答", result)

    def test_second_turn_same_chat_keeps_route_via_context(self):
        # 2026-08-05修正: 「2ターン目以降はLLMを呼ばない」旧仕様は、実機で話題が
        # 変わった場合に誤ったrouteへ固定され続けるバグの原因だったため廃止した。
        # 現在は毎ターンLLMを呼ぶが、直前のrouteを文脈として渡すことで、
        # 単体では分類しづらい短い続きの発言でも直前のDEEPを維持できることを確認する。
        phi4_call_count = {"n": 0}

        def fake_call_phi4(system_prompt, user_text):
            phi4_call_count["n"] += 1
            return '{"route": "DEEP"}'

        def fake_generate(model, prompt, **kwargs):
            return f"({model})"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで", chat_id="chat-1"))
        self.assertEqual(phi4_call_count["n"], 1)

        result2 = pipe.pipe(make_body("土日はもう少し軽めにしてほしい", chat_id="chat-1"))
        self.assertEqual(phi4_call_count["n"], 2)  # 2ターン目もLLMを呼ぶ
        self.assertIn("[route: DEEP]", result2)

    def test_topic_change_mid_chat_is_reclassified_not_stuck(self):
        # 実機バグ再現: 同じchat_id内で全く違う話題の質問が続いた場合、
        # 最初のrouteに固定され続けず、内容に応じて正しく再分類されるべき。
        def fake_call_phi4(system_prompt, user_text):
            if "旅行" in user_text:
                return '{"route": "DEEP"}'
            if "天気" in user_text:
                return '{"route": "FAST"}'
            return '{"route": "CLARIFY"}'

        def fake_generate(model, prompt, **kwargs):
            return f"({model})"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        result1 = pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで", chat_id="chat-1"))
        self.assertIn("[route: DEEP]", result1)

        result2 = pipe.pipe(make_body("今日の東京の天気を教えて", chat_id="chat-1"))
        self.assertIn("[route: FAST]", result2)  # DEEPに固定されず正しくFASTに変わる

    def test_different_chat_ids_are_independent_sessions(self):
        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "FAST"}' if "天気" in user_text else '{"route": "DEEP"}'

        def fake_generate(model, prompt, **kwargs):
            return f"({model})"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        result_a = pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで", chat_id="chat-A"))
        result_b = pipe.pipe(make_body("今日の天気を教えて", chat_id="chat-B"))

        self.assertIn("[route: DEEP]", result_a)
        self.assertIn("[route: FAST]", result_b)

    def test_code_trigger_overrides_session_route_mid_conversation(self):
        # 4日目ノート「⑤」で見つかったケース: 会話中に自然な口語でCODEトリガーが
        # 出てきたら、セッションが保持しているDEEPより優先してCODEへ上書きされるはず。
        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "DEEP"}'

        def fake_generate(model, prompt, **kwargs):
            return f"({model})"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("3ヶ月後の資格試験に向けて学習計画を立てて", chat_id="chat-1"))
        result = pipe.pipe(
            make_body("ついでにこの前渡したスクリプトのバグも直して実装しといて", chat_id="chat-1")
        )
        self.assertIn("[route: CODE]", result)
        self.assertIn("devstral-small-2:24b", result)

    def test_clarify_route_generates_followup_question_not_target_model(self):
        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "CLARIFY"}'

        clarify_calls = []

        def fake_generate(model, prompt, **kwargs):
            clarify_calls.append((model, prompt))
            return "何について知りたいですか?"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("あれ、どうすればいい?"))

        self.assertIn("[route: CLARIFY]", result)
        self.assertEqual(len(clarify_calls), 1)
        self.assertEqual(clarify_calls[0][0], router.ROUTER_MODEL)  # phi4-mini-cpu側で聞き返す

    def test_llm_error_falls_back_to_clarify(self):
        def failing_call_phi4(system_prompt, user_text):
            raise OllamaError("接続できませんでした")

        def fake_generate(model, prompt, **kwargs):
            return "聞き返し文"

        router.call_phi4 = failing_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("これ"))

        self.assertIn("[error]", result)
        self.assertIn("[route: CLARIFY]", result)

    def test_empty_message_returns_guard_message_without_calling_llm(self):
        called = {"n": 0}

        def fake_call_phi4(system_prompt, user_text):
            called["n"] += 1
            return '{"route": "FAST"}'

        router.call_phi4 = fake_call_phi4

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("   "))

        self.assertEqual(called["n"], 0)
        self.assertIn("空", result)

    def test_debug_prefix_can_be_disabled_via_valves(self):
        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "FAST"}'

        def fake_generate(model, prompt, **kwargs):
            return "回答本文"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.show_route_debug_prefix = False
        result = pipe.pipe(make_body("今日の東京の天気を教えて"))

        self.assertNotIn("[route:", result)
        self.assertEqual(result, "回答本文")

    def test_image_attachment_forces_deep_and_forwards_images_non_streaming(self):
        # 11日目④-1: 非ストリーミング経路(streaming_modeが"off"の場合等)でも、
        # 画像添付があればルーターを経由せず強制DEEP・generate()へimagesが渡ること。
        phi4_calls: list = []

        def fake_call_phi4(system_prompt, user_text):
            phi4_calls.append(user_text)
            return '{"route": "FAST"}'

        recorder: list = []

        def fake_generate(model, prompt, **kwargs):
            recorder.append({"model": model, **kwargs})
            return "青い箱と赤い楕円が写っています"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.streaming_mode = "off"
        result = pipe.pipe(make_body("この画像は何?", images=["QUJD"]))

        self.assertEqual(phi4_calls, [])
        self.assertIn("[route: DEEP]", result)
        self.assertEqual(recorder[0]["model"], "gemma4:26b")
        self.assertEqual(recorder[0]["images"], ["QUJD"])

    def test_image_attachment_adds_table_format_hint_non_streaming(self):
        # 12日目追記→13日目改訂: 画像添付ターンでは、手書き表などをMarkdownのパイプ表
        # (Obsidianノートへそのままコピペしたときに罫線付きの表として描画される形式)で
        # 出力するよう促すsystemプロンプトが足される。
        router.call_phi4 = lambda system_prompt, user_text: '{"route": "FAST"}'

        recorder: list = []

        def fake_generate(model, prompt, **kwargs):
            recorder.append(kwargs)
            return "回答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.streaming_mode = "off"
        pipe.pipe(make_body("この表を読み取って", images=["QUJD"]))

        self.assertIn(support_ai_auto_pipe.TABLE_FORMAT_SYSTEM_PROMPT, recorder[0]["system"])

    def test_no_image_does_not_add_table_format_hint_non_streaming(self):
        # 画像が無い通常ターンにまでTABLE_FORMAT_SYSTEM_PROMPTを付けると、
        # ①-3で短縮した応答時間に無駄なプロンプト処理コストが乗ってしまうため付けない。
        router.call_phi4 = lambda system_prompt, user_text: '{"route": "FAST"}'

        recorder: list = []

        def fake_generate(model, prompt, **kwargs):
            recorder.append(kwargs)
            return "回答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.streaming_mode = "off"
        pipe.pipe(make_body("こんにちは"))

        self.assertIsNone(recorder[0]["system"])

    def test_deep_route_non_streaming_call_disables_thinking(self):
        # 12日目①-2で判明した実運用遅延の根本原因対応。非ストリーミング経路
        # (streaming_mode="off"等)でも、DEEP(gemma4:26b)呼び出しに`think=False`が
        # 渡ることを確認する(直接A/Bテストでthink未指定=32.38秒→think=Falseで1.23秒)。
        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "DEEP"}'

        recorder: list = []

        def fake_generate(model, prompt, **kwargs):
            recorder.append({"model": model, **kwargs})
            return "回答本文"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.streaming_mode = "off"
        pipe.pipe(make_body("来月の旅行の計画を立てて"))

        self.assertEqual(recorder[0]["think"], False)

    def test_code_route_call_disables_thinking(self):
        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "CODE"}'

        recorder: list = []

        def fake_generate(model, prompt, **kwargs):
            recorder.append({"model": model, **kwargs})
            return "回答本文"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("このスクリプトのバグを直して実装しといて"))

        self.assertEqual(recorder[0]["think"], False)

    def test_extract_last_user_attached_document_reads_convention_key(self):
        # 13日目「直近添付ファイルを自動優先」対応: static/index.htmlが次のtext_input送信に
        # 乗せてくる独自convention("attached_document"キー)を読み取れること。
        body = make_body("このファイルの内容を要約して", attached_document="workout.pdf")
        self.assertEqual(
            support_ai_auto_pipe.Pipe._extract_last_user_attached_document(body), "workout.pdf"
        )

    def test_extract_last_user_attached_document_returns_none_when_absent(self):
        body = make_body("こんにちは")
        self.assertIsNone(support_ai_auto_pipe.Pipe._extract_last_user_attached_document(body))

    def test_task_call_bypasses_routing_and_does_not_poison_session(self):
        # 6日目⑧-2「マツコ問題」の根本原因の再発防止テスト。
        # Open WebUIのタスクモデルが「現在のモデル」だと、タイトル生成・タグ生成・
        # フォローアップ生成が本物の会話と同じchat_idでこのPipeに飛んでくる
        # (__metadata__["task"]で識別できる)。これがrouter.classify_route経由で
        # RouterSessionのlast_routeを汚染し(JSON形式が違うため必ずCLARIFYに
        # フォールバックする)、直後の本物の発言までCLARIFYに引きずられるバグが
        # 実機で発生した。タスク呼び出しは分類・セッションを一切通さないことを検証する。
        phi4_calls = {"n": 0}

        def fake_call_phi4(system_prompt, user_text):
            phi4_calls["n"] += 1
            return '{"route": "FAST"}'

        def fake_generate(model, prompt, **kwargs):
            if "Generate a concise title" in prompt:
                return '{"title": "Cat Name Discussion"}'
            return f"({model}からの応答)"

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        chat_id = "chat-task-1"

        # Open WebUIのタイトル生成タスク相当の呼び出し(本物の会話と同じchat_id)
        task_body = {
            "chat_id": chat_id,
            "messages": [
                {
                    "role": "user",
                    "content": "### Task:\nGenerate a concise title summarizing the chat history.",
                }
            ],
        }
        task_result = pipe.pipe(
            task_body, __metadata__={"chat_id": chat_id, "task": "title_generation"}
        )

        # 分類ロジック(router.call_phi4)・RouterSessionには一切触れていないこと
        self.assertEqual(phi4_calls["n"], 0)
        self.assertNotIn(chat_id, pipe._sessions)
        self.assertEqual(task_result, '{"title": "Cat Name Discussion"}')

        # 直後に本物の発言が同じchat_idで来ても、汚染されず正しく分類される
        real_result = pipe.pipe(make_body("私の猫の名前はマツコです", chat_id=chat_id))
        self.assertEqual(phi4_calls["n"], 1)
        self.assertIn("[route: FAST]", real_result)


if __name__ == "__main__":
    unittest.main()
