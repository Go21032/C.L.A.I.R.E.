"""PDF/Word/Excel/PowerPointファイルをテキスト抽出し、ノート取り込み(ingest_notes.py)と
同じ経路(chunker→embed→LanceDB)で「ナレッジ」として永続登録する。

11日目ノート(サポートAI作製計画/11日目Web検索対応・UIデザイン確定・マルチモーダル対応調査.md)④の
改善策のうち、実装コストの低いPDF/Word/Excel/PowerPoint対応(画像は対象外。同ノートに追記した
「画像対応の検討事項」参照)。voice_gateway.pyの`POST/GET /documents`・`DELETE /documents/{filename}`
から呼ばれる想定(CLIからも単体で使える)。

source規約: `f"doc:{filename}"`(ingest_notes.pyの`f"note:{path}"`・memory_store.append_turnの
`f"chat:{chat_id}"`に倣う)。`role="document"` / `route="DOCUMENT"`で登録する。同名ファイルの
再アップロードは ingest_notes.py と同様に「既存行を削除してから追加」でupsert扱いにする。

前提(未インストールなら):
    pip install pdfplumber python-docx openpyxl python-pptx

使い方(CLI):
    python doc_ingest.py <file>              # 単発の取り込み
    python doc_ingest.py --list              # 登録済みナレッジの一覧
    python doc_ingest.py --delete <filename> # 指定ファイル名のナレッジを削除
"""
from __future__ import annotations

import argparse
import io
import uuid
from datetime import datetime
from pathlib import Path

import chunker

# memory_store は config.yaml(db_path等)をimport時に読み込むため、ingest_notes.py と同じ理由で
# ここではトップレベルimportせず、実際にDBへ触る関数の内部で遅延importする。

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx"}
DOC_SOURCE_PREFIX = "doc:"


class UnsupportedFileTypeError(ValueError):
    """対応していない拡張子のファイルが渡された。"""


def extract_text(filename: str, data: bytes) -> str:
    """拡張子に応じてファイルの中身をテキストへ変換する(見出し風の区切り付き)。"""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(data)
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix == ".xlsx":
        return _extract_xlsx(data)
    if suffix == ".pptx":
        return _extract_pptx(data)
    raise UnsupportedFileTypeError(
        f"未対応のファイル形式です: {suffix or '(拡張子なし)'}(対応: {sorted(SUPPORTED_EXTENSIONS)})"
    )


def _extract_pdf(data: bytes) -> str:
    import pdfplumber  # 遅延import(重い依存のためモジュールimport時のコストを避ける)

    pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"## page {i}\n{text}")
    return "\n\n".join(pages)


def _extract_docx(data: bytes) -> str:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_xlsx(data: bytes) -> str:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    parts: list[str] = []
    for sheet in workbook.worksheets:
        sheet_lines = []
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                sheet_lines.append(" | ".join(cells))
        if sheet_lines:
            parts.append(f"## sheet: {sheet.title}\n" + "\n".join(sheet_lines))
    return "\n\n".join(parts)


def _extract_pptx(data: bytes) -> str:
    from pptx import Presentation

    presentation = Presentation(io.BytesIO(data))
    parts = []
    for i, slide in enumerate(presentation.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text.strip())
        if texts:
            parts.append(f"## slide {i}\n" + "\n".join(texts))
    return "\n\n".join(parts)


def _chunks_for_text(text: str) -> list[str]:
    return [c.strip() for c in chunker.chunk_markdown(text) if c.strip()]


def ingest_document(filename: str, data: bytes) -> dict:
    """アップロードされたファイルを抽出→チャンク化→LanceDBへ永続登録する(upsert)。

    戻り値: {"filename": str, "chunks": int}
    """
    import memory_store  # 遅延import(理由は冒頭コメント参照)

    text = extract_text(filename, data)
    chunks = _chunks_for_text(text)

    source = f"{DOC_SOURCE_PREFIX}{filename}"
    safe_source = source.replace("'", "''")
    table = memory_store._table()
    # 冪等性: 同一ファイル名の既存行を先に削除してから追加する(ingest_notes.pyと同じupsert方式)
    table.delete(f"source = '{safe_source}'")

    if chunks:
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            {
                "id": str(uuid.uuid4()),
                "date": now,
                "source": source,
                "role": "document",
                "route": "DOCUMENT",
                "topic": filename,
                "content": c,
                "vector": memory_store.embed(c, is_query=False),
            }
            for c in chunks
        ]
        table.add(rows)

    return {"filename": filename, "chunks": len(chunks)}


def list_documents() -> list[dict]:
    """登録済みナレッジをファイル名ごとに集計して返す(チャンク数・最終登録日時)。"""
    import memory_store  # 遅延import

    table = memory_store._table()
    if table.count_rows() == 0:
        return []
    df = table.to_pandas()
    docs = df[df["source"].str.startswith(DOC_SOURCE_PREFIX)]
    if docs.empty:
        return []
    grouped = docs.groupby("source").agg(chunks=("id", "count"), date=("date", "max")).reset_index()
    results = [
        {
            "filename": row["source"][len(DOC_SOURCE_PREFIX):],
            "chunks": int(row["chunks"]),
            "date": row["date"],
        }
        for _, row in grouped.iterrows()
    ]
    return sorted(results, key=lambda r: r["date"], reverse=True)


def delete_document(filename: str) -> int:
    """指定ファイル名のナレッジを削除する。削除した行数を返す。"""
    import memory_store  # 遅延import

    source = f"{DOC_SOURCE_PREFIX}{filename}"
    safe_source = source.replace("'", "''")
    table = memory_store._table()
    before = table.count_rows()
    table.delete(f"source = '{safe_source}'")
    after = table.count_rows()
    return before - after


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF/Word/Excel/PowerPointをナレッジ(記憶DB)へ取り込む")
    parser.add_argument("file", nargs="?", default=None, help="取り込むファイルのパス")
    parser.add_argument("--list", action="store_true", help="登録済みナレッジの一覧を表示する")
    parser.add_argument("--delete", metavar="FILENAME", default=None, help="指定ファイル名のナレッジを削除する")
    args = parser.parse_args()

    if args.list:
        for doc in list_documents():
            print(f"{doc['filename']}: {doc['chunks']}チャンク (最終登録: {doc['date']})")
        return

    if args.delete:
        deleted = delete_document(args.delete)
        print(f"{args.delete}: {deleted}行削除しました")
        return

    if not args.file:
        raise SystemExit("ファイルパス、--list、--delete のいずれかを指定してください")

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"対象ファイルが存在しません: {path}")

    result = ingest_document(path.name, path.read_bytes())
    print(f"{result['filename']}: {result['chunks']}チャンクを登録しました")


if __name__ == "__main__":
    main()
