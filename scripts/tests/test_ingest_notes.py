import sys
import unittest
from pathlib import Path

# ingest_notes.py は scripts/rag_memory/ 配下にあるため、そこへのパスを通す
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rag_memory"))

from ingest_notes import _is_excluded, _strip_frontmatter


class TestStripFrontmatter(unittest.TestCase):
    """7日目④: frontmatterを除去し本文だけを取り込み対象にする。"""

    def test_frontmatter_block_is_removed(self):
        text = (
            "---\n"
            "project: C.L.A.I.R.E.\n"
            "tags: [a, b]\n"
            "---\n"
            "## 本文\n"
            "ここが本文です。"
        )
        result = _strip_frontmatter(text)
        self.assertNotIn("project:", result)
        self.assertNotIn("tags:", result)
        self.assertTrue(result.startswith("## 本文"))

    def test_text_without_frontmatter_is_unchanged(self):
        text = "## 本文\nfrontmatterがない場合はそのまま。"
        self.assertEqual(_strip_frontmatter(text), text)


class TestIsExcluded(unittest.TestCase):
    """7日目④: .obsidian/添付ファイル/テンプレート配下を取り込み対象から除外する。"""

    def test_dot_obsidian_is_excluded(self):
        self.assertTrue(_is_excluded(Path(".obsidian/app.json")))

    def test_attachments_is_excluded(self):
        self.assertTrue(_is_excluded(Path("サポートAI作製計画/attachments/img.md")))

    def test_normal_note_is_not_excluded(self):
        self.assertFalse(_is_excluded(Path("サポートAI作製計画/7日目Obsidianノート取り込みとCloudflareTunnel.md")))

    def test_nested_pycache_is_excluded(self):
        self.assertTrue(_is_excluded(Path("サポートAI作製計画/scripts/__pycache__/foo.md")))


if __name__ == "__main__":
    unittest.main()
