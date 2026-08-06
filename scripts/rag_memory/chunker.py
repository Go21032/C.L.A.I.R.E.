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
    """Markdownを見出し(#〜######)単位で分割する。見出し行はチャンク先頭に残す
    (「何についての記述か」という文脈をベクトルに含めるため)。"""
    parts = re.split(r"(?m)^(?=#{1,6}\s)", text)
    chunks: list[str] = []
    for part in parts:
        chunks.extend(_split_by_length(part))
    return chunks