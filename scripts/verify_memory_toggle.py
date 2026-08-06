"""
verify_memory_toggle.py
-------------------------
6日目ノート⑧の残タスク
「`Valves`の`memory_enabled`をOFFにすると、5日目以前と同じ挙動に戻ることを確認する」
を、Open WebUIの画面操作なしで自動検証するスクリプト。

smoke_test_pipe.py と同様に openwebui_pipe/support_ai_auto_pipe.py の`Pipe`クラスを
直接呼び出す(Open WebUIは未導入のため経由できない)。実際のOllama・実際の記憶DB
(D:\\sapo_ai\\rag_memory)を使う。

検証シナリオ:
  チャットA: 「合言葉は『ペンギン38号』です。覚えておいてください。」(事実を伝える)
  チャットB(別chat_id): 「さっき伝えた合言葉は何でしたか?」(想起質問)

  ⚠️ 試行錯誤の記録(実機で2回作り直した):
  1. 当初は⑧-1と同じ「私は火曜日と木曜日が休みです」→「私の休みは何曜日?」という
     個人的事実の想起パターンで自動判定していたが、memory_enabled=**False**
     (記憶を一切参照していない)にもかかわらず、FASTモデル(gpt-oss:20b)が
     一般的な勤務パターンの例として偶然「火曜」「木曜」という単語を生成し、
     単純な部分一致判定が誤って「想起できた」と判定する事故が発生した
     (LLMの非決定的な生成が原因。よくある曜日はモデルが**何も覚えていなくても**
     それらしく言い当ててしまうため、判定材料として不適切だった)。
  2. 「合言葉は『ペンギン38号』です」のような明示的パスワード形式に変えたところ、
     今度はPhi-4-mini(ルーター)が「意図不明」と判断してCLARIFYに分類してしまい、
     CLARIFYはそもそも検索・書き戻しの対象外(⑤の表)のため、memory_enabled=True
     でも行数が増えず判定不能になった。
  3. 最終的に、⑧-1で実績のある「私は～です」という自然な事実申告の文型は維持しつつ、
     内容だけをモデルが記憶なしに偶然言い当てる可能性が低い固有名詞(ペットの名前)に
     差し替えた。文型を自然な事実申告のままにすることでFAST分類を維持しつつ、
     「ナポレオン」という一般的でない固有名詞にすることで偶然一致を防いでいる。

memory_enabled=True の場合(現状のC.L.A.I.R.E.):
  - チャットAの発言が記憶DBに書き戻される(行数が増える)
  - チャットBがチャットAの内容を検索して踏まえた返答になる(合言葉「ペンギン38号」を含む)

memory_enabled=False の場合(5日目以前と同じ挙動に戻ったと言える条件):
  - チャットAの発言を記憶DBに書き戻さない(Pipe呼び出し前後で行数が変化しない)
  - チャットBはチャットAの内容を検索しない(=何も参照材料がないので、
    推測不可能な合言葉を言い当てられない=「わかりません」の類の返答になる)
  - どちらの場合も、記憶レイヤーに関係なくPipe自体は例外なく応答を返す
    (④の完了条件「記憶レイヤーの障害が本体を止めない」の一種としても確認できる)

実行方法:
    python verify_memory_toggle.py

前提: Ollamaサーバー起動済み、D:\\sapo_ai\\rag_memory (外付けHDD)が接続済みであること。
本番の記憶DBに一時的にテストデータを書き込む(memory_enabled=True側)。
route='TEST_VERIFY_TOGGLE'で登録するため、実行後に自動で後片付け(delete)する。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "openwebui_pipe"))

from support_ai_auto_pipe import Pipe, memory_store  # noqa: E402

FACT_TEXT = "私が飼っている亀の名前はナポレオンです。"
RECALL_QUESTION = "私が飼っている亀の名前は何ですか?"

# 想起質問への返答にこのトークンが含まれていれば、記憶を参照できたとみなす。
# 「ナポレオン」という一般的でない固有名詞にすることで、モデルが記憶なしに
# 偶然この単語を生成する可能性をほぼ排除している
# (試行錯誤の経緯は上のdocstring参照。曜日名や「合言葉」形式では正しく判定できなかった)。
RECALL_KEYWORDS = ["ナポレオン"]


def make_body(text: str, chat_id: str) -> dict:
    return {"chat_id": chat_id, "messages": [{"role": "user", "content": text}]}


def run_scenario(pipe: Pipe, memory_enabled: bool, tag: str) -> dict:
    pipe.valves.memory_enabled = memory_enabled
    chat_a = f"verify-toggle-{tag}-A"
    chat_b = f"verify-toggle-{tag}-B"

    rows_before = memory_store.count_rows() if memory_store is not None else None

    reply_a = pipe.pipe(make_body(FACT_TEXT, chat_a))
    time.sleep(0.5)  # append_turnのembedding呼び出しが確実に完了するのを待つ保険
    rows_after_a = memory_store.count_rows() if memory_store is not None else None

    reply_b = pipe.pipe(make_body(RECALL_QUESTION, chat_b))

    recalled = any(kw in reply_b for kw in RECALL_KEYWORDS)

    return {
        "tag": tag,
        "memory_enabled": memory_enabled,
        "rows_before": rows_before,
        "rows_after_a": rows_after_a,
        "rows_delta": (rows_after_a - rows_before) if None not in (rows_before, rows_after_a) else None,
        "reply_a": reply_a,
        "reply_b": reply_b,
        "recalled": recalled,
    }


def cleanup_test_rows() -> None:
    if memory_store is None:
        return
    table = memory_store._table()
    before = table.count_rows()
    table.delete("route = 'TEST_VERIFY_TOGGLE'")
    after = table.count_rows()
    print(f"\n[cleanup] route='TEST_VERIFY_TOGGLE' を削除: {before}件 -> {after}件(削除数: {before - after})")


def print_result(label: str, r: dict) -> None:
    print("=" * 70)
    print(f"[{label}] memory_enabled={r['memory_enabled']}")
    print(f"  チャットA応答(先頭120字): {r['reply_a'][:120]}")
    print(f"  記憶DB行数: {r['rows_before']} -> {r['rows_after_a']} (差分 {r['rows_delta']})")
    print(f"  チャットB応答(先頭200字): {r['reply_b'][:200]}")
    print(f"  『{'/'.join(RECALL_KEYWORDS)}』を含む(=想起できた): {r['recalled']}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if memory_store is None:
        print("[error] memory_storeの読み込みに失敗しています。D:\\sapo_ai\\rag_memory が接続されているか確認してください。")
        sys.exit(1)

    pipe = Pipe()

    # 本番チャットAは"FAST"(事実申告)に分類されAPPEND_ROUTESに含まれる想定。
    # メモリ書き戻しは chat_a 側のみで発生するので、判定は rows_after_a との差分で行う。
    off_result = run_scenario(pipe, memory_enabled=False, tag="off")
    on_result = run_scenario(pipe, memory_enabled=True, tag="on")

    # ただしrun_scenario内のappend_turnはFACT_TEXTを"route=FAST"などの実route名で
    # 登録してしまうため、後片付け対象を追えるようにテスト用routeへ差し替えたい場合は
    # memory_store.append_turnを直接使う運用に変更する必要がある。今回は実際のPipe経路を
    # そのまま通すことを優先し、後片付けは日付とcontentで絞り込む。
    print_result("OFF (5日目以前相当の期待値)", off_result)
    print_result("ON (現状のC.L.A.I.R.E.)", on_result)

    print("\n" + "=" * 70)
    print("判定")
    print("=" * 70)
    off_ok = (off_result["rows_delta"] == 0) and (not off_result["recalled"])
    on_ok = (on_result["rows_delta"] is not None and on_result["rows_delta"] > 0) and on_result["recalled"]
    print(f"OFF時に『書き戻しなし・想起なし』(5日目以前と同じ挙動)か: {'OK' if off_ok else 'NG'}")
    print(f"ON時に『書き戻しあり・想起あり』(6日目の狙いどおり)か  : {'OK' if on_ok else 'NG'}")
    if off_ok and on_ok:
        print("\n[PASS] memory_enabledのON/OFFで挙動が期待どおり切り替わっていることを確認しました。")
    else:
        print("\n[FAIL] 期待どおりの挙動になっていません。上記の詳細ログを確認してください。")

    # ON側で本番DBに書き込んだテストデータ(FACT_TEXT本文を含む行)を後片付けする。
    if memory_store is not None:
        table = memory_store._table()
        before = table.count_rows()
        table.delete(f"content = '{FACT_TEXT}'")
        after = table.count_rows()
        print(f"\n[cleanup] テストで登録した発言(content='{FACT_TEXT}')を削除: {before}件 -> {after}件(削除数: {before - after})")
        # 応答文(アシスタント発言)側もappend_turnで登録されているため、
        # chat:verify-toggle-on-A / chat:verify-toggle-on-B を出所とする行も合わせて削除する。
        before2 = table.count_rows()
        table.delete("source = 'chat:verify-toggle-on-A' OR source = 'chat:verify-toggle-on-B'")
        after2 = table.count_rows()
        print(f"[cleanup] テストチャット由来の行(source=chat:verify-toggle-on-*)を削除: {before2}件 -> {after2}件(削除数: {before2 - after2})")


if __name__ == "__main__":
    main()
