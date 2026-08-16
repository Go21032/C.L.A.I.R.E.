"""14日目④追記: markdown_docs.py の単体テスト。

parse_blocks()/build_paragraph_requests()はGoogle API非依存の純粋関数なので、
ここではネットワークにもgoogleapiclientにも触れずにテストできる。
insert_table_block()はservice呼び出しを含むため、こちらはfake_serviceで検証する。
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

import markdown_docs


class TestParseBlocksHeadingAndParagraph(unittest.TestCase):
    def test_heading_levels(self):
        blocks = markdown_docs.parse_blocks("# 見出し1\n## 見出し2\n### 見出し3\n")
        self.assertEqual([b["level"] for b in blocks], [1, 2, 3])
        self.assertTrue(all(b["type"] == "heading" for b in blocks))

    def test_plain_paragraph(self):
        blocks = markdown_docs.parse_blocks("ただの文章です")
        self.assertEqual(blocks, [{"type": "paragraph", "runs": [("ただの文章です", False)]}])

    def test_bold_inline_span(self):
        blocks = markdown_docs.parse_blocks("前置き**太字部分**後置き")
        self.assertEqual(
            blocks[0]["runs"],
            [("前置き", False), ("太字部分", True), ("後置き", False)],
        )

    def test_blank_lines_are_skipped(self):
        blocks = markdown_docs.parse_blocks("段落1\n\n\n段落2")
        self.assertEqual(len(blocks), 2)


class TestParseBlocksBulletList(unittest.TestCase):
    def test_consecutive_bullets_form_one_block(self):
        blocks = markdown_docs.parse_blocks("- 項目1\n- 項目2\n* 項目3\n")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "bullet_list")
        self.assertEqual([r[0][0] for r in blocks[0]["items"]], ["項目1", "項目2", "項目3"])


class TestParseBlocksTable(unittest.TestCase):
    def test_header_and_rows(self):
        text = "| 大胸筋 | 種目 |\n| --- | --- |\n| a | b |\n| c | d |\n"
        blocks = markdown_docs.parse_blocks(text)
        self.assertEqual(len(blocks), 1)
        table = blocks[0]
        self.assertEqual(table["type"], "table")
        self.assertEqual([r[0][0] for r in table["header"]], ["大胸筋", "種目"])
        self.assertEqual(len(table["rows"]), 2)
        self.assertEqual(table["rows"][0][0][0][0], "a")

    def test_table_without_header_row(self):
        # 区切り行がテーブルの先頭に来るケース(ヘッダーテキストが無い)
        text = "| --- | --- |\n| a | b |\n"
        blocks = markdown_docs.parse_blocks(text)
        self.assertEqual(blocks[0]["header"], [])
        self.assertEqual(len(blocks[0]["rows"]), 1)

    def test_br_tag_becomes_newline_and_other_tags_are_stripped(self):
        text = "| 見出し |\n| --- |\n| 1行目<br>2行目</sub> |\n"
        blocks = markdown_docs.parse_blocks(text)
        cell_text = blocks[0]["rows"][0][0][0][0]
        self.assertEqual(cell_text, "1行目\n2行目")

    def test_bold_inside_table_cell(self):
        text = "| a |\n| --- |\n| **強調** |\n"
        blocks = markdown_docs.parse_blocks(text)
        self.assertEqual(blocks[0]["rows"][0][0][0], ("強調", True))


class TestBuildParagraphRequests(unittest.TestCase):
    def test_plain_paragraph_advances_cursor_by_text_length_plus_newline(self):
        requests, next_cursor = markdown_docs.build_paragraph_requests(1, [("あいう", False)])
        self.assertEqual(requests, [{"insertText": {"location": {"index": 1}, "text": "あいう\n"}}])
        self.assertEqual(next_cursor, 1 + len("あいう\n"))

    def test_heading_adds_paragraph_style_request(self):
        requests, _ = markdown_docs.build_paragraph_requests(1, [("見出し", False)], heading_level=2)
        style_requests = [r for r in requests if "updateParagraphStyle" in r]
        self.assertEqual(len(style_requests), 1)
        self.assertEqual(
            style_requests[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"], "HEADING_2"
        )

    def test_bold_run_adds_text_style_request_at_correct_offset(self):
        requests, _ = markdown_docs.build_paragraph_requests(1, [("abc", False), ("def", True)])
        style_requests = [r for r in requests if "updateTextStyle" in r]
        self.assertEqual(len(style_requests), 1)
        rng = style_requests[0]["updateTextStyle"]["range"]
        self.assertEqual((rng["startIndex"], rng["endIndex"]), (1 + 3, 1 + 3 + 3))

    def test_bullet_adds_create_paragraph_bullets_request(self):
        requests, _ = markdown_docs.build_paragraph_requests(1, [("項目", False)], bullet=True)
        self.assertTrue(any("createParagraphBullets" in r for r in requests))


class TestInsertTableBlock(unittest.TestCase):
    def _fake_service_for_2x2_table(self):
        fake_service = mock.MagicMock()
        get_responses = [
            {
                "body": {
                    "content": [
                        {
                            "startIndex": 1,
                            "endIndex": 21,
                            "table": {
                                "tableRows": [
                                    {
                                        "tableCells": [
                                            {"content": [{"startIndex": 4}]},
                                            {"content": [{"startIndex": 7}]},
                                        ]
                                    },
                                    {
                                        "tableCells": [
                                            {"content": [{"startIndex": 12}]},
                                            {"content": [{"startIndex": 15}]},
                                        ]
                                    },
                                ]
                            },
                        }
                    ]
                }
            },
            {"body": {"content": [{"startIndex": 1, "endIndex": 22}]}},
        ]
        fake_service.documents.return_value.get.return_value.execute.side_effect = get_responses
        return fake_service

    def test_inserts_table_then_fills_cells_back_to_front(self):
        fake_service = self._fake_service_for_2x2_table()
        table_block = {
            "header": [[("h1", False)], [("h2", False)]],
            "rows": [[[("r1c1", False)], [("r1c2", False)]]],
        }
        next_cursor = markdown_docs.insert_table_block(fake_service, "DOC1", 1, table_block)

        batch_call = fake_service.documents.return_value.batchUpdate
        self.assertEqual(batch_call.call_count, 2)
        insert_table_req = batch_call.call_args_list[0].kwargs["body"]["requests"][0]
        self.assertEqual(insert_table_req["insertTable"], {"rows": 2, "columns": 2, "location": {"index": 1}})

        cell_fill_requests = batch_call.call_args_list[1].kwargs["body"]["requests"]
        insert_texts = [r["insertText"] for r in cell_fill_requests if "insertText" in r]
        # 後方(行2)から先に書き込まれていること
        self.assertEqual(insert_texts[0]["location"]["index"], 15)
        self.assertEqual(insert_texts[-1]["location"]["index"], 4)
        self.assertEqual(next_cursor, 21)

    def test_header_row_is_bold_even_without_markdown_bold_markers(self):
        fake_service = self._fake_service_for_2x2_table()
        table_block = {
            "header": [[("h1", False)], [("h2", False)]],
            "rows": [[[("r1c1", False)], [("r1c2", False)]]],
        }
        markdown_docs.insert_table_block(fake_service, "DOC1", 1, table_block)
        batch_call = fake_service.documents.return_value.batchUpdate
        cell_fill_requests = batch_call.call_args_list[1].kwargs["body"]["requests"]
        bold_ranges = [r["updateTextStyle"]["range"] for r in cell_fill_requests if "updateTextStyle" in r]
        # ヘッダー2セルぶんのbold指定があること(データ行は太字指定なし)
        self.assertEqual(len(bold_ranges), 2)

    def test_empty_table_returns_cursor_unchanged(self):
        fake_service = mock.MagicMock()
        next_cursor = markdown_docs.insert_table_block(fake_service, "DOC1", 5, {"header": [], "rows": []})
        self.assertEqual(next_cursor, 5)
        fake_service.documents.return_value.batchUpdate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
