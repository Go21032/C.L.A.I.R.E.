"""
tests/test_support_ai_auto_pipe_code_execution.py
----------------------------------------------------
CODEルート(devstral)に「実際にファイルを作成し実行する」機能(⑩)のユニットテスト。
Ollama呼び出し(router.call_phi4 / support_ai_auto_pipe.generate)はフェイクに差し替え、
実際のファイル書き込み・python実行だけは一時ディレクトリ(WORKSPACE_DIR)に対して行う。
"""

import sys
import tempfile
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

ACTION_TEXT = (
    "承知しました。以下のファイルを作成します。\n"
    '<ACTION path="hello.py" run="true">\n'
    "```python\n"
    "print('Hello World')\n"
    "```\n"
    "</ACTION>\n"
)


def make_body(text: str, chat_id: str = "chat-1") -> dict:
    return {
        "chat_id": chat_id,
        "messages": [{"role": "user", "content": text}],
    }


class TestCodeExecution(unittest.TestCase):
    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_generate = support_ai_auto_pipe.generate
        self._orig_workspace_dir = support_ai_auto_pipe.WORKSPACE_DIR
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        router.ensure_model_ready = lambda route: None
        # 6日目: 本番LanceDBへの書き込み事故を防ぐため、CODEルートのテストでも
        # memory_storeをフェイクに差し替える(詳細はtest_support_ai_auto_pipe.py参照)。
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

        self.tmpdir = tempfile.TemporaryDirectory()
        support_ai_auto_pipe.WORKSPACE_DIR = Path(self.tmpdir.name)

        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "CODE"}'

        router.call_phi4 = fake_call_phi4

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.generate = self._orig_generate
        support_ai_auto_pipe.WORKSPACE_DIR = self._orig_workspace_dir
        support_ai_auto_pipe.memory_store = self._orig_memory_store
        self.tmpdir.cleanup()

    def _make_pipe(self, mode: str) -> "support_ai_auto_pipe.Pipe":
        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.code_execution_mode = mode
        return pipe

    # --- confirmモード(既定) ---

    def test_confirm_mode_does_not_write_file_immediately(self):
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: ACTION_TEXT

        pipe = self._make_pipe("confirm")
        result = pipe.pipe(make_body("scriptsに新しくファイルを作ってHello Worldと出力して"))

        self.assertFalse((support_ai_auto_pipe.WORKSPACE_DIR / "hello.py").exists())
        self.assertIn("実行してよろしいですか", result)

    def test_confirm_mode_executes_after_user_confirms(self):
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: ACTION_TEXT

        pipe = self._make_pipe("confirm")
        pipe.pipe(make_body("scriptsに新しくファイルを作ってHello Worldと出力して"))

        result2 = pipe.pipe(make_body("実行して"))

        written = support_ai_auto_pipe.WORKSPACE_DIR / "hello.py"
        self.assertTrue(written.exists())
        self.assertIn("Hello World", result2)

    def test_confirm_mode_discards_pending_action_on_unrelated_followup(self):
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: ACTION_TEXT

        def fake_call_phi4(system_prompt, user_text):
            if "天気" in user_text:
                return '{"route": "FAST"}'
            return '{"route": "CODE"}'

        router.call_phi4 = fake_call_phi4

        pipe = self._make_pipe("confirm")
        pipe.pipe(make_body("scriptsに新しくファイルを作ってHello Worldと出力して"))

        # 全く関係ない話題に切り替えた場合、保留中のアクションは実行されないはず
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: f"({model})"
        pipe.pipe(make_body("今日の東京の天気を教えて"))

        self.assertFalse((support_ai_auto_pipe.WORKSPACE_DIR / "hello.py").exists())

    # --- autonomousモード ---

    def test_autonomous_mode_executes_without_confirmation(self):
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: ACTION_TEXT

        pipe = self._make_pipe("autonomous")
        result = pipe.pipe(make_body("scriptsに新しくファイルを作ってHello Worldと出力して"))

        written = support_ai_auto_pipe.WORKSPACE_DIR / "hello.py"
        self.assertTrue(written.exists())
        self.assertIn("Hello World", result)

    # --- offモード ---

    def test_off_mode_never_writes_file(self):
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: ACTION_TEXT

        pipe = self._make_pipe("off")
        pipe.pipe(make_body("scriptsに新しくファイルを作ってHello Worldと出力して"))
        pipe.pipe(make_body("実行して"))

        self.assertFalse((support_ai_auto_pipe.WORKSPACE_DIR / "hello.py").exists())

    # --- アクションブロックがない場合は今までどおり ---

    def test_no_action_block_returns_plain_reply(self):
        support_ai_auto_pipe.generate = lambda model, prompt, **kw: "(devstralからの通常の回答)"

        pipe = self._make_pipe("autonomous")
        result = pipe.pipe(make_body("このコードをレビューして"))

        self.assertIn("devstralからの通常の回答", result)
        self.assertFalse((support_ai_auto_pipe.WORKSPACE_DIR / "hello.py").exists())


if __name__ == "__main__":
    unittest.main()
