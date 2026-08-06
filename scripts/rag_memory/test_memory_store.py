# D:\sapo_ai\rag_memory\scripts\test_memory_store.py
"""
6日目ノート(サポートAI作製計画/6日目RAG記憶レイヤーのPipe組み込み.md)
③chunker.py・④memory_store.pyの動作確認用スクリプト。

【重要】このファイルはバックアップ用に
  C:\\Users\\gakuh\\Documents\\obsidian\\サポートAI作製計画\\scripts\\rag_memory\\
に置いているが、実行はこのままでは失敗する。memory_store.pyは
`Path(__file__).resolve().parent.parent / "config.yaml"` でconfig.yamlを探すため、
実行するファイル自身が
  D:\\sapo_ai\\rag_memory\\scripts\\test_memory_store.py
の位置(=config.yamlの1階層下)にある必要がある。
→ 使う前に、このファイルをD:\\sapo_ai\\rag_memory\\scripts\\ へコピーしてから実行すること。

やること:
  1. chunker.py: 短文/長文/Markdown見出しでの分割結果を出力し、目視で確認する
  2. memory_store.py: 話題の異なるサンプルを登録(append_turn)し、
     意味検索(retrieve)・route絞り込み・format_contextの足切りが機能するか確認する
  3. embed/retrieve/append_turnの所要時間を計測する(⑧のレイテンシ考察の材料)
  4. このスクリプトが登録したテストデータ(route LIKE 'TEST_MEMORY_STORE%')だけを
     最後に削除し、本番の記憶DBを汚さない

単体実行するスクリプト: `python test_memory_store.py`
"""
from __future__ import annotations

import time

import chunker
import memory_store

# 5日目からの既存テストデータ(route='TEST')や、将来の本番データ(FAST/DEEP/CODE)と
# 混ざらないよう、このスクリプト専用のroute名前空間を使う。最後に一括削除する。
TEST_ROUTE = "TEST_MEMORY_STORE"
TEST_ROUTE_CODE = "TEST_MEMORY_STORE_CODE"  # route絞り込み確認用


def _section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


# ---------------------------------------------------------------------------
# 1. chunker.py の確認
# ---------------------------------------------------------------------------

def test_chunker() -> None:
    _section("1. chunker.py の確認")

    short_text = "今日は天気がいいですね。"
    chunks = chunker.chunk_utterance(short_text)
    print(f"[短文発言] 入力{len(short_text)}字 → {len(chunks)}チャンク")
    assert chunks == [short_text], "短文はそのまま1チャンクになるはず"
    print("  OK: 短文は分割されない")

    # MAX_CHARS(400)を超える長文発言 → オーバーラップ付きで機械分割されるはず
    long_text = "あ" * 1000
    chunks = chunker.chunk_utterance(long_text)
    print(f"[長文発言] 入力{len(long_text)}字 → {len(chunks)}チャンク "
          f"(各チャンク長: {[len(c) for c in chunks]})")
    assert len(chunks) > 1, "400字超なら複数チャンクに分かれるはず"
    # オーバーラップ(80字)が実際に効いているか: 隣接チャンクの末尾80字と
    # 次チャンクの先頭80字が一致するはず
    overlap_ok = chunks[0][-chunker.OVERLAP:] == chunks[1][:chunker.OVERLAP]
    print(f"  オーバーラップ({chunker.OVERLAP}字)が一致: {overlap_ok}")
    assert overlap_ok, "オーバーラップ部分が一致していない"
    print("  OK: 長文はオーバーラップ付きで分割される")

    # Markdown見出し単位の分割
    md_text = (
        "## 背景\n"
        "これは背景の説明です。\n\n"
        "## 実装\n"
        "これは実装の説明です。" + "あ" * 500 + "\n\n"
        "## まとめ\n"
        "これはまとめです。"
    )
    chunks = chunker.chunk_markdown(md_text)
    print(f"[Markdown] 見出し3つ(うち1つは400字超) → {len(chunks)}チャンク")
    for i, c in enumerate(chunks):
        preview = c.replace("\n", "\\n")[:40]
        print(f"  chunk[{i}] ({len(c)}字): {preview}...")
    assert len(chunks) >= 4, "見出し3つのうち1つが機械分割で増えるので4つ以上のはず"
    assert chunks[0].startswith("## 背景"), "チャンク先頭に見出し行が残っているはず"
    print("  OK: 見出し単位で分割され、長い見出し配下だけ機械分割される")


# ---------------------------------------------------------------------------
# 2. memory_store.py の確認
# ---------------------------------------------------------------------------

