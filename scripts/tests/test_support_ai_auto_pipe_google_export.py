"""
tests/test_support_ai_auto_pipe_google_export.py
--------------------------------------------------
14日目④: 発話での「Googleドキュメントに出力して」「スプレッドシートにして」トリガーの
ユニットテスト。google_workspace.export_to_docs/export_to_sheetsはフェイクへ差し替え、
ネットワーク/ブラウザ同意には一切触れない(google_workspace自体のテストは
test_google_workspace.pyが担当)。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
OPENWEBUI_PIPE_DIR = SCRIPTS_DIR / "openwebui_pipe"
for p in (SCRIPTS_DIR, OPENWEBUI_PIPE_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import router  # noqa: E402
import support_ai_auto_pipe  # noqa: E402
from fakes import NoopMemoryStore  # noqa: E402


def make_body_with_history(user_text: str, assistant_reply: str | None, chat_id: str = "chat-1") -> dict:
    messages = []
    if assistant_reply is not None:
        messages.append({"role": "user", "content": "さっきの調査結果を教えて"})
        messages.append({"role": "assistant", "content": assistant_reply})
    messages.append({"role": "user", "content": user_text})
    return {"chat_id": chat_id, "messages": messages}


class TestGoogleExportTrigger(unittest.TestCase):
    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        self._orig_generate = support_ai_auto_pipe.generate
        router.ensure_model_ready = lambda route: None
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.memory_store = self._orig_memory_store
        support_ai_auto_pipe.generate = self._orig_generate

    def test_docs_trigger_exports_last_assistant_reply(self):
        body = make_body_with_history("Googleドキュメントに出力して", "調査結果:天気は晴れです。")
        pipe = support_ai_auto_pipe.Pipe()
        with mock.patch.object(
            support_ai_auto_pipe.google_workspace,
            "export_to_docs",
            return_value="https://docs.google.com/document/d/X/edit",
        ) as fake_export:
            reply = pipe.pipe(body)
        self.assertIn("https://docs.google.com/document/d/X/edit", reply)
        fake_export.assert_called_once()
        args, _ = fake_export.call_args
        self.assertEqual(args[1], "調査結果:天気は晴れです。")

    def test_sheets_trigger_exports_last_assistant_reply_as_rows(self):
        body = make_body_with_history("スプレッドシートにして", "項目A: 10\n項目B: 20")
        pipe = support_ai_auto_pipe.Pipe()
        with mock.patch.object(
            support_ai_auto_pipe.google_workspace,
            "export_to_sheets",
            return_value="https://docs.google.com/spreadsheets/d/X/edit",
        ) as fake_export:
            reply = pipe.pipe(body)
        self.assertIn("https://docs.google.com/spreadsheets/d/X/edit", reply)
        fake_export.assert_called_once()
        args, _ = fake_export.call_args
        self.assertEqual(args[1], [["項目A: 10"], ["項目B: 20"]])

    def test_trigger_does_not_call_router_classification(self):
        # google_export分岐はルーティング(session.get_route/router.call_phi4)を
        # 一切通らないことの確認。call_phi4が呼ばれたら失敗させる。
        def fail_call_phi4(system_prompt, user_text):
            raise AssertionError("google export trigger should not classify via call_phi4")

        router.call_phi4 = fail_call_phi4
        body = make_body_with_history("Googleドキュメントに出力して", "本文")
        pipe = support_ai_auto_pipe.Pipe()
        with mock.patch.object(support_ai_auto_pipe.google_workspace, "export_to_docs", return_value="url"):
            pipe.pipe(body)  # 例外が飛ばなければOK

    def test_not_authenticated_error_returns_friendly_message(self):
        body = make_body_with_history("Googleドキュメントに出力して", "本文")
        pipe = support_ai_auto_pipe.Pipe()
        with mock.patch.object(
            support_ai_auto_pipe.google_workspace,
            "export_to_docs",
            side_effect=support_ai_auto_pipe.google_workspace.NotAuthenticatedError(
                "Googleの認証が期限切れです。再認証してください。"
            ),
        ):
            reply = pipe.pipe(body)
        self.assertIn("再認証", reply)

    def test_no_prior_assistant_reply_returns_guidance_message(self):
        body = make_body_with_history("Googleドキュメントに出力して", None)
        pipe = support_ai_auto_pipe.Pipe()
        reply = pipe.pipe(body)
        self.assertIn("見つかりませんでした", reply)

    def test_casual_mention_does_not_trigger_export(self):
        # 「スプレッドシートって便利だよね」のような雑談は誤爆しないこと。
        body = make_body_with_history("スプレッドシートって便利だよね", "何かの応答")
        pipe = support_ai_auto_pipe.Pipe()

        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "FAST"}'

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = lambda model, prompt, **kwargs: "ふつうの応答"
        reply = pipe.pipe(body)
        self.assertNotIn("docs.google.com", reply)
        self.assertNotIn("spreadsheets", reply)


class TestGoogleExportCombinedRequest(unittest.TestCase):
    """バグ修正: 依頼本体とGoogle出力指示が同じ発話にまとまっているケース。

    「2026年7月から放送開始したアニメを一覧表で作成し、スプレッドシートで
    出力してください」のような、会話の最初の発言で依頼と出力指示が
    一度に来るケースを再現する。修正前は直前のアシスタント応答が無いため
    「出力する直前の応答が見つかりませんでした」とだけ返し、依頼そのものが
    一度も処理されなかった。
    """

    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        self._orig_generate = support_ai_auto_pipe.generate
        router.ensure_model_ready = lambda route: None
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

        def fail_call_phi4(system_prompt, user_text):
            raise AssertionError(
                "combined google export request should force DEEP without classification"
            )

        router.call_phi4 = fail_call_phi4

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.memory_store = self._orig_memory_store
        support_ai_auto_pipe.generate = self._orig_generate

    def test_generates_reply_then_exports_to_sheets_with_no_prior_history(self):
        body = make_body_with_history(
            "2026年7月から放送開始したアニメを一覧表で作成し、スプレッドシートで出力してください",
            None,
        )
        pipe = support_ai_auto_pipe.Pipe()
        generated_table = "| タイトル | 放送局 |\n|---|---|\n| 作品A | ○○テレビ |"
        support_ai_auto_pipe.generate = lambda model, prompt, **kwargs: generated_table
        with mock.patch.object(
            support_ai_auto_pipe.google_workspace,
            "export_to_sheets",
            return_value="https://docs.google.com/spreadsheets/d/X/edit",
        ) as fake_export:
            reply = pipe.pipe(body)
        self.assertIn("https://docs.google.com/spreadsheets/d/X/edit", reply)
        self.assertIn(generated_table, reply)  # 生成した内容もユーザーへ見える
        fake_export.assert_called_once()
        args, _ = fake_export.call_args
        # Markdownパイプ表が列ごとに分割され、区切り行(|---|---|)は含まれないこと
        self.assertEqual(args[1], [["タイトル", "放送局"], ["作品A", "○○テレビ"]])

    def test_combined_request_with_image_still_exports(self):
        # 「テンプした画像をデジタル化して表にまとめてそれをスプレッドシートで出力してください」
        # のように画像添付+依頼+出力指示が1メッセージのケース。
        messages = [
            {
                "role": "user",
                "content": "テンプした画像をデジタル化して表にまとめてそれをスプレッドシートで出力してください",
                "images": ["data:image/png;base64,fake"],
            }
        ]
        body = {"chat_id": "chat-2", "messages": messages}
        pipe = support_ai_auto_pipe.Pipe()
        generated_table = "| 日付 | 内容 |\n|---|---|\n| 8/1 | 腕立て30回 |"
        support_ai_auto_pipe.generate = lambda model, prompt, **kwargs: generated_table
        with mock.patch.object(
            support_ai_auto_pipe.google_workspace,
            "export_to_sheets",
            return_value="https://docs.google.com/spreadsheets/d/Y/edit",
        ) as fake_export:
            reply = pipe.pipe(body)
        self.assertIn("https://docs.google.com/spreadsheets/d/Y/edit", reply)
        fake_export.assert_called_once()
        args, _ = fake_export.call_args
        self.assertEqual(args[1], [["日付", "内容"], ["8/1", "腕立て30回"]])

    def test_short_reference_only_remainder_still_uses_last_reply(self):
        # 「それをスプレッドシートにして」は依頼本体を含まない短い参照なので、
        # 従来どおり直前のアシスタント応答をそのまま出力する(生成をしない)。
        body = make_body_with_history("それをスプレッドシートにして", "項目A: 10\n項目B: 20")
        pipe = support_ai_auto_pipe.Pipe()

        def fail_generate(model, prompt, **kwargs):
            raise AssertionError("reference-only export request should not call generate()")

        support_ai_auto_pipe.generate = fail_generate
        with mock.patch.object(
            support_ai_auto_pipe.google_workspace,
            "export_to_sheets",
            return_value="https://docs.google.com/spreadsheets/d/Z/edit",
        ) as fake_export:
            reply = pipe.pipe(body)
        self.assertIn("https://docs.google.com/spreadsheets/d/Z/edit", reply)
        args, _ = fake_export.call_args
        self.assertEqual(args[1], [["項目A: 10"], ["項目B: 20"]])


if __name__ == "__main__":
    unittest.main()
