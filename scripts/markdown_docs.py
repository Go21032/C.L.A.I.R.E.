"""
markdown_docs.py — 14日目④追記: Markdown本文をGoogleドキュメントの
構造化コンテンツ(見出し・太字・箇条書き・表)へ変換する。

背景: 当初のexport_to_docs()は本文をそのままinsertTextしていたため、
AIの応答に含まれるMarkdown記法(表の`| --- |`や`**太字**`、見出しの`##`)が
そのまま文字として流し込まれ、Obsidianのような整形された表として見えて
いなかった。このモジュールは:

  1. parse_blocks() — MarkdownテキストをGoogle API非依存のブロック列へ
     分解する純粋関数(単体テストしやすい)
  2. build_paragraph_requests() — 見出し/段落/箇条書きの1ブロックを
     Google Docs APIのbatchUpdateリクエストへ変換する純粋関数
  3. insert_table_block() — 表ブロックをGoogleドキュメントへ実際に
     書き込む(service呼び出しを含む)

表だけは3段階が必要な点に注意(Docs APIの制約):
  ① insertTableで空の表(各セルは空段落1つ)を作る
  ② documents().get()で作られた各セルの開始位置を読み直す
     (insertTableのレスポンスにはセル位置が含まれないため)
  ③ 後ろのセルから前方へ向かってinsertTextする
     (先頭セルから埋めると、後続セルの位置がテキスト分だけ
     ずれてしまうため)
"""

from __future__ import annotations

import re

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{1,}:?$")

Run = tuple[str, bool]  # (テキスト, 太字かどうか)


def _clean_html(text: str) -> str:
    """<br>は改行へ、その他のHTMLタグは削除する(Docs APIに上付き文字等の概念がないため)。"""
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    return text


def _parse_inline(text: str) -> list[Run]:
    """"**太字**"を(テキスト, 太字かどうか)のリストへ分解する。"""
    runs: list[Run] = []
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False))
        runs.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    if not runs:
        runs.append(("", False))
    return runs


def _split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    non_empty = [c for c in cells if c.strip() != ""]
    return bool(non_empty) and all(_SEPARATOR_CELL_RE.match(c.strip()) for c in non_empty)


def parse_blocks(text: str) -> list[dict]:
    """Markdown本文をブロック列へ分解する。

    ブロック種別:
      - {"type": "heading", "level": int, "runs": [Run, ...]}
      - {"type": "paragraph", "runs": [Run, ...]}
      - {"type": "bullet_list", "items": [[Run, ...], ...]}
      - {"type": "table", "header": [[Run,...], ...] | [], "rows": [[[Run,...], ...], ...]}
        (header/各行は「セルのリスト」、各セルは「Runのリスト」。Runのテキストには
        <br>由来の改行\\nが含まれうる。ヘッダー行が無い表はheader=[])
    """
    lines = text.splitlines()
    blocks: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            runs = _parse_inline(_clean_html(m.group(2)))
            blocks.append({"type": "heading", "level": level, "runs": runs})
            i += 1
            continue

        if line.startswith("|"):
            row_lines = []
            while i < n and lines[i].strip().startswith("|"):
                row_lines.append(lines[i].strip())
                i += 1
            rows_cells = [_split_table_row(rl) for rl in row_lines]
            if rows_cells and _is_separator_row(rows_cells[0]):
                header_cells: list[str] = []
                body_rows = rows_cells[1:]
            elif len(rows_cells) >= 2 and _is_separator_row(rows_cells[1]):
                header_cells = rows_cells[0]
                body_rows = rows_cells[2:]
            else:
                header_cells = rows_cells[0]
                body_rows = rows_cells[1:]
            header = [_parse_inline(_clean_html(c)) for c in header_cells]
            rows = [[_parse_inline(_clean_html(c)) for c in row] for row in body_rows]
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue

        m = _BULLET_RE.match(line)
        if m:
            items = []
            while i < n:
                bm = _BULLET_RE.match(lines[i].strip())
                if not bm:
                    break
                items.append(_parse_inline(_clean_html(bm.group(1))))
                i += 1
            blocks.append({"type": "bullet_list", "items": items})
            continue

        runs = _parse_inline(_clean_html(line))
        blocks.append({"type": "paragraph", "runs": runs})
        i += 1

    return blocks


def build_paragraph_requests(
    cursor: int,
    runs: list[Run],
    heading_level: int | None = None,
    bullet: bool = False,
) -> tuple[list[dict], int]:
    """見出し/段落/箇条書き1項目ぶんのbatchUpdateリクエストを組み立てる。

    cursorは挿入先のインデックス。戻り値は(リクエスト列, 挿入後の次のcursor)。
    """
    text = "".join(t for t, _ in runs) + "\n"
    requests: list[dict] = [{"insertText": {"location": {"index": cursor}, "text": text}}]

    if heading_level:
        style = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}.get(heading_level, "HEADING_3")
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": cursor, "endIndex": cursor + len(text)},
                "paragraphStyle": {"namedStyleType": style},
                "fields": "namedStyleType",
            }
        })

    offset = cursor
    for run_text, bold in runs:
        if bold and run_text:
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": offset, "endIndex": offset + len(run_text)},
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            })
        offset += len(run_text)

    if bullet:
        requests.append({
            "createParagraphBullets": {
                "range": {"startIndex": cursor, "endIndex": cursor + len(text)},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }
        })

    return requests, cursor + len(text)


def _find_table_element(document: dict, cursor: int) -> dict:
    """documents().get()の結果から、cursor付近に挿入したtable要素を探す。"""
    content = document.get("body", {}).get("content", [])
    tables = [el["table"] for el in content if "table" in el]
    if not tables:
        raise ValueError("挿入したはずの表要素が見つかりません")
    # 直前に挿入したtableは通常content内の最後のtable要素になる。
    return tables[-1]


def _table_cell_start_indices(table: dict) -> list[list[int]]:
    """各セルへテキストを挿入すべき開始インデックスを、行×列で返す。

    セルの中身は挿入直後、空段落が1つだけの状態になっている。
    その段落のstartIndexがテキストを挿入すべき位置。
    """
    starts: list[list[int]] = []
    for row in table.get("tableRows", []):
        row_starts = []
        for cell in row.get("tableCells", []):
            first_content = cell["content"][0]
            row_starts.append(first_content["startIndex"])
        starts.append(row_starts)
    return starts


def insert_table_block(service, doc_id: str, cursor: int, table_block: dict) -> int:
    """表ブロックをGoogleドキュメントへ書き込み、挿入後の次のcursorを返す。"""
    header = table_block.get("header") or []
    rows = table_block.get("rows") or []
    all_rows = ([header] if header else []) + rows
    num_rows = len(all_rows)
    num_cols = max((len(r) for r in all_rows), default=0)
    if num_rows == 0 or num_cols == 0:
        return cursor

    service.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [
                {
                    "insertTable": {
                        "rows": num_rows,
                        "columns": num_cols,
                        "location": {"index": cursor},
                    }
                }
            ]
        },
    ).execute()

    document = service.documents().get(documentId=doc_id).execute()
    table = _find_table_element(document, cursor)
    cell_starts = _table_cell_start_indices(table)

    text_requests: list[dict] = []
    for r in range(num_rows - 1, -1, -1):
        row_runs = all_rows[r]
        for c in range(num_cols - 1, -1, -1):
            runs = row_runs[c] if c < len(row_runs) else [("", False)]
            cell_text = "".join(t for t, _ in runs)
            if not cell_text:
                continue
            start = cell_starts[r][c]
            text_requests.append({"insertText": {"location": {"index": start}, "text": cell_text}})
            offset = start
            for run_text, bold in runs:
                want_bold = bold or (header and r == 0)
                if want_bold and run_text:
                    text_requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": offset, "endIndex": offset + len(run_text)},
                            "textStyle": {"bold": True},
                            "fields": "bold",
                        }
                    })
                offset += len(run_text)

    if text_requests:
        service.documents().batchUpdate(documentId=doc_id, body={"requests": text_requests}).execute()

    document = service.documents().get(documentId=doc_id).execute()
    end_index = document["body"]["content"][-1]["endIndex"]
    return end_index - 1
