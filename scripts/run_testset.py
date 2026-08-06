"""
run_testset.py
----------------
prompts/router_classification/testset_v1.md のFAST/DEEP/CODE/CLARIFY
テストセットをrouter.classify_route()に実際に通し、分類精度を記録する使い捨てスクリプト。

生成応答(gpt-oss/gemma4/devstral側の実際の回答)までは取得せず、
「route判定が期待通りか」だけを見る(Task5 Step4: 分類正答率の算出)。
CODE_TRIGGERSに一致する質問はPhi-4-miniを呼ばずルールベースで即決するため、
実際にOllama呼び出しが発生するのはFAST/DEEP/CLARIFY相当の質問のみ。

結果は monitor_ollama.py と同じ方式でCSVに自動保存する:
  - results/Router Classification/<label>_<timestamp>_detail.csv … 1問1行の詳細ログ
  - results/Router Classification/summary.csv … 実行ごとの正答率サマリを1行追記(実行履歴の比較用)

使い方:
    python run_testset.py
    python run_testset.py --label system_prompt_v2
    python run_testset.py --system-prompt prompts/router_classification/system_prompt_v2.txt
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

RESULTS_DIR = SCRIPT_DIR / "results" / "Router Classification"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"
SUMMARY_HEADER = [
    "timestamp",
    "label",
    "system_prompt",
    "total",
    "correct",
    "accuracy_pct",
    "fast_correct",
    "fast_total",
    "deep_correct",
    "deep_total",
    "code_correct",
    "code_total",
    "clarify_correct",
    "clarify_total",
    "detail_csv",
]
DETAIL_HEADER = ["id", "question", "expected", "actual", "ok", "elapsed_s"]

import router  # noqa: E402
from router import call_phi4, classify_route  # noqa: E402

# testset_v1.md の表から転記(#, 質問, 期待ルート)。
# X5は複合タスクの優先度ルールにより期待ルートをCODEとしている(testset_v1.md参照)。
TESTSET: list[tuple[str, str, str]] = [
    ("F1", "今日の東京の天気を教えて", "FAST"),
    ("F2", "消費税10%のとき、1980円の商品の税込価格は?", "FAST"),
    ("F3", "おすすめのラーメン屋のジャンルを3つ挙げて", "FAST"),
    ("F4", "Pythonのlen()関数って何をするものだっけ?", "FAST"),
    ("F5", "最近疲れてるんだけど、なんか一言励まして", "FAST"),
    ("D1", "3ヶ月後の資格試験に向けて、平日2時間・休日4時間の学習計画を立てて", "DEEP"),
    ("D2", "来月の家族旅行のスケジュールを、移動時間と子どもの体力を考慮して組んで", "DEEP"),
    ("D3", "今の投資ポートフォリオの偏りを踏まえて、リバランス方針を考えて", "DEEP"),
    ("D4", "新しい業務プロセスの提案資料の構成を、経営層向けに練り直して", "DEEP"),
    ("D5", "副業と本業の両立が難しくなってきた。優先順位の付け方を一緒に整理して", "DEEP"),
    ("C1", "このPythonの関数、TypeErrorが出るんだけどデバッグして", "CODE"),
    ("C2", "FastAPIでファイルアップロードを受け付けるエンドポイントを実装して", "CODE"),
    ("C3", "以下のコードをレビューして、リファクタリングの提案をして", "CODE"),
    ("C4", "この正規表現、意図通りにマッチしない原因を教えて", "CODE"),
    ("C5", "SQLiteのテーブル定義をSQLAlchemyのモデルに書き換えて", "CODE"),
    ("X1", "あれ、どうすればいい?", "CLARIFY"),
    ("X2", "ちょっと相談があるんだけど", "CLARIFY"),
    ("X3", "これ", "CLARIFY"),
    ("X4", "このコードのアルゴリズムを勉強計画に組み込みたい", "CLARIFY"),
    ("X5", "バグ修正しつつ開発スケジュールも整理して", "CODE"),
]


def main() -> None:
    import argparse

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        default=None,
        help="結果ファイル名・summary.csvに記録するラベル(未指定ならsystem_promptのファイル名を使用)",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help=(
            "分類に使うシステムプロンプトファイルを差し替える"
            "(未指定ならrouter.pyのデフォルト=SYSTEM_PROMPT_PATHをそのまま使う)"
        ),
    )
    args = parser.parse_args()

    if args.system_prompt:
        prompt_path = Path(args.system_prompt)
        if not prompt_path.is_absolute():
            candidates = [Path(args.system_prompt), SCRIPT_DIR / args.system_prompt]
            prompt_path = next((p for p in candidates if p.exists()), prompt_path)
        if not prompt_path.exists():
            parser.error(f"--system-promptで指定されたファイルが見つかりません: {args.system_prompt}")
        router.SYSTEM_PROMPT_PATH = prompt_path

    system_prompt_name = router.SYSTEM_PROMPT_PATH.name
    label = args.label or system_prompt_name.replace(".txt", "")

    results = []
    for idx, question, expected in TESTSET:
        t0 = time.time()
        actual = classify_route(question, call_phi4)
        elapsed = time.time() - t0
        ok = actual == expected
        results.append((idx, question, expected, actual, ok, elapsed))
        mark = "OK" if ok else "NG"
        print(f"[{mark}] {idx:>3} expected={expected:<8} actual={actual:<8} ({elapsed:.1f}s)  {question}")

    total = len(results)
    correct = sum(1 for r in results if r[4])
    print(f"\n===== 全体正答率: {correct}/{total} ({correct/total*100:.1f}%) =====")

    by_category: dict[str, list[tuple]] = {}
    for r in results:
        by_category.setdefault(r[2], []).append(r)
    for cat, rows in by_category.items():
        c = sum(1 for r in rows if r[4])
        print(f"  {cat}: {c}/{len(rows)} ({c/len(rows)*100:.1f}%)")

    ng_rows = [r for r in results if not r[4]]
    if ng_rows:
        print("\n===== 誤判定一覧 =====")
        for idx, question, expected, actual, ok, elapsed in ng_rows:
            print(f"  {idx}: 期待={expected} 実際={actual} 質問=「{question}」")

    # ---- ここからCSV保存(monitor_ollama.pyと同じ方式) ----
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    import re

    safe_label = re.sub(r"[^\w\-]+", "_", label) or "run"
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    detail_path = RESULTS_DIR / f"{safe_label}_{ts_str}_detail.csv"
    with open(detail_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_HEADER)
        writer.writeheader()
        for idx, question, expected, actual, ok, elapsed in results:
            writer.writerow(
                {
                    "id": idx,
                    "question": question,
                    "expected": expected,
                    "actual": actual,
                    "ok": ok,
                    "elapsed_s": round(elapsed, 2),
                }
            )

    def cat_counts(cat: str) -> tuple[int, int]:
        rows = by_category.get(cat, [])
        return sum(1 for r in rows if r[4]), len(rows)

    fast_c, fast_t = cat_counts("FAST")
    deep_c, deep_t = cat_counts("DEEP")
    code_c, code_t = cat_counts("CODE")
    clarify_c, clarify_t = cat_counts("CLARIFY")

    summary_row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "system_prompt": system_prompt_name,
        "total": total,
        "correct": correct,
        "accuracy_pct": round(correct / total * 100, 1),
        "fast_correct": fast_c,
        "fast_total": fast_t,
        "deep_correct": deep_c,
        "deep_total": deep_t,
        "code_correct": code_c,
        "code_total": code_t,
        "clarify_correct": clarify_c,
        "clarify_total": clarify_t,
        "detail_csv": detail_path.name,
    }
    write_header = not SUMMARY_CSV.exists()
    with open(SUMMARY_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow(summary_row)

    print(f"\n[info] 詳細ログを保存: {detail_path}")
    print(f"[info] サマリを追記: {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
