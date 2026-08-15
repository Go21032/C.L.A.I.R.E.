"""
tests/test_support_ai_auto_pipe_memory.py
--------------------------------------------
6日目ノート(サポートAI作製計画/6日目RAG記憶レイヤーのPipe組み込み.md)④⑤で
support_ai_auto_pipe.pyに組み込んだ記憶レイヤー(検索retrieve・書き戻しappend)の
ユニットテスト。

実際のOllama Embedding・LanceDBは一切呼ばず、tests/fakes.pyの
RecordingMemoryStore/FailingMemoryStoreに差し替えて、以下を検証する:
  - route別のretrieve/append要否(⑤の表どおりか)
  - CODEルートでCODE_ACTION_SYSTEM_PROMPTと記憶文脈が「上書きでなく連結」されるか
  - memory_enabled=Falseで記憶レイヤーが完全に無効化されるか
  - memory_storeが読み込めない(HDD未接続を想定しNone)場合でも本体が止まらないか
  - retrieve/append_turnが例外を出しても本体が止まらないか(④の完了条件)
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
from fakes import FailingMemoryStore, RecordingMemoryStore  # noqa: E402


def make_body(text: str, chat_id: str = "chat-1", attached_document: str | None = None) -> dict:
    message: dict = {"role": "user", "content": text}
    if attached_document:
        message["attached_document"] = attached_document
    return {
        "chat_id": chat_id,
        "messages": [message],
    }


class TestSupportAiAutoPipeMemory(unittest.TestCase):
    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_generate = support_ai_auto_pipe.generate
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        router.ensure_model_ready = lambda route: None

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.generate = self._orig_generate
        support_ai_auto_pipe.memory_store = self._orig_memory_store

    @staticmethod
    def _route_phi4(route: str):
        def fake_call_phi4(system_prompt, user_text):
            return f'{{"route": "{route}"}}'

        return fake_call_phi4

    # --- route別のretrieve/append要否(⑤の表) ---

    def test_fast_route_retrieves_unfiltered_and_appends(self):
        # 6日目ノート改善策(⑧実機検証で発覚): 「私の休みは何曜日ですか?」のような
        # 個人情報の想起質問はFASTに分類されるため、FASTでもretrieveしないと
        # 記憶を参照できないまま応答してしまう。RETRIEVE_ROUTESにFASTを追加した。
        recording = RecordingMemoryStore(
            hits=[{"content": "毎週火曜日は定休日にしています。", "date": "2026-08-06", "role": "user", "route": "FAST", "_distance": 0.2}]
        )
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("FAST")

        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "FAST応答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("私の休みは何曜日ですか?"))

        self.assertEqual(len(recording.retrieve_calls), 1)
        self.assertIsNone(recording.retrieve_calls[0]["route"])  # FASTはroute絞り込みしない
        self.assertIn("毎週火曜日は定休日にしています。", captured["system"])
        self.assertEqual(len(recording.append_calls), 2)
        self.assertEqual(recording.append_calls[0]["role"], "user")
        self.assertEqual(recording.append_calls[1]["role"], "assistant")
        self.assertTrue(all(c["route"] == "FAST" for c in recording.append_calls))

    def test_deep_route_retrieves_unfiltered_and_appends(self):
        recording = RecordingMemoryStore(
            hits=[{"content": "過去の相談内容", "date": "2026-08-01", "role": "user", "route": "DEEP", "_distance": 0.2}]
        )
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("DEEP")

        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "DEEP応答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで"))

        self.assertEqual(len(recording.retrieve_calls), 1)
        self.assertIsNone(recording.retrieve_calls[0]["route"])  # DEEPはroute絞り込みしない
        self.assertIn("過去の相談内容", captured["system"])  # 検索結果がsystemに差し込まれる
        self.assertEqual(len(recording.append_calls), 2)

    def test_code_route_retrieves_filtered_and_context_is_appended_not_overwritten(self):
        recording = RecordingMemoryStore(
            hits=[{"content": "前に書いたスクリプトの内容", "date": "2026-08-01", "role": "user", "route": "CODE", "_distance": 0.2}]
        )
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("CODE")

        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "CODE応答(ACTIONブロックなし)"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("前に渡したスクリプトのバグを直して"))

        self.assertEqual(len(recording.retrieve_calls), 1)
        # 7日目⑤: CODEはCODE・NOTE・DOCUMENTに絞り込む(NOTEを除外するとノート由来の
        # 記憶が一切ヒットしなくなる設計上の衝突が実データ検証で判明したため。
        # 11日目④でDOCUMENT(PDF/Word/Excel/PowerPoint取り込み)を追加した際、
        # CODEルートでもナレッジ由来の記憶がヒットするよう同様に対象へ加えた)
        self.assertEqual(recording.retrieve_calls[0]["route"], ("CODE", "NOTE", "DOCUMENT"))
        # ⑤の注意点: CODE_ACTION_SYSTEM_PROMPTが上書きされず、記憶文脈と連結されていること
        self.assertIn(support_ai_auto_pipe.CODE_ACTION_SYSTEM_PROMPT, captured["system"])
        self.assertIn("前に書いたスクリプトの内容", captured["system"])
        self.assertEqual(len(recording.append_calls), 2)

    def test_clarify_route_never_touches_memory(self):
        recording = RecordingMemoryStore()
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("CLARIFY")
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: "聞き返し文"

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("あれ、どうすればいい?"))

        self.assertEqual(recording.retrieve_calls, [])
        self.assertEqual(recording.append_calls, [])

    # --- Valvesによる無効化 ---

    def test_memory_enabled_false_disables_retrieve_and_append(self):
        recording = RecordingMemoryStore(
            hits=[{"content": "無視されるはずの記憶", "date": "2026-08-01", "role": "user", "route": "DEEP", "_distance": 0.1}]
        )
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("DEEP")
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: "DEEP応答"

        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.memory_enabled = False
        pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで"))

        self.assertEqual(recording.retrieve_calls, [])
        self.assertEqual(recording.append_calls, [])

    # --- 障害時に本体を止めない(④の完了条件) ---

    def test_memory_store_none_does_not_crash_pipe(self):
        # 外付けHDD未接続等でmemory_storeのimport自体に失敗したケースを模す
        support_ai_auto_pipe.memory_store = None
        router.call_phi4 = self._route_phi4("DEEP")
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: "DEEP応答(記憶レイヤー無し)"

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで"))

        self.assertIn("DEEP応答(記憶レイヤー無し)", result)

    def test_retrieve_failure_falls_back_to_no_context_without_crashing(self):
        support_ai_auto_pipe.memory_store = FailingMemoryStore()
        router.call_phi4 = self._route_phi4("DEEP")

        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "DEEP応答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで"))

        self.assertIsNone(captured["system"])  # 検索失敗時はNone(文脈なし)で呼ばれる
        self.assertIn("DEEP応答", result)

    # --- 13日目「直近添付ファイルを自動優先」 ---

    def test_attached_document_uses_source_filtered_retrieve_first(self):
        # 📎で直近添付されたファイル名がある場合、まず`source = "doc:<filename>"`で
        # 絞り込んだ検索を行い、ヒットがあればそれだけをsystemの文脈として使う
        # (通常の全文脈検索は呼ばない=応答がその1ファイルの内容に確実に集中する)。
        recording = RecordingMemoryStore(
            hits=[{"content": "無関係な過去の記憶", "date": "2026-08-01", "role": "user", "route": "DEEP", "_distance": 0.1}],
            source_hits={
                "doc:workout.pdf": [
                    {"content": "1/10 ベンチプレス60kg×10", "date": "2026-08-14", "role": "document", "route": "DOCUMENT", "_distance": 0.1},
                ]
            },
        )
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("DEEP")

        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "要約しました"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("このファイルの内容を要約して", attached_document="workout.pdf"))

        # source絞り込みの検索だけが呼ばれ(ヒットしたので通常の全文脈検索は呼ばれない)
        self.assertEqual(len(recording.retrieve_calls), 1)
        self.assertEqual(recording.retrieve_calls[0]["source"], "doc:workout.pdf")
        self.assertIn("1/10 ベンチプレス60kg×10", captured["system"])
        self.assertNotIn("無関係な過去の記憶", captured["system"])

    def test_attached_document_with_no_source_hits_falls_back_to_generic_retrieve(self):
        # source絞り込みでヒットが無い(ファイル名不一致等)場合は、従来どおりの
        # 全文脈検索へフォールバックし、応答が「記憶なし」より悪化しないようにする。
        recording = RecordingMemoryStore(
            hits=[{"content": "通常検索でヒットした記憶", "date": "2026-08-01", "role": "user", "route": "DEEP", "_distance": 0.2}],
            source_hits={"doc:workout.pdf": []},
        )
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("DEEP")

        captured = {}

        def fake_generate(model, prompt, **kw):
            captured["system"] = kw.get("system")
            return "回答"

        support_ai_auto_pipe.generate = fake_generate

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("このファイルの内容を要約して", attached_document="workout.pdf"))

        self.assertEqual(len(recording.retrieve_calls), 2)
        self.assertEqual(recording.retrieve_calls[0]["source"], "doc:workout.pdf")
        self.assertIsNone(recording.retrieve_calls[1]["source"])
        self.assertIn("通常検索でヒットした記憶", captured["system"])

    def test_no_attached_document_skips_source_filtered_retrieve(self):
        # 後方互換の確認: attached_documentが無い従来どおりのターンでは、
        # source絞り込み検索は行わず、これまでと同じ全文脈検索1回だけになる。
        recording = RecordingMemoryStore(
            hits=[{"content": "通常の記憶", "date": "2026-08-01", "role": "user", "route": "DEEP", "_distance": 0.2}]
        )
        support_ai_auto_pipe.memory_store = recording
        router.call_phi4 = self._route_phi4("DEEP")
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: "回答"

        pipe = support_ai_auto_pipe.Pipe()
        pipe.pipe(make_body("来月の家族旅行のスケジュールを組んで"))

        self.assertEqual(len(recording.retrieve_calls), 1)
        self.assertIsNone(recording.retrieve_calls[0]["source"])

    def test_append_failure_does_not_crash_pipe(self):
        support_ai_auto_pipe.memory_store = FailingMemoryStore()
        router.call_phi4 = self._route_phi4("FAST")
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: "FAST応答"

        pipe = support_ai_auto_pipe.Pipe()
        result = pipe.pipe(make_body("今日の東京の天気を教えて"))

        self.assertIn("FAST応答", result)


if __name__ == "__main__":
    unittest.main()