# 意味検索の効果を確認するため、わざと話題の異なる発言をまとめて登録する
# (test_search.pyと同じ狙い。route絞り込みの確認も兼ねて2つのrouteに分ける)
SAMPLES = [
    (TEST_ROUTE, "user", "毎週火曜日は定休日にしています。"),
    (TEST_ROUTE, "assistant", "承知しました。火曜定休として記録しますね。"),
    (TEST_ROUTE, "user", "夕食は何を作ろうか迷っている。冷蔵庫に鶏肉があったはず。"),
    (TEST_ROUTE_CODE, "user", "前に書いたchunker.pyのオーバーラップ処理を直したい。"),
    (TEST_ROUTE_CODE, "assistant", "chunker.pyの_split_by_length関数を修正しました。"),
]


def test_memory_store() -> None:
    _section("2. memory_store.py の確認")

    print(f"接続先DB: {memory_store.DB_PATH}")
    print(f"Embeddingモデル: {memory_store.EMBED_MODEL}")

    # --- embed() の所要時間 ---
    t0 = time.perf_counter()
    vec = memory_store.embed("計測用のテキストです")
    t1 = time.perf_counter()
    print(f"[embed] 次元数={len(vec)} 所要時間={t1 - t0:.3f}秒")

    # --- append_turn() の登録と所要時間 ---
    print("\n[append_turn] テストデータを登録")
    total_registered = 0
    t0 = time.perf_counter()
    for route, role, text in SAMPLES:
        n = memory_store.append_turn(chat_id="test-chat", role=role, route=route, text=text)
        total_registered += n
    t1 = time.perf_counter()
    print(f"  {total_registered}件登録 / 所要時間={t1 - t0:.3f}秒"
          f"(1件あたり約{(t1 - t0) / max(total_registered, 1):.3f}秒)")

    # --- retrieve() の意味検索確認 ---
    _section("2-1. 意味検索(表記ゆれでもヒットするか)")
    t0 = time.perf_counter()
    hits = memory_store.retrieve("休みの曜日を知りたい", limit=3)
    t1 = time.perf_counter()
    print(f"クエリ: 「休みの曜日を知りたい」(所要時間={t1 - t0:.3f}秒)")
    for h in hits:
        print(f"  distance={h['_distance']:.4f} route={h['route']:<20} content={h['content']}")
    top = hits[0]
    print(f"\n最上位ヒット: {top['content']!r}")
    assert "定休日" in top["content"], (
        "「休みの曜日」→「定休日」の意味検索がヒットしていない。"
        "Embeddingモデルや接頭辞の設定を見直すこと。"
    )
    print("  OK: 表記が違っても意味的に近い発言がトップに来ている")

    # --- retrieve() のroute絞り込み確認 ---
    _section("2-2. route絞り込み(CODEルート想定)")
    hits_code = memory_store.retrieve("さっきのスクリプトの不具合を直して", limit=3, route=TEST_ROUTE_CODE)
    print(f"route='{TEST_ROUTE_CODE}'で絞り込んだ結果: {len(hits_code)}件")
    for h in hits_code:
        print(f"  distance={h['_distance']:.4f} route={h['route']} content={h['content']}")
    assert all(h["route"] == TEST_ROUTE_CODE for h in hits_code), "絞り込みが効いていない"
    print("  OK: route絞り込みが機能している")

    # --- format_context() の足切り確認 ---
    _section("2-3. format_context()の距離足切り")
    all_hits = memory_store.retrieve("休みの曜日を知りたい", limit=5)
    for threshold in (0.2, 0.45, 1.0):
        ctx = memory_store.format_context(all_hits, max_distance=threshold)
        n_used = ctx.count("- (") if ctx else 0
        print(f"  max_distance={threshold}: 採用{n_used}件 / 空文字={ctx == ''}")
    print("  OK: max_distanceを厳しくするほど採用件数が減ることを確認")


# ---------------------------------------------------------------------------
# 3. 後片付け: このスクリプトが登録したテストデータだけを削除する
# ---------------------------------------------------------------------------

def cleanup() -> None:
    _section("3. テストデータの後片付け")
    table = memory_store._table()
    before = table.count_rows()
    table.delete(f"route LIKE '{TEST_ROUTE}%'")
    after = table.count_rows()
    print(f"route LIKE '{TEST_ROUTE}%' を削除: {before}件 → {after}件"
          f"(削除数: {before - after})")
    print("※ 5日目からの既存データ(route='TEST')には触れていない")


if __name__ == "__main__":
    try:
        test_chunker()
        test_memory_store()
        print("\nすべてのテストが正常に完了しました。")
    finally:
        # assert失敗などで例外が飛んでも、登録したテストデータは必ず消す
        cleanup()
