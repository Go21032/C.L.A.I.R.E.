"""
measure_memory_overhead.py
----------------------------
6日目ノート⑧の残タスク
「レイテンシ実測:同一質問をmemory_enabled ON/OFFで各3回実行し、平均応答時間を比較する」
「`nvidia-smi`でEmbedding実行中もVRAM使用量が跳ねない(CPU固定が効いている)ことを確認する」
の2つを、手動計測(ストップウォッチ+目視でのnvidia-smi確認)ではなく自動化するスクリプト。

smoke_test_pipe.py と同じ方式で support_ai_auto_pipe.Pipe を直接呼び出し、
monitor_ollama.py の GpuSampler をそのまま流用してPipe呼び出し中のVRAM/GPU使用率を
一定間隔でサンプリングする(monitor_ollama.py が単体モデルの検証で使っているのと
同じ仕組みを、Pipe全体の呼び出しに対して適用する形)。

比較対象はDEEPルート固定(同じ質問・同じモデル=gemma4:26bで、
memory_enabledだけを切り替える)。ルートを固定する理由:
  route(=呼び出すモデル)が違うと生成時間そのものが大きく変わってしまい、
  「記憶レイヤーの検索・書き戻しによる上乗せ」を正しく切り分けられなくなるため。
  6日目ノート⑧の判断基準(上乗せが1秒以内か)は、同一モデル・同一質問での
  ON/OFF差分でしか意味を持たない。

実行方法:
    python measure_memory_overhead.py [--repeat 3]

前提: Ollamaサーバー起動済み、D:\\sapo_ai\\rag_memory (外付けHDD)が接続済みであること。
nvidia-smiがPATHに通っていること(monitor_ollama.pyと同じ前提)。
本番の記憶DBに一時的にテストデータを書き込むため、実行後に自動で後片付けする。
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "openwebui_pipe"))
sys.path.insert(0, str(SCRIPT_DIR))

from support_ai_auto_pipe import Pipe, memory_store  # noqa: E402
from monitor_ollama import GpuSampler  # noqa: E402

RESULTS_DIR = SCRIPT_DIR / "results" / "Memory Latency"

# DEEPルートに確実に分類される、記憶に依存しない相談文(ここ自体は前回の記憶を必要としない)。
PROMPT = "3ヶ月後の資格試験に向けて、平日2時間・休日4時間の学習計画を立ててください。"
CHAT_TAG = "latency-deep"

# nvidia-smiのポーリング間隔。embedding呼び出し自体は0.05〜0.3秒程度(③④の実測)と短いため、
# 間隔を短くするほど「跳ねた瞬間」を捉えやすくなる(monitor_ollama.pyの既定1.0秒より短くする)。
GPU_POLL_INTERVAL = 0.2


def make_body(text: str, chat_id: str) -> dict:
    return {"chat_id": chat_id, "messages": [{"role": "user", "content": text}]}


def run_once(pipe: Pipe, memory_enabled: bool, chat_id: str) -> dict:
    pipe.valves.memory_enabled = memory_enabled
    sampler = GpuSampler(interval=GPU_POLL_INTERVAL)
    sampler.start()
    t0 = time.perf_counter()
    reply = pipe.pipe(make_body(PROMPT, chat_id))
    elapsed = time.perf_counter() - t0
    sampler.stop()

    samples = sampler.samples
    vram_peak = max((s["vram_used_mib"] for s in samples), default=None)
    vram_avg = (
        round(sum(s["vram_used_mib"] for s in samples) / len(samples), 1) if samples else None
    )
    gpu_peak = max((s["gpu_util_pct"] for s in samples), default=None)

    return {
        "elapsed_s": round(elapsed, 3),
        "vram_peak_mib": vram_peak,
        "vram_avg_mib": vram_avg,
        "gpu_util_peak_pct": gpu_peak,
        "sample_count": len(samples),
        "reply_head": reply[:80].replace("\n", " "),
    }


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3, help="ON/OFFそれぞれの試行回数")
    args = parser.parse_args()

    if memory_store is None:
        print("[error] memory_storeの読み込みに失敗しています。D:\\sapo_ai\\rag_memory が接続されているか確認してください。")
        sys.exit(1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    pipe = Pipe()

    conditions = [
        ("memory_enabled=False(DEEP)", False),
        ("memory_enabled=True(DEEP・検索あり)", True),
    ]

    rows_before_all = memory_store.count_rows()

    # ウォームアップ: gemma4:26bが未ロード(直前に別モデルが動いていた)状態から測定を
    # 始めると、1回目だけモデルロード時間(数十秒)が乗って結果がロード時間で支配されてしまう
    # (実機1回目の試行で run1=66s, run2=46s, run3=33s と大きくばらついたのがこれ)。
    # ④の分析どおり「モデル常駐中」の数値を見たいので、本計測の前に1回捨て呼びしてロードを終わらせる。
    print("[info] ウォームアップ実行中(モデルロード時間を計測対象から除外するため)...")
    warmup_chat_id = f"{CHAT_TAG}-warmup-{int(time.time() * 1000)}"
    run_once(pipe, False, warmup_chat_id)
    table = memory_store._table()
    table.delete(f"content = '{PROMPT}'")
    table.delete("source LIKE 'chat:latency-deep-warmup-%'")
    print("[info] ウォームアップ完了。本計測を開始します。\n")

    results: dict[str, list[dict]] = {}
    for label, mem_flag in conditions:
        print("=" * 70)
        print(f"[{label}] を{args.repeat}回実行")
        runs = []
        for i in range(1, args.repeat + 1):
            chat_id = f"{CHAT_TAG}-{'on' if mem_flag else 'off'}-{i}-{int(time.time() * 1000)}"
            r = run_once(pipe, mem_flag, chat_id)
            print(
                f"  run{i}: {r['elapsed_s']}s "
                f"VRAM peak={r['vram_peak_mib']}MiB avg={r['vram_avg_mib']}MiB "
                f"gpu_util_peak={r['gpu_util_peak_pct']}% (samples={r['sample_count']})"
            )
            runs.append(r)
        results[label] = runs

    off_avg = sum(r["elapsed_s"] for r in results[conditions[0][0]]) / args.repeat
    on_avg = sum(r["elapsed_s"] for r in results[conditions[1][0]]) / args.repeat
    overhead = on_avg - off_avg

    off_vram_peak = max(r["vram_peak_mib"] for r in results[conditions[0][0]] if r["vram_peak_mib"] is not None)
    on_vram_peak = max(r["vram_peak_mib"] for r in results[conditions[1][0]] if r["vram_peak_mib"] is not None)
    vram_jump = on_vram_peak - off_vram_peak

    print("\n" + "=" * 70)
    print("集計結果")
    print("=" * 70)
    print(f"OFF平均: {off_avg:.3f}s / ON平均: {on_avg:.3f}s / 上乗せ: {overhead:+.3f}s")
    print(f"OFF側VRAMピーク: {off_vram_peak}MiB / ON側VRAMピーク: {on_vram_peak}MiB / 差: {vram_jump:+.1f}MiB")

    judge_latency = "OK(1秒以内)" if overhead <= 1.0 else "NG(1秒超。⑨の改善策(モデル常駐化/top_k削減/非同期化)を検討)"
    # CPU固定(OLLAMA_NUM_GPU=0)が効いていれば、embeddingの有無でVRAMピークは
    # 大きくは変わらないはず。500MiB以上跳ねていたら「CPU固定が効いていない疑い」とする
    # (目安値。embedding呼び出しは短いのでポーリング間隔によっては見逃すこともある点に注意)。
    judge_vram = "OK(VRAM跳ね上がりなし=CPU固定が効いている)" if vram_jump < 500 else "NG(500MiB以上の増加。OLLAMA_NUM_GPU=0が効いていない疑い)"
    print(f"\n判定(レイテンシ上乗せ): {judge_latency}")
    print(f"判定(VRAM): {judge_vram}")

    # CSV保存
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"memory_overhead_{ts}.csv"
    fieldnames = ["condition", "run", "elapsed_s", "vram_peak_mib", "vram_avg_mib", "gpu_util_peak_pct", "sample_count"]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for label, runs in results.items():
            for i, r in enumerate(runs, start=1):
                writer.writerow({
                    "condition": label,
                    "run": i,
                    "elapsed_s": r["elapsed_s"],
                    "vram_peak_mib": r["vram_peak_mib"],
                    "vram_avg_mib": r["vram_avg_mib"],
                    "gpu_util_peak_pct": r["gpu_util_peak_pct"],
                    "sample_count": r["sample_count"],
                })
        writer.writerow({
            "condition": "OFF平均",
            "run": "-",
            "elapsed_s": round(off_avg, 3),
            "vram_peak_mib": off_vram_peak,
            "vram_avg_mib": "",
            "gpu_util_peak_pct": "",
            "sample_count": "",
        })
        writer.writerow({
            "condition": "ON平均",
            "run": "-",
            "elapsed_s": round(on_avg, 3),
            "vram_peak_mib": on_vram_peak,
            "vram_avg_mib": "",
            "gpu_util_peak_pct": "",
            "sample_count": "",
        })
        writer.writerow({
            "condition": "上乗せ(ON-OFF)",
            "run": "-",
            "elapsed_s": round(overhead, 3),
            "vram_peak_mib": vram_jump,
            "vram_avg_mib": "",
            "gpu_util_peak_pct": "",
            "sample_count": "",
        })
    print(f"\n[info] CSVを保存: {out_path}")

    # 後片付け: ON側の3回で本番DBに書き込まれたテストデータを削除する。
    table = memory_store._table()
    before = table.count_rows()
    table.delete(f"content = '{PROMPT}'")
    # ON側の応答(assistant発言)も書き戻されているため、
    # このスクリプト由来のchat_id接頭辞(source LIKE 'chat:latency-deep-%')でまとめて削除する。
    table.delete("source LIKE 'chat:latency-deep-%'")
    after = table.count_rows()
    print(f"\n[cleanup] このスクリプトが書き込んだテストデータを削除: {before}件 -> {after}件(削除数: {before - after})")
    rows_after_all = memory_store.count_rows()
    print(f"[cleanup] 実行前後の総行数: {rows_before_all}件 -> {rows_after_all}件(復元できていれば一致するはず)")


if __name__ == "__main__":
    main()
