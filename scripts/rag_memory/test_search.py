# D:\sapo_ai\rag_memory\scripts\test_search.py
import uuid
from datetime import date
from pathlib import Path

import lancedb
import ollama
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

DB_PATH = CFG["db_path"]
EMBED_MODEL = CFG["embed_model"]
DOC_PREFIX = CFG["doc_prefix"]
QUERY_PREFIX = CFG["query_prefix"]


def embed(text: str, is_query: bool = False) -> list[float]:
    prefix = QUERY_PREFIX if is_query else DOC_PREFIX
    resp = ollama.embed(model=EMBED_MODEL, input=f"{prefix}{text}")
    return resp["embeddings"][0]


# 意味検索の効果を確認するため、わざと話題の異なる文をまとめて登録する
SAMPLES = [
    "今日は学習計画を立てて、来週までにRAGの実装を終わらせる予定です。",
    "夕食は何を作ろうか迷っている。冷蔵庫に鶏肉があったはず。",
    "Pythonのlancedbライブラリでベクトル検索のテストをしている。",
    "明日は病院の予約があるので午前中は出かける。",
]


def insert_samples():
    db = lancedb.connect(DB_PATH)
    table = db.open_table("conversations")
    rows = [
        {
            "id": str(uuid.uuid4()),
            "date": date.today().isoformat(),
            "source": "test_search.py",
            "role": "user",
            "route": "TEST",
            "topic": "動作確認",
            "content": text,
            "vector": embed(text, is_query=False),
        }
        for text in SAMPLES
    ]
    table.add(rows)
    print(f"{len(rows)}件登録しました")


def search(query_text: str, limit: int = 3):
    db = lancedb.connect(DB_PATH)
    table = db.open_table("conversations")
    qvec = embed(query_text, is_query=True)
    results = table.search(qvec).limit(limit).to_pandas()
    print(f"\nクエリ: 「{query_text}」の検索結果(上位{limit}件)")
    print(results[["content", "_distance"]].to_string(index=False))


if __name__ == "__main__":
    insert_samples()
    # 登録文は「学習計画を立てて」、検索文はあえて表記を変えた「勉強の予定を組みたい」
    search("勉強の予定を組みたい")