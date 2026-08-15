"""web_search.py — 13日目④:11日目②で立てたSearXNG(http://127.0.0.1:8888)を叩く
検索部品。I/Fはmemory_store.pyの検索I/Fに寄せる(11日目②の方針)。

失敗(SearXNG未起動・タイムアウト・不正レスポンス等)しても例外を投げず空リストを
返す。Web検索1つの不調で会話全体(応答生成)を止めないため
(stt_engine.STTEngineのon_error方針と同じ考え方)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

SEARXNG_URL = "http://127.0.0.1:8888/search"
DEFAULT_LIMIT = 5
DEFAULT_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def search(
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    http_get: Callable | None = None,
) -> list[SearchResult]:
    """SearXNGへ問い合わせて検索結果を返す。失敗時は例外を投げずに空リストを返す。"""
    if http_get is None:
        import requests  # noqa: PLC0415 - テストでは注入するため実運用時のみ必要

        http_get = requests.get
    try:
        resp = http_get(
            SEARXNG_URL,
            params={"q": query, "format": "json", "language": "ja"},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 - 検索の失敗で会話全体を落とさない
        return []
    return [
        SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
        for r in payload.get("results", [])[:limit]
    ]


def format_for_prompt(results: list[SearchResult]) -> str:
    """検索結果をプロンプトへ差し込める形に整形する(RAGの記憶差し込みと同じ考え方)。"""
    if not results:
        return ""
    lines = ["以下はWeb検索の結果です。回答の根拠に使い、末尾に出典URLを示してください。"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}\n{r.snippet}\n出典: {r.url}")
    return "\n".join(lines)
