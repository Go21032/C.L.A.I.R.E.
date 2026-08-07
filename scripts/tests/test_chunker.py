import sys
import unittest
from pathlib import Path

# chunker.py は scripts/rag_memory/ 配下にあるため、そこへのパスを通す
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rag_memory"))

from chunker import MAX_CHARS, chunk_markdown

# Ruri v2 base の最大トークン数(512)に対する保守的な字数上限。
# 見出し行の再付与でMAX_CHARS(400字)をわずかに超える分の余裕を確認する用途。
RURI_MAX_TOKENS = 512
SAFE_CHAR_LIMIT = RURI_MAX_TOKENS  # 日本語は概ね1文字≒1トークン以上なので保守側に倒す


class TestChunkMarkdownHeadingPreservation(unittest.TestCase):
    """7日目③: 機械分割された2チャンク目以降にも見出しが付くことを確認する。"""

    def test_heading_repeated_in_every_chunk_after_mechanical_split(self):
        heading = "## 実装"
        body = "あ" * (MAX_CHARS + 50)  # 400字を超えて機械分割を発生させる
        text = f"{heading}\n{body}"

        chunks = chunk_markdown(text)

        self.assertGreater(len(chunks), 1, "400字超のため複数チャンクに分割されるはず")
        for i, chunk in enumerate(chunks):
            self.assertTrue(
                chunk.startswith(heading),
                f"チャンク{i}が見出しを含んでいない: {chunk[:30]!r}",
            )

    def test_max_chunk_length_has_margin_against_ruri_token_limit(self):
        # 見出し再付与分の超過を含めても512トークン上限に対して余裕があることを確認
        heading = "## 見出し"
        body = "あ" * (MAX_CHARS + 50)
        chunks = chunk_markdown(f"{heading}\n{body}")

        max_len = max(len(c) for c in chunks)
        self.assertLess(
            max_len,
            SAFE_CHAR_LIMIT,
            f"最大チャンク長{max_len}字がRuri上限{RURI_MAX_TOKENS}トークンに対して余裕がない",
        )


if __name__ == "__main__":
    unittest.main()
