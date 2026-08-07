"""テキストをEmbedding単位のチャンクへ分割する。

会話ログ = 発言単位(長すぎる場合のみ文字数で再分割)
Markdownノート = 見出し単位(同上)
単体実行はしない(memory_store.pyからimportして使う部品)。
"""
from __future__ import annotations

import re

MAX_CHARS = 400
OVERLAP = 80


def _split_by_length(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """長すぎるテキストをオーバーラップ付きで機械的に分割する(フォールバック)。"""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    step = max_chars - overlap
    while start < len(text):
        chunks.append(text[start : start + max_chars])
        start += step
    return chunks


def chunk_utterance(text: str) -> list[str]:
    """会話の1発言をチャンクへ分割する(基本は1発言=1チャンク)。"""
    return _split_by_length(text)


def chunk_markdown(text: str) -> list[str]:
    """Markdownを見出し(#〜######)単位で分割する。

    見出し配下が長く機械分割される場合、2チャンク目以降にも見出し行を
    再付与して「何についての記述か」という文脈を保持する(7日目③)。
    """
    parts = re.split(r"(?m)^(?=#{1,6}\s)", text)
    chunks: list[str] = []
    for part in parts:
        lines = part.split("\n", 1)
        heading = lines[0] if re.match(r"^#{1,6}\s", lines[0]) else ""
        pieces = _split_by_length(part)
        for i, piece in enumerate(pieces):
            # 1つ目は元々見出しを含んでいるのでそのまま。
            # 2つ目以降は見出しを失っているため先頭に付け直す。
            if i > 0 and heading and not piece.startswith(heading):
                piece = f"{heading}\n{piece}"
            chunks.append(piece)
    return chunks