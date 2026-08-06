"""
verify_conversation_swap.py
----------------------------
4日目ノートの残タスク「会話継続時に不要なモデルスワップが起きないことを確認する」を
実機(実際のOllama・実際のFAST/DEEP/CODEモデル)で検証するスクリプト。

これまで`RouterSession`のロジック自体はフェイク関数でユニットテスト済みだったが、
「実際にモデルを切り替えながらの通し確認」はまだ行っていなかった。
本スクリプトは1つの会話スレッドを模したターン列を1つのRouterSessionインスタンスに
順番に投げ、各ターンで
  - Phi-4-mini(分類LLM)が実際に呼ばれたか(session再利用でスキップされたか)
  - `ollama ps`で見た実行中モデルが想定通りに切り替わった/切り替わらなかったか
を記録する。

実行方法:
    python verify_conversation_swap.py

前提: Ollamaサーバーが起動していること(gpt-oss:20b, gemma4:26b, devstral-small-2:24b,
phi4-mini-cpu:latest がpull済みであること)。
"""

from __future__ import annotations

import time

import router
from ollama_client import generate, list_running_models

# (説明, 質問文, session_id) のターン列。
# シナリオ:
#   T1: DEEPな質問(新規スレッド thread-A) -> Phi4分類が走り、gemma4に初回スワップするはず
#   T2: thread-Aの続き(文脈依存・単体では分類しづらい文)
#       -> セッションが直近routeを保持しているのでPhi4を呼ばずDEEP継続、モデルスワップも起きないはず
#   T3: thread-Aの続きその2 -> 同上
#   T4: thread-Aの続きでCODEトリガー語("実装して")を含む文
#       -> 優先度ルール(CODE>DEEP>FAST)によりルールベースでCODEへ即上書き。
#          Phi4は呼ばれず(ルールベースがLLMをスキップする)、モデルはdevstralへスワップするはず
#   T5: 別スレッド(thread-B、新規)でFASTな質問 -> thread-Aの状態と無関係に新規分類・gpt-ossへスワップ
TURNS = [
    (
        "T1: 新規スレッドでDEEP相談",
        "thread-A",
        "3ヶ月後の資格試験に向けて、平日2時間・休日4時間の学習計画を立てて",
    ),
    (
        "T2: 同スレッドの文脈依存の続き(単体では分類しづらい)",
        "thread-A",
        "土日はもう少し軽めにしてほしい",
    ),
    (
        "T3: 同スレッドのさらに続き",
        "thread-A",
        "あと、直前1週間は復習に切り替える形にして",
    ),
    (
        "T4: 同スレッドでCODEトリガー語を含む複合タスク",
        "thread-A",
        "ついでにこの前渡したスクリプトのバグも直して実装しといて",
    ),
    (
        "T5: 別スレッドでFAST質問(thread-Aとは無関係)",
        "thread-B",
        "今日の東京の天気を教えて",
    ),
]


def main() -> None:
    session = router.RouterSession()
    calls_made: list[str] = []

    def counting_call_phi4(system_prompt: str, user_text: str) -> str:
        calls_made.append(user_text)
        return router.call_phi4(system_prompt, user_text)

    print("=" * 70)
    print("会話継続時のモデルスワップ実機検証")
    print("=" * 70)

    for label, session_id, text in TURNS:
        before_running = list_running_models()
        before_calls = len(calls_made)

        t0 = time.perf_counter()
        route = session.get_route(session_id, text, counting_call_phi4)
        classify_elapsed = time.perf_counter() - t0

        llm_called = len(calls_made) > before_calls
        target_model = router.ROUTE_MODEL_MAP[route]

        t1 = time.perf_counter()
        gen_elapsed = None
        if route != "CLARIFY":
            router.ensure_model_ready(route)
            # 実運用同様、実際に対象モデルへ生成を投げてロードを確定させる
            # (ensure_model_readyは「他モデルの停止」だけを担当し、対象モデル自体の
            #  ロードはgenerate()呼び出し時にOllamaが行うため)。
            t2 = time.perf_counter()
            generate(model=target_model, prompt=text, timeout=120.0)
            gen_elapsed = time.perf_counter() - t2
        ensure_elapsed = time.perf_counter() - t1

        after_running = list_running_models()
        swapped = set(before_running) != set(after_running)

        print(f"\n[{label}]")
        print(f"  session_id       : {session_id}")
        print(f"  質問             : {text}")
        print(f"  判定route        : {route} (分類{classify_elapsed:.2f}s)")
        print(f"  Phi4呼び出し     : {'あり' if llm_called else 'なし(セッション/ルールベースで確定)'}")
        print(f"  対象モデル       : {target_model}")
        print(f"  ensure_model_ready+生成: {ensure_elapsed:.2f}s(うち生成{gen_elapsed:.2f}s)" if gen_elapsed is not None else f"  ensure_model_ready: {ensure_elapsed:.2f}s")
        print(f"  実行中モデル(前) : {before_running}")
        print(f"  実行中モデル(後) : {after_running}")
        print(f"  モデルスワップ   : {'発生' if swapped else 'なし'}")

    print("\n" + "=" * 70)
    print("検証終了。上記ログでT2/T3がPhi4呼び出しなし・モデルスワップなしに"
          "なっていればセッション保持ロジックは実機でも機能している。")
    print("=" * 70)


if __name__ == "__main__":
    main()
