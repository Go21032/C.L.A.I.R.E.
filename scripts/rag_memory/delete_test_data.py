"""5日目のテストデータ(route='TEST')を削除する。"""
from pathlib import Path
import yaml
import lancedb

# config.yaml から接続情報を読み込む
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

DB_PATH = CFG["db_path"]
TABLE = "conversations"

# DB接続して削除実行
db = lancedb.connect(DB_PATH)
table = db.open_table(TABLE)

# 削除前の行数
before = table.count_rows()

# route='TEST' のデータを削除
table.delete("route = 'TEST'")

# 削除後の行数
after = table.count_rows()

print(f"削除前: {before}件")
print(f"削除後: {after}件")
print(f"削除数: {before - after}件")
print("✅ 削除完了")
