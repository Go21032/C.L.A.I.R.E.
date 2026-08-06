import lancedb
import ollama
import pyarrow as pa
import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

DB_PATH = CFG["db_path"]                 # HDD接続後はここを書き換えるだけで移行できる
EMBED_MODEL = CFG["embed_model"]         # 例: kun432/cl-nagoya-ruri-base
DOC_PREFIX = CFG["doc_prefix"]           # v2: "文章: " / v3: "検索文書: "
QUERY_PREFIX = CFG["query_prefix"]       # v2: "クエリ: " / v3: "検索クエリ: "


def embed(text: str, is_query: bool = False) -> list[float]:
    prefix = QUERY_PREFIX if is_query else DOC_PREFIX
    resp = ollama.embed(model=EMBED_MODEL, input=f"{prefix}{text}")
    return resp["embeddings"][0]


def main():
    dim = len(embed("次元確認用のテキスト"))   # ハードコードせず実測値でスキーマを作る
    print(f"embedding dim = {dim}")

    db = lancedb.connect(DB_PATH)
    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("date", pa.string()),
        pa.field("source", pa.string()),
        pa.field("role", pa.string()),
        pa.field("route", pa.string()),
        pa.field("topic", pa.string()),
        pa.field("content", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
    ])

    if "conversations" not in db.table_names():
        db.create_table("conversations", schema=schema)
        print("テーブル作成完了")
    else:
        print("既存テーブルを使用")


if __name__ == "__main__":
    main()