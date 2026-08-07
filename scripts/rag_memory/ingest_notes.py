"""Obsidianのノートを記憶DB(LanceDB)へ取り込む。

会話ログ(memory_store.append_turn)と違い、ノートは「更新され続ける」ため、
同一source(note:<vault相対パス>)の既存行を削除してから登録し直す(冪等・upsert相当)。

使い方:
    python ingest_notes.py                    # 既定(config.yamlのingest_default_path)を取り込む
    python ingest_notes.py --dry-run          # 登録せずに対象ファイルとチャンク数だけ表示
    python ingest_notes.py --path <vault相対パス>   # 対象を限定して取り込む(ファイル/ディレクトリどちらも可)

前提:
    config.yaml に vault_root(vaultの絶対パス)/ ingest_default_path(既定の取り込み対象。
    vault_root からの相対パス)が設定されていること(7日目④)。
"""
from __future__ import annotations

import argparse
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import chunker

# memory_store は config.yaml(vault_root等)をimport時に読み込むため、ここでは
# import しない。pytest でこのファイルの純粋関数(_strip_frontmatter/_is_excluded等)
# だけをテストする際に、config.yamlの実行場所依存(D:側/C:側)に引っかからないようにするため、
# 実際にDBへ接続する関数(ingest/main)の内部で遅延importする。

# vault内のどの階層に出現しても取り込み対象から除外するディレクトリ名(7日目④の設計論点)
EXCLUDE_DIR_NAMES = {
    ".obsidian",
    ".git",
    ".pytest_cache",
    "__pycache__",
    "attachments",
    "templates",
    "テンプレート",
}

# frontmatter(先頭の --- 〜 --- ブロック)を除去する。YAMLのキー名がベクトルに
# 混ざると検索精度を下げるため、本文だけを取り込む(7日目④の設計論点)。
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _is_excluded(rel_path: Path) -> bool:
    """vault_rootからの相対パスに、除外ディレクトリ名が含まれているか判定する。"""
    return any(part in EXCLUDE_DIR_NAMES for part in rel_path.parts[:-1])


def _iter_target_files(target: Path, vault_root: Path) -> list[Path]:
    """取り込み対象の.mdファイル一覧を返す(除外パターン適用済み・パス順)。"""
    if target.is_file():
        candidates = [target] if target.suffix == ".md" else []
    else:
        candidates = target.rglob("*.md")
    files = [
        p for p in candidates
        if not _is_excluded(p.relative_to(vault_root))
    ]
    return sorted(files)


def _chunks_for_file(md_path: Path) -> list[str]:
    text = md_path.read_text(encoding="utf-8")
    body = _strip_frontmatter(text)
    return [c.strip() for c in chunker.chunk_markdown(body) if c.strip()]


def ingest(target: Path, vault_root: Path, dry_run: bool) -> None:
    import memory_store  # 遅延import(理由は冒頭コメント参照)

    files = _iter_target_files(target, vault_root)
    print(f"対象ファイル数: {len(files)}")

    table = None if dry_run else memory_store._table()
    total_chunks = 0
    t0 = time.perf_counter()

    for f in files:
        rel = f.relative_to(vault_root).as_posix()
        chunks = _chunks_for_file(f)
        total_chunks += len(chunks)
        print(f"  {rel}: {len(chunks)}チャンク")

        if dry_run:
            continue

        source = f"note:{rel}"
        safe_source = source.replace("'", "''")
        # 冪等性: 同一sourceの既存行を先に削除してから追加する(upsert相当)
        table.delete(f"source = '{safe_source}'")

        if not chunks:
            continue

        now = datetime.now().isoformat(timespec="seconds")
        topic = f.stem
        rows = [
            {
                "id": str(uuid.uuid4()),
                "date": now,
                "source": source,
                "role": "note",
                "route": "NOTE",
                "topic": topic,
                "content": c,
                "vector": memory_store.embed(c, is_query=False),
            }
            for c in chunks
        ]
        table.add(rows)

    elapsed = time.perf_counter() - t0
    print(f"\n対象ファイル数: {len(files)} / 総チャンク数: {total_chunks} / 所要時間: {elapsed:.1f}秒")
    if dry_run:
        print("(--dry-run のため実際の登録は行っていません)")


def main() -> None:
    import memory_store  # 遅延import(理由は冒頭コメント参照)

    parser = argparse.ArgumentParser(description="Obsidianのノートを記憶DBへ取り込む")
    parser.add_argument("--dry-run", action="store_true", help="登録せず対象ファイル・チャンク数だけ表示する")
    parser.add_argument(
        "--path", default=None,
        help="取り込み対象のvault相対パス(既定: config.yamlのingest_default_path)",
    )
    args = parser.parse_args()

    vault_root = Path(memory_store.CFG["vault_root"])
    default_path = memory_store.CFG.get("ingest_default_path", "サポートAI作製計画")
    target = vault_root / (args.path or default_path)

    if not target.exists():
        raise SystemExit(f"対象パスが存在しません: {target}")

    ingest(target, vault_root, args.dry_run)


if __name__ == "__main__":
    main()
