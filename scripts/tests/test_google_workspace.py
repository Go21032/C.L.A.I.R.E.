"""14日目④: Google Workspace連携。

このテストは**一度もネットワークへ出てはいけない**。google_workspace._build_service
(またはそのさらに内側のgoogle-auth系関数)を丸ごとモックし、「正しい引数でAPIを
呼んだか」「戻り値からURLを組み立てられたか」だけを見る。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import google_workspace


class TestExportToDocs(unittest.TestCase):
    def test_creates_document_and_returns_url(self):
        fake_service = mock.MagicMock()
        fake_service.documents.return_value.create.return_value.execute.return_value = {
            "documentId": "DOC123"
        }
        with mock.patch.object(google_workspace, "_build_service", return_value=fake_service):
            url = google_workspace.export_to_docs("調査レポート", "本文テキスト")
        self.assertEqual(url, "https://docs.google.com/document/d/DOC123/edit")
        fake_service.documents.return_value.create.assert_called_once_with(
            body={"title": "調査レポート"}
        )
        # 14日目④追記: 単純な1段落は末尾に改行が付いた1回のinsertTextへ変換される
        # (markdown_docs.build_paragraph_requestsを参照)。表を含まないのでbatchUpdateは1回のみ。
        batch_call = fake_service.documents.return_value.batchUpdate
        batch_call.assert_called_once_with(
            documentId="DOC123",
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": "本文テキスト\n"}}]},
        )

    def test_raises_friendly_error_when_not_authenticated(self):
        # 認証情報が無いとき、生のFileNotFoundErrorではなく
        # ユーザーに見せられるメッセージを持つ例外を出すこと
        with mock.patch.object(google_workspace, "CREDENTIALS_PATH", Path("/nonexistent.json")):
            with self.assertRaises(google_workspace.NotAuthenticatedError) as ctx:
                google_workspace.export_to_docs("題", "本文")
        self.assertIn("認証", str(ctx.exception))

    def test_table_body_inserts_table_and_flushes_pending_paragraphs_first(self):
        # 14日目④追記: 見出し+表という構成のとき、①先に見出しのbatchUpdateが
        # flushされ、②insertTableのbatchUpdate、③documents().get()でセル位置取得、
        # ④セルへのテキスト書き込みbatchUpdate、⑤終端位置確認のget()、の順で呼ばれること。
        fake_service = mock.MagicMock()
        fake_service.documents.return_value.create.return_value.execute.return_value = {
            "documentId": "DOC123"
        }
        get_responses = [
            {
                "body": {
                    "content": [
                        {"startIndex": 1, "endIndex": 10},
                        {
                            "startIndex": 10,
                            "endIndex": 30,
                            "table": {
                                "tableRows": [
                                    {
                                        "tableCells": [
                                            {"content": [{"startIndex": 13}]},
                                            {"content": [{"startIndex": 16}]},
                                        ]
                                    },
                                    {
                                        "tableCells": [
                                            {"content": [{"startIndex": 21}]},
                                            {"content": [{"startIndex": 24}]},
                                        ]
                                    },
                                ]
                            },
                        },
                    ]
                }
            },
            {"body": {"content": [{"startIndex": 1, "endIndex": 10}, {"startIndex": 10, "endIndex": 31}]}},
        ]
        fake_service.documents.return_value.get.return_value.execute.side_effect = get_responses

        with mock.patch.object(google_workspace, "_build_service", return_value=fake_service):
            google_workspace.export_to_docs(
                "調査レポート",
                "# 見出し\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n",
            )

        batch_call = fake_service.documents.return_value.batchUpdate
        self.assertEqual(batch_call.call_count, 3)
        first_requests = batch_call.call_args_list[0].kwargs["body"]["requests"]
        self.assertIn("insertText", first_requests[0])
        self.assertEqual(first_requests[0]["insertText"]["text"], "見出し\n")
        second_requests = batch_call.call_args_list[1].kwargs["body"]["requests"]
        self.assertIn("insertTable", second_requests[0])
        self.assertEqual(second_requests[0]["insertTable"]["rows"], 2)
        self.assertEqual(second_requests[0]["insertTable"]["columns"], 2)
        third_requests = batch_call.call_args_list[2].kwargs["body"]["requests"]
        inserted_texts = {r["insertText"]["text"] for r in third_requests if "insertText" in r}
        self.assertEqual(inserted_texts, {"a", "b", "1", "2"})


class TestExportToSheets(unittest.TestCase):
    def test_writes_rows_and_returns_url(self):
        fake_service = mock.MagicMock()
        fake_service.spreadsheets.return_value.create.return_value.execute.return_value = {
            "spreadsheetId": "SHEET123"
        }
        with mock.patch.object(google_workspace, "_build_service", return_value=fake_service):
            url = google_workspace.export_to_sheets("データ", [["a", "b"], ["1", "2"]])
        self.assertEqual(url, "https://docs.google.com/spreadsheets/d/SHEET123/edit")
        update_call = fake_service.spreadsheets.return_value.values.return_value.update
        update_call.assert_called_once_with(
            spreadsheetId="SHEET123",
            range="A1",
            valueInputOption="RAW",
            body={"values": [["a", "b"], ["1", "2"]]},
        )

    def test_empty_rows_skips_values_update(self):
        fake_service = mock.MagicMock()
        fake_service.spreadsheets.return_value.create.return_value.execute.return_value = {
            "spreadsheetId": "SHEET123"
        }
        with mock.patch.object(google_workspace, "_build_service", return_value=fake_service):
            google_workspace.export_to_sheets("データ", [])
        fake_service.spreadsheets.return_value.values.return_value.update.assert_not_called()


class TestUploadToDrive(unittest.TestCase):
    def test_xlsx_is_converted_to_google_sheets(self):
        fake_service = mock.MagicMock()
        fake_service.files.return_value.create.return_value.execute.return_value = {
            "id": "FILE123",
            "webViewLink": "https://drive.google.com/file/d/FILE123/view",
        }
        with mock.patch.object(google_workspace, "_build_service", return_value=fake_service), \
             mock.patch("googleapiclient.http.MediaFileUpload") as fake_media:
            url = google_workspace.upload_to_drive(Path("report.xlsx"))
        self.assertEqual(url, "https://drive.google.com/file/d/FILE123/view")
        _, kwargs = fake_service.files.return_value.create.call_args
        self.assertEqual(
            kwargs["body"]["mimeType"], "application/vnd.google-apps.spreadsheet"
        )
        fake_media.assert_called_once()

    def test_convert_false_does_not_set_mime_type(self):
        fake_service = mock.MagicMock()
        fake_service.files.return_value.create.return_value.execute.return_value = {
            "id": "FILE123",
            "webViewLink": "https://drive.google.com/file/d/FILE123/view",
        }
        with mock.patch.object(google_workspace, "_build_service", return_value=fake_service), \
             mock.patch("googleapiclient.http.MediaFileUpload"):
            google_workspace.upload_to_drive(Path("report.xlsx"), convert=False)
        _, kwargs = fake_service.files.return_value.create.call_args
        self.assertNotIn("mimeType", kwargs["body"])


class TestCheckStatus(unittest.TestCase):
    def test_returns_unauthenticated_when_credentials_missing(self):
        with mock.patch.object(google_workspace, "CREDENTIALS_PATH", Path("/nonexistent.json")):
            status = google_workspace.check_status()
        self.assertFalse(status["authenticated"])
        self.assertIn("認証", status["reason"])

    def test_returns_unauthenticated_when_token_missing(self, tmp_credentials=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            creds_path = Path(tmp) / "credentials.json"
            creds_path.write_text("{}", encoding="utf-8")
            token_path = Path(tmp) / "token.json"
            with mock.patch.object(google_workspace, "CREDENTIALS_PATH", creds_path), \
                 mock.patch.object(google_workspace, "TOKEN_PATH", token_path):
                status = google_workspace.check_status()
        self.assertFalse(status["authenticated"])

    def test_does_not_open_browser_consent(self):
        # check_status()はget_credentials()を呼んではいけない(呼ぶとInstalledAppFlowの
        # run_local_server()でブラウザが勝手に開いてしまう)。get_credentialsをモックし、
        # 呼ばれていないことを確認する。
        with mock.patch.object(google_workspace, "get_credentials") as fake_get_creds:
            google_workspace.check_status()
        fake_get_creds.assert_not_called()


if __name__ == "__main__":
    unittest.main()
