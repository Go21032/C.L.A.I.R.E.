"""C.L.A.I.R.E.の長期記憶(LanceDB)への登録・検索を担う部品。

単体実行はしない(support_ai_auto_pipe.py からimportして使う部品)。
config.yaml のパス設定を唯一の情報源とし、Pipe側にパスを書かない。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import lancedb
import ollama
import yaml

import chunker

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

DB_PATH = CFG["db_path"]
EMBED_MODEL = CFG["embed_model"]
DOC_PREFIX = CFG["doc_prefix"]
QUERY_PREFIX = CFG["query_prefix"]
TABLE = "conversations"

# 5日目⑩:Ollamaは既定で空きGPUを使おうとする。残VRAMは約1GBしかなく、
# Gemma側とVRAMを取り合うとOOM・速度低下を招くためCPUへ固定する。
os.environ.setdefault("OLLAMA_NUM_GPU", "0")


def embed(text: str, is_query: bool = False) -> list[float]:
    prefix = QUERY_PREFIX if is_query else DOC_PREFIX
    return ollama.embed(model=EMBED_MODEL, input=f"{prefix}{text}")["embeddings"][0]


def _table():
    return lancedb.connect(DB_PATH).open_table(TABLE)


def count_rows() -> int:
    """テーブルの現在の行数を返す。

    6日目⑧「memory_enabled=OFFで書き戻しが本当に起きていないか」を自動検証する際に、
    Pipe呼び出しの前後でこの値を比較して「行数が変化しない」ことを機械的に確認するために使う。
    """
    return _table().count_rows()


def append_turn(chat_id: str, role: str, route: str, text: str, topic: str = "") -> int:
    """1発言を記憶DBへ登録する。登録した行数を返す。"""
    chunks = chunker.chunk_utterance(text)
    if not chunks:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        {
            "id": str(uuid.uuid4()),
            "date": now,
            "source": f"chat:{chat_id}",
            "role": role,
            "route": route,
            "topic": topic,
            "content": c,
            "vector": embed(c, is_query=False),
        }
        for c in chunks
    ]
    _table().add(rows)
    return len(rows)


def retrieve(query: str, limit: int = 3, route: str | None = None) -> list[dict]:
    """クエリに意味的に近い過去の記憶を返す。routeを渡すとそのrouteに絞り込む。"""
    table = _table()
    if table.count_rows() == 0:
        # ②の警告どおり、db_path誤指定で空DBが新規作成された場合をここで検出する
        print("[memory_store] 警告: テーブルが空です。db_pathの指定を確認してください")
        return []
    search = table.search(embed(query, is_query=True))
    if route:
        search = search.where(f"route = '{route}'")
    df = search.limit(limit).to_pandas()
    return df[["content", "date", "role", "route", "_distance"]].to_dict("records")


def format_context(hits: list[dict], max_distance: float = 0.45) -> str:
    """検索結果をプロンプトへ差し込む文字列に整形する。
    距離が遠い(=関係ない)記憶は足を引っ張るのでmax_distanceで足切りする。"""
    useful = [h for h in hits if h["_distance"] <= max_distance]
    if not useful:
        return ""
    lines = [f"- ({h['date']}) {h['content']}" for h in useful]
    return "以下は過去の会話からの参考情報です。関連する場合のみ利用してください。\n" + "\n".join(lines)