"""
tests/test_web_search.py
--------------------------
13日目④:`web_search.search`/`format_for_prompt` のユニットテスト。

11日目②で立てたSearXNG(http://127.0.0.1:8888)を実際に叩かず、`http_get`を
フェイクへ差し替えてロジック(JSON→SearchResultへのマッピング・limit・
バックエンド停止時のフォールバック)だけを検証する。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from web_search import SearchResult, format_for_prompt, search  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class TestSearch(unittest.TestCase):
    def test_search_maps_searxng_json_to_results(self):
        payload = {
            "results": [
                {"title": "天気予報", "url": "https://example.com/a", "content": "今日は晴れ"},
                {"title": "週間天気", "url": "https://example.com/b", "content": "明日は雨"},
            ]
        }
        got = search("今日の天気", limit=2, http_get=lambda url, **kw: FakeResponse(payload))
        self.assertEqual(
            got,
            [
                SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ"),
                SearchResult(title="週間天気", url="https://example.com/b", snippet="明日は雨"),
            ],
        )

    def test_search_respects_limit(self):
        payload = {"results": [{"title": f"t{i}", "url": f"u{i}", "content": "c"} for i in range(10)]}
        got = search("q", limit=3, http_get=lambda url, **kw: FakeResponse(payload))
        self.assertEqual(len(got), 3)

    def test_search_returns_empty_list_when_backend_is_down(self):
        def boom(url, **kw):
            raise ConnectionError("searxng down")

        self.assertEqual(search("q", http_get=boom), [])

    def test_search_returns_empty_list_on_http_error(self):
        class ErrorResponse:
            def json(self):
                return {}

            def raise_for_status(self):
                raise RuntimeError("500 Internal Server Error")

        got = search("q", http_get=lambda url, **kw: ErrorResponse())
        self.assertEqual(got, [])

    def test_search_passes_query_and_language_params(self):
        seen = {}

        def fake_get(url, **kw):
            seen["url"] = url
            seen["params"] = kw.get("params")
            return FakeResponse({"results": []})

        search("猫", http_get=fake_get)
        self.assertEqual(seen["params"]["q"], "猫")
        self.assertEqual(seen["params"]["format"], "json")


class TestFormatForPrompt(unittest.TestCase):
    def test_empty_results_returns_empty_string(self):
        self.assertEqual(format_for_prompt([]), "")

    def test_includes_title_snippet_and_source_url(self):
        results = [SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ")]
        out = format_for_prompt(results)
        self.assertIn("天気予報", out)
        self.assertIn("今日は晴れ", out)
        self.assertIn("https://example.com/a", out)


if __name__ == "__main__":
    unittest.main()
