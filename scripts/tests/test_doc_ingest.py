"""tests/test_doc_ingest.py
------------------------------
11日目ノート④: PDF/Word/Excel/PowerPointのテキスト抽出(doc_ingest.py)のテスト。

memory_store(config.yaml依存)へは触れない範囲(拡張子ディスパッチ・実際の
テキスト抽出そのもの)だけを対象にする。ingest_document()/list_documents()/
delete_document()はLanceDB接続が要るため、ここでは対象外(実機での動作確認は
smoke_test_pipe.py同様に手動で行う)。

PDFは書き込み用ライブラリ(reportlab等)をこの取り込み機能のためだけに追加する
コストを避け、実際のpdfplumber読み取りは手動確認に留める。拡張子ディスパッチ
(_extract_pdfが呼ばれること)自体はモックで検証する。
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
RAG_MEMORY_DIR = SCRIPTS_DIR / "rag_memory"
for p in (RAG_MEMORY_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import doc_ingest  # noqa: E402


class TestExtractTextDispatch(unittest.TestCase):
    """拡張子ごとに正しい抽出関数へディスパッチされることを確認する。"""

    def test_unsupported_extension_raises(self):
        with self.assertRaises(doc_ingest.UnsupportedFileTypeError):
            doc_ingest.extract_text("image.png", b"dummy")

    def test_no_extension_raises(self):
        with self.assertRaises(doc_ingest.UnsupportedFileTypeError):
            doc_ingest.extract_text("noext", b"dummy")

    def test_pdf_dispatches_to_pdf_extractor(self):
        with patch.object(doc_ingest, "_extract_pdf", return_value="pdf-text") as mocked:
            result = doc_ingest.extract_text("a.PDF", b"data")  # 大文字拡張子でも動くこと
        mocked.assert_called_once_with(b"data")
        self.assertEqual(result, "pdf-text")

    def test_docx_dispatches_to_docx_extractor(self):
        with patch.object(doc_ingest, "_extract_docx", return_value="docx-text") as mocked:
            result = doc_ingest.extract_text("a.docx", b"data")
        mocked.assert_called_once_with(b"data")
        self.assertEqual(result, "docx-text")

    def test_xlsx_dispatches_to_xlsx_extractor(self):
        with patch.object(doc_ingest, "_extract_xlsx", return_value="xlsx-text") as mocked:
            result = doc_ingest.extract_text("a.xlsx", b"data")
        mocked.assert_called_once_with(b"data")
        self.assertEqual(result, "xlsx-text")

    def test_pptx_dispatches_to_pptx_extractor(self):
        with patch.object(doc_ingest, "_extract_pptx", return_value="pptx-text") as mocked:
            result = doc_ingest.extract_text("a.pptx", b"data")
        mocked.assert_called_once_with(b"data")
        self.assertEqual(result, "pptx-text")


class TestDocxExtraction(unittest.TestCase):
    """python-docxで実際にファイルを作成し、往復でテキストが取れることを確認する。"""

    def test_paragraphs_and_table_are_extracted(self):
        import docx

        document = docx.Document()
        document.add_paragraph("これは段落1です。")
        document.add_paragraph("これは段落2です。")
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "見出しA"
        table.rows[0].cells[1].text = "見出しB"

        buf = io.BytesIO()
        document.save(buf)

        text = doc_ingest.extract_text("test.docx", buf.getvalue())
        self.assertIn("これは段落1です。", text)
        self.assertIn("これは段落2です。", text)
        self.assertIn("見出しA", text)
        self.assertIn("見出しB", text)


class TestXlsxExtraction(unittest.TestCase):
    """openpyxlで実際にファイルを作成し、往復でテキストが取れることを確認する。"""

    def test_cells_are_extracted(self):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["名前", "値"])
        ws.append(["テスト太郎", 123])

        buf = io.BytesIO()
        wb.save(buf)

        text = doc_ingest.extract_text("test.xlsx", buf.getvalue())
        self.assertIn("Sheet1", text)
        self.assertIn("名前", text)
        self.assertIn("テスト太郎", text)
        self.assertIn("123", text)


class TestPptxExtraction(unittest.TestCase):
    """python-pptxで実際にファイルを作成し、往復でテキストが取れることを確認する。"""

    def test_slide_text_is_extracted(self):
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # 白紙レイアウト
        textbox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        textbox.text_frame.text = "スライドの本文テキスト"

        buf = io.BytesIO()
        prs.save(buf)

        text = doc_ingest.extract_text("test.pptx", buf.getvalue())
        self.assertIn("slide 1", text)
        self.assertIn("スライドの本文テキスト", text)


if __name__ == "__main__":
    unittest.main()
