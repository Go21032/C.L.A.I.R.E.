"""
tests/test_support_ai_auto_pipe_image_document_generation.py
--------------------------------------------------------------
バグ修正: 画像添付+実ファイル生成(Excel/Word/PowerPoint)の組み合わせ依頼。

修正前は「画像添付があれば問答無用でDEEPへ強制ルーティングする」
(11日目④-1)が優先され、「この手書きメモを表にしてExcelに出力して」のような
依頼でもCODEルートの③資料生成機能(router_rules.CODE_TRIGGERS)へ一度も
到達せず、DEEPがMarkdownの表を返すだけで終わっていた
(「このままコピーしてObsidianなどのノートアプリに貼り付けてご利用ください」)。

修正後は、画像添付時にrouter_rules.match_rule_based(user_text)が"CODE"を
返す(=Excel/Word/PowerPoint等を明示的に依頼している)場合のみ、
  1. DEEP(vision対応)で画像を構造化テキスト(表)へ変換し
  2. その結果をCODEモデル(devstral、画像非対応)への依頼文に含めて渡す
という二段構えにする。
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
from ollama_client import OllamaError  # noqa: E402


def make_body_with_image(text: str, chat_id: str = "chat-1") -> dict:
    return {
        "chat_id": chat_id,
        "messages": [
            {"role": "user", "content": text, "images": ["data:image/png;base64,fake"]}
        ],
    }


class TestImageDocumentGeneration(unittest.TestCase):
    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        self._orig_generate = support_ai_auto_pipe.generate
        router.ensure_model_ready = lambda route: None
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

        def fail_call_phi4(system_prompt, user_text):
            raise AssertionError("image + explicit document format should bypass classification")

        router.call_phi4 = fail_call_phi4

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.memory_store = self._orig_memory_store
        support_ai_auto_pipe.generate = self._orig_generate

    def test_image_with_excel_request_digitizes_then_routes_to_code(self):
        body = make_body_with_image("この手書きのワークアウトメモを表にしてExcelに出力して")
        pipe = support_ai_auto_pipe.Pipe()
        pipe.valves.code_execution_mode = "off"  # ACTIONブロック実行は別テストの範囲外

        digitized_table = "| 日付 | 種目 | 回数 |\n|---|---|---|\n| 8/1 | 腕立て | 30 |"
        calls = []

        def fake_generate(model, prompt, **kwargs):
            calls.append({"model": model, "prompt": prompt, "kwargs": kwargs})
            if model == router.ROUTE_MODEL_MAP["DEEP"]:
                return digitized_table
            if model == router.ROUTE_MODEL_MAP["CODE"]:
                return "承知しました。openpyxlでExcelファイルを作成します。"
            raise AssertionError(f"unexpected model: {model}")

        support_ai_auto_pipe.generate = fake_generate
        reply = pipe.pipe(body)

        self.assertIn("[route: CODE]", reply)
        self.assertEqual(len(calls), 2)

        deep_call, code_call = calls
        self.assertEqual(deep_call["model"], router.ROUTE_MODEL_MAP["DEEP"])
        self.assertEqual(deep_call["kwargs"].get("images"), ["data:image/png;base64,fake"])

        self.assertEqual(code_call["model"], router.ROUTE_MODEL_MAP["CODE"])
        # devstralは画像を読めないため、CODE呼び出しにはimagesを渡さず、
        # 代わりにDEEPが読み取った内容をプロンプト文字列へ含めて渡す。
        self.assertNotIn("images", code_call["kwargs"])
        self.assertIn(digitized_table, code_call["prompt"])
        self.assertIn("表にしてExcelに出力して", code_call["prompt"])

    def test_image_without_explicit_format_request_still_forces_deep(self):
        # 明示的な資料形式の指定が無い、通常の画像に関する質問は従来どおりDEEP単発呼び出し。
        body = make_body_with_image("この画像には何が写っていますか?")
        pipe = support_ai_auto_pipe.Pipe()

        calls = []

        def fake_generate(model, prompt, **kwargs):
            calls.append({"model": model, "kwargs": kwargs})
            return "説明文です。"

        support_ai_auto_pipe.generate = fake_generate
        reply = pipe.pipe(body)

        self.assertIn("[route: DEEP]", reply)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], router.ROUTE_MODEL_MAP["DEEP"])
        self.assertEqual(calls[0]["kwargs"].get("images"), ["data:image/png;base64,fake"])

    def test_digitization_failure_falls_back_to_deep_with_image(self):
        # DEEPでの画像読み取り自体が失敗した場合、CODEへは進まず、
        # 従来どおり画像付きでDEEPへ1回だけ問い合わせるフォールバックにする。
        body = make_body_with_image("このメモを表にしてExcelに出力して")
        pipe = support_ai_auto_pipe.Pipe()

        calls = []

        def fake_generate(model, prompt, **kwargs):
            calls.append({"model": model, "kwargs": kwargs})
            if len(calls) == 1:
                raise OllamaError("simulated vision call failure")
            return "フォールバック応答です。"

        support_ai_auto_pipe.generate = fake_generate
        reply = pipe.pipe(body)

        self.assertIn("[route: DEEP]", reply)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c["model"] == router.ROUTE_MODEL_MAP["DEEP"] for c in calls))
        self.assertEqual(calls[1]["kwargs"].get("images"), ["data:image/png;base64,fake"])


if __name__ == "__main__":
    unittest.main()
