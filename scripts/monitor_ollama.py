"""
monitor_ollama.py
------------------
Ollamaモデルの推論を実行しながら、GPU使用率・VRAM使用量(nvidia-smi)を
1秒間隔で自動収集し、`ollama run --verbose` の統計と合わせて
CSVに自動記録するツール。

== 通常モード(単体モデルの動作検証) ==
使い方:
    python monitor_ollama.py --model gemma4:26b --prompt "簡単な自己紹介をして" --label "Gemma4-初回"

    長文プロンプト(実コード投入テストなど)はファイルから読み込める(--promptより優先):
    python monitor_ollama.py --model devstral-small-2:24b --prompt-file prompts/devstral_typingquest_1_review.txt \
        --label "Devstral_実コード_レビュー" --repeat 3

実行すると:
  1. 別スレッドで `nvidia-smi --query-gpu=... -l 1` 相当のポーリングを開始
     (実際は subprocess.run を1秒ごとに呼び出す方式。Windows/Linux両対応)
  2. `ollama run <model> --verbose "<prompt>"` を実行し、標準出力を取得
  3. 実行終了後、収集したGPUサンプルから ピーク値/平均値 を算出
  4. verbose出力から total/load/prompt eval/eval の各統計をパース
  5. results/Functional Testing/summary.csv に1行追記(モデルごとの比較がしやすい形)
  6. results/Functional Testing/<label>_<timestamp>_samples.csv に生データ(1秒ごとのVRAM/GPU-Util)を保存

== スワップ計測モード(モデル切り替え検証) ==
使い方:
    python monitor_ollama.py --swap-models "gemma4:26b,gpt-oss:20b,devstral-small-2:24b,gemma4:26b" \
        --prompt "簡単な自己紹介をして" --label "スワップ検証" --cycles 1

`--swap-models` にカンマ区切りでモデルを指定すると自動的にスワップ計測モードになり
(`--model`は使わない)、リストの先頭モデルをロードした後、
`ollama stop <前モデル>` → VRAM安定待ち → `ollama run <次モデル> --verbose` を
リストの並び通りに繰り返す。`--cycles`でリスト全体を複数回繰り返せるので、
5〜10回連続切り替えのリーク確認にも使える。
結果は results/Swap Verification/<label>_<timestamp>_swap.csv に1 transitionごと1行で保存される。

依存: 標準ライブラリのみ(subprocess, threading, csv, re, argparse)。
前提: nvidia-smi と ollama が PATH に通っていること。
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = SCRIPT_DIR / "results"
FUNCTIONAL_DIR = RESULTS_DIR / "Functional Testing"  # 単体モデルの動作検証結果
SWAP_DIR = RESULTS_DIR / "Swap Verification"  # モデル切り替え(スワップ)検証結果
SUMMARY_CSV = FUNCTIONAL_DIR / "summary.csv"

SWAP_HEADER = [
    "timestamp",
    "label",
    "cycle",
    "step",
    "from_model",
    "to_model",
    "vram_before_stop_mib",
    "vram_after_stop_mib",
    "vram_released_mib",
    "stop_duration_s",
    "load_duration_s",
    "total_duration_s",
    "swap_total_wait_s",
    "vram_peak_during_run_mib",
    "vram_avg_during_run_mib",
    "gpu_util_peak_pct",
    "gpu_util_avg_pct",
    "eval_rate_tps",
    "note",
]

SUMMARY_HEADER = [
    "timestamp",
    "label",
    "model",
    "prompt",
    "vram_used_peak_mib",
    "vram_used_avg_mib",
    "vram_total_mib",
    "gpu_util_peak_pct",
    "gpu_util_avg_pct",
    "sample_count",
    "total_duration_s",
    "load_duration_s",
    "prompt_eval_count",
    "prompt_eval_duration_s",
    "prompt_eval_rate_tps",
    "eval_count",
    "eval_duration_s",
    "eval_rate_tps",
    "ttft_ms",
]

VERBOSE_PATTERNS = {
    "total_duration_s": r"total duration:\s*(\S+)",
    "load_duration_s": r"load duration:\s*(\S+)",
    "prompt_eval_count": r"prompt eval count:\s*(\d+)",
    "prompt_eval_duration_s": r"prompt eval duration:\s*(\S+)",
    "prompt_eval_rate_tps": r"prompt eval rate:\s*([\d.]+)",
    "eval_count": r"(?<!prompt )\beval count:\s*(\d+)",
    "eval_duration_s": r"(?<!prompt )\beval duration:\s*(\S+)",
    "eval_rate_tps": r"(?<!prompt )\beval rate:\s*([\d.]+)",
}

# ollama --verboseの時間表記(Goのtime.Duration文字列)を秒に変換する。
# 例: "225.4458ms" / "634.358ms" / "52.8766542s" / "1m0.21564s" / "1m37.0748739s" / "1h2m3.4s"
# 1分を超えると "1m37.07s" のように分+秒の複合表記になり、単発の([\d.]+)(m?s)では
# マッチできず値が欠損する不具合があったため、単位ごとに分解して合算する方式に変更。
_DURATION_TOKEN_RE = re.compile(r"([\d.]+)(ms|µs|us|h|m|s)")


def parse_go_duration(text: str) -> float | None:
    if not text:
        return None
    matches = _DURATION_TOKEN_RE.findall(text)
    if not matches:
        return None
    total = 0.0
    unit_seconds = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "µs": 1e-6, "us": 1e-6}
    for value, unit in matches:
        total += float(value) * unit_seconds[unit]
    return total


def get_gpu_snapshot(timeout: float = 5.0) -> dict | None:
    """nvidia-smiを1回だけ叩いてVRAM/GPU使用率のスナップショットを取る。"""
    query = "memory.used,memory.total,utilization.gpu"
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=True,
        ).stdout.strip()
        # 複数GPUがある場合は先頭行(GPU0)のみ採用
        first_line = out.splitlines()[0]
        mem_used, mem_total, util = [x.strip() for x in first_line.split(",")]
        return {
            "vram_used_mib": int(mem_used),
            "vram_total_mib": int(mem_total),
            "gpu_util_pct": int(util),
        }
    except Exception as e:  # nvidia-smi失敗時は呼び出し側でNoneとして扱う
        print(f"[warn] nvidia-smi snapshot failed: {e}")
        return None


class GpuSampler:
    """nvidia-smiを一定間隔でポーリングしてサンプルをためるスレッド。"""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.samples: list[dict] = []
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=self.interval * 2)

    def _run(self):
        while not self._stop_event.is_set():
            t0 = time.time()
            snap = get_gpu_snapshot(timeout=self.interval * 3)
            if snap is not None:
                self.samples.append(
                    {"t": datetime.now().isoformat(timespec="seconds"), **snap}
                )
            elapsed = time.time() - t0
            self._stop_event.wait(max(0.0, self.interval - elapsed))


def parse_verbose_output(text: str) -> dict:
    result: dict[str, float | int | None] = {k: None for k in VERBOSE_PATTERNS}
    for key, pattern in VERBOSE_PATTERNS.items():
        m = re.search(pattern, text)
        if not m:
            continue
        if key in ("prompt_eval_count", "eval_count"):
            result[key] = int(m.group(1))
        elif key.endswith("_rate_tps"):
            result[key] = float(m.group(1))
        else:
            result[key] = parse_go_duration(m.group(1))
    return result


def execute_ollama_run(model: str, prompt: str, interval: float) -> dict:
    """`ollama run --verbose`を実行しつつGPUをサンプリングし、統計をまとめて返す
    共通ヘルパー(通常モード・スワップモード両方から使う)。
    """
    sampler = GpuSampler(interval=interval)
    sampler.start()

    print(f"[info] running: ollama run {model} --verbose \"{prompt}\"")
    proc = subprocess.run(
        ["ollama", "run", model, "--verbose", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sampler.stop()

    combined_output = proc.stdout + "\n" + proc.stderr
    verbose_stats = parse_verbose_output(combined_output)

    samples = sampler.samples
    if samples:
        vram_peak = max(s["vram_used_mib"] for s in samples)
        vram_avg = sum(s["vram_used_mib"] for s in samples) / len(samples)
        vram_total = samples[0]["vram_total_mib"]
        util_peak = max(s["gpu_util_pct"] for s in samples)
        util_avg = sum(s["gpu_util_pct"] for s in samples) / len(samples)
    else:
        vram_peak = vram_avg = vram_total = util_peak = util_avg = None

    return {
        "combined_output": combined_output,
        "verbose_stats": verbose_stats,
        "samples": samples,
        "vram_peak": vram_peak,
        "vram_avg": vram_avg,
        "vram_total": vram_total,
        "util_peak": util_peak,
        "util_avg": util_avg,
    }


def run_and_monitor(model: str, prompt: str, label: str, interval: float) -> dict:
    FUNCTIONAL_DIR.mkdir(parents=True, exist_ok=True)

    run_result = execute_ollama_run(model, prompt, interval)
    combined_output = run_result["combined_output"]
    verbose_stats = run_result["verbose_stats"]
    samples = run_result["samples"]
    vram_peak = run_result["vram_peak"]
    vram_avg = run_result["vram_avg"]
    vram_total = run_result["vram_total"]
    util_peak = run_result["util_peak"]
    util_avg = run_result["util_avg"]

    load_d = verbose_stats.get("load_duration_s")
    prompt_eval_d = verbose_stats.get("prompt_eval_duration_s")
    ttft_ms = None
    if load_d is not None and prompt_eval_d is not None:
        ttft_ms = round((load_d + prompt_eval_d) * 1000, 1)

    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "model": model,
        "prompt": prompt,
        "vram_used_peak_mib": vram_peak,
        "vram_used_avg_mib": round(vram_avg, 1) if vram_avg is not None else None,
        "vram_total_mib": vram_total,
        "gpu_util_peak_pct": util_peak,
        "gpu_util_avg_pct": round(util_avg, 1) if util_avg is not None else None,
        "sample_count": len(samples),
        **verbose_stats,
        "ttft_ms": ttft_ms,
    }

    # サマリCSVへ追記
    write_header = not SUMMARY_CSV.exists()
    with open(SUMMARY_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    # 生データCSVを個別保存
    safe_label = re.sub(r"[^\w\-]+", "_", label) or "run"
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    samples_path = FUNCTIONAL_DIR / f"{safe_label}_{ts_str}_samples.csv"
    with open(samples_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["t", "vram_used_mib", "vram_total_mib", "gpu_util_pct"]
        )
        writer.writeheader()
        writer.writerows(samples)

    print("\n===== ollama run 出力 =====")
    print(combined_output.strip())
    print("\n===== 記録結果 =====")
    for k, v in row.items():
        print(f"{k}: {v}")
    print(f"\n[info] サマリを追記: {SUMMARY_CSV}")
    print(f"[info] 生データを保存: {samples_path}")

    return row


def stop_model_and_measure(
    model: str, poll_interval: float = 0.3, timeout: float = 20.0
) -> tuple[float, dict | None, dict | None]:
    """`ollama stop <model>`を実行し、VRAM使用量が安定するまでの時間を計測する。

    戻り値: (stop_duration_s, stop前スナップショット, 安定後スナップショット)
    「安定」の判定は、連続する2回のスナップショットでVRAM使用量の差が
    50MiB以内に収まった時点とする(taskoutを完全な0待ちにしないための緩衝)。
    """
    vram_before = get_gpu_snapshot()
    t0 = time.time()
    subprocess.run(
        ["ollama", "stop", model],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    last_snap = get_gpu_snapshot()
    stable_count = 0
    while time.time() - t0 < timeout:
        if stable_count >= 2:
            break
        time.sleep(poll_interval)
        snap = get_gpu_snapshot()
        if snap is not None and last_snap is not None:
            if abs(snap["vram_used_mib"] - last_snap["vram_used_mib"]) <= 50:
                stable_count += 1
            else:
                stable_count = 0
        last_snap = snap

    stop_duration = time.time() - t0
    return stop_duration, vram_before, last_snap


def run_swap_sequence(
    models: list[str],
    prompt: str,
    label: str,
    cycles: int,
    interval: float,
    release_timeout: float,
) -> list[dict]:
    """`--swap-models`で指定された順序でモデルを切り替えながら、
    各切り替え(stop→VRAM安定待ち→run)の所要時間とVRAMを記録する。
    `cycles`>1ならリスト全体を繰り返し、連続切り替えによるVRAMリークの
    有無も末尾で確認する。
    """
    SWAP_DIR.mkdir(parents=True, exist_ok=True)

    sequence = models * cycles
    n_models = len(models)
    initial_snapshot = get_gpu_snapshot()

    rows: list[dict] = []
    prev_model: str | None = None
    for idx, model in enumerate(sequence):
        cycle_idx = idx // n_models + 1
        step_idx = idx % n_models + 1

        if prev_model is None:
            stop_duration = None
            vram_before_stop = initial_snapshot
            vram_after_stop = initial_snapshot
            note = "初回ロード(スワップなし)"
        else:
            stop_duration, vram_before_stop, vram_after_stop = stop_model_and_measure(
                prev_model, timeout=release_timeout
            )
            note = ""

        run_result = execute_ollama_run(model, prompt, interval)
        verbose_stats = run_result["verbose_stats"]
        load_d = verbose_stats.get("load_duration_s")
        total_d = verbose_stats.get("total_duration_s")

        swap_total_wait = None
        if stop_duration is not None:
            base = load_d if load_d is not None else total_d
            if base is not None:
                swap_total_wait = round(stop_duration + base, 3)

        vram_before_val = vram_before_stop["vram_used_mib"] if vram_before_stop else None
        vram_after_val = vram_after_stop["vram_used_mib"] if vram_after_stop else None

        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "cycle": cycle_idx,
            "step": step_idx,
            "from_model": prev_model or "",
            "to_model": model,
            "vram_before_stop_mib": vram_before_val,
            "vram_after_stop_mib": vram_after_val,
            "vram_released_mib": (
                round(vram_before_val - vram_after_val, 1)
                if vram_before_val is not None and vram_after_val is not None
                else None
            ),
            "stop_duration_s": round(stop_duration, 3) if stop_duration is not None else None,
            "load_duration_s": load_d,
            "total_duration_s": total_d,
            "swap_total_wait_s": swap_total_wait,
            "vram_peak_during_run_mib": run_result["vram_peak"],
            "vram_avg_during_run_mib": (
                round(run_result["vram_avg"], 1) if run_result["vram_avg"] is not None else None
            ),
            "gpu_util_peak_pct": run_result["util_peak"],
            "gpu_util_avg_pct": (
                round(run_result["util_avg"], 1) if run_result["util_avg"] is not None else None
            ),
            "eval_rate_tps": verbose_stats.get("eval_rate_tps"),
            "note": note,
        }
        rows.append(row)
        prev_model = model

    # 全transition終了後、最後のモデルも停止してアイドル状態のVRAMを確認(リーク判定用)
    final_stop_duration, vram_before_final_stop, vram_after_final_stop = stop_model_and_measure(
        prev_model, timeout=release_timeout
    )
    initial_idle = initial_snapshot["vram_used_mib"] if initial_snapshot else None
    final_idle = vram_after_final_stop["vram_used_mib"] if vram_after_final_stop else None
    leak_note = ""
    if initial_idle is not None and final_idle is not None:
        leak_delta = round(final_idle - initial_idle, 1)
        leak_note = (
            f"開始前アイドルVRAM {initial_idle}MiB → 全スワップ終了後アイドルVRAM {final_idle}MiB"
            f"(差分 {leak_delta:+.1f}MiB)"
        )
        if leak_delta >= 500:
            leak_note += " ※リーク疑いあり(500MiB以上増加)"

    vram_before_final_val = (
        vram_before_final_stop["vram_used_mib"] if vram_before_final_stop else None
    )
    rows.append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "cycle": "-",
            "step": "-",
            "from_model": prev_model or "",
            "to_model": "(全停止・リーク確認)",
            "vram_before_stop_mib": vram_before_final_val,
            "vram_after_stop_mib": final_idle,
            "vram_released_mib": (
                round(vram_before_final_val - final_idle, 1)
                if vram_before_final_val is not None and final_idle is not None
                else None
            ),
            "stop_duration_s": round(final_stop_duration, 3) if final_stop_duration is not None else None,
            "load_duration_s": None,
            "total_duration_s": None,
            "swap_total_wait_s": None,
            "vram_peak_during_run_mib": None,
            "vram_avg_during_run_mib": None,
            "gpu_util_peak_pct": None,
            "gpu_util_avg_pct": None,
            "eval_rate_tps": None,
            "note": leak_note or "リーク確認用の最終停止",
        }
    )

    safe_label = re.sub(r"[^\w\-]+", "_", label) or "swap"
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    swap_path = SWAP_DIR / f"{safe_label}_{ts_str}_swap.csv"
    with open(swap_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SWAP_HEADER)
        writer.writeheader()
        writer.writerows(rows)

    print("\n===== スワップ計測 結果 =====")
    for row in rows:
        print(
            f"[cycle{row['cycle']}/step{row['step']}] {row['from_model'] or '(なし)'} -> {row['to_model']}: "
            f"stop={row['stop_duration_s']}s load={row['load_duration_s']}s "
            f"合計待ち={row['swap_total_wait_s']}s VRAM解放={row['vram_released_mib']}MiB"
        )
        if row["note"]:
            print(f"    note: {row['note']}")
    print(f"\n[info] スワップ計測CSVを保存: {swap_path}")

    return rows


def main():
    # Windowsのコンソールがcp932だと日本語プロンプトの表示でコケることがあるためutf-8化
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default=None, help="ollama listで表示されるモデル名(通常モード用)"
    )
    parser.add_argument(
        "--prompt", default=None, help="投げるプロンプト(--prompt-fileとどちらか必須)"
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="プロンプトをUTF-8テキストファイルから読み込む(長文の実コード投入テスト向け。--promptより優先)",
    )
    parser.add_argument(
        "--label", default=None, help="記録用のラベル(未指定ならモデル名を使用)"
    )
    parser.add_argument(
        "--interval", type=float, default=1.0, help="nvidia-smiのポーリング間隔(秒)"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="同じプロンプトで複数回試行し、平均値も記録する(ノート推奨: 3回、通常モードのみ)",
    )
    parser.add_argument(
        "--swap-models",
        default=None,
        help=(
            "スワップ計測モードを有効にし、カンマ区切りで切り替え順序を指定する"
            "(例: 'gemma4:26b,gpt-oss:20b,devstral-small-2:24b,gemma4:26b')。"
            "指定した場合 --model は無視される。"
        ),
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="スワップ計測モードで--swap-modelsのリストを繰り返す回数(連続切り替えのリーク確認用)",
    )
    parser.add_argument(
        "--release-timeout",
        type=float,
        default=20.0,
        help="スワップ計測モードで、ollama stop後にVRAMが安定するまで待つ最大秒数",
    )
    args = parser.parse_args()

    if args.prompt_file:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.is_absolute():
            # 相対パスはこのスクリプトのカレントディレクトリではなく
            # スクリプト自身の場所(scripts/)基準でも探せるようにする
            candidates = [Path(args.prompt_file), SCRIPT_DIR / args.prompt_file]
            prompt_path = next((p for p in candidates if p.exists()), prompt_path)
        if not prompt_path.exists():
            parser.error(f"--prompt-fileで指定されたファイルが見つかりません: {args.prompt_file}")
        args.prompt = prompt_path.read_text(encoding="utf-8")
    elif not args.prompt:
        parser.error("--prompt か --prompt-file のどちらかを指定してください")

    if args.swap_models:
        models = [m.strip() for m in args.swap_models.split(",") if m.strip()]
        if len(models) < 2:
            parser.error("--swap-modelsは2つ以上のモデルをカンマ区切りで指定してください")
        label = args.label or "スワップ検証"
        run_swap_sequence(
            models=models,
            prompt=args.prompt,
            label=label,
            cycles=args.cycles,
            interval=args.interval,
            release_timeout=args.release_timeout,
        )
        return

    if not args.model:
        parser.error("--model か --swap-models のどちらかを指定してください")

    base_label = args.label or args.model
    rows = []
    for i in range(1, args.repeat + 1):
        run_label = base_label if args.repeat == 1 else f"{base_label}_run{i}"
        rows.append(run_and_monitor(args.model, args.prompt, run_label, args.interval))

    if args.repeat > 1:
        numeric_keys = [
            k
            for k in SUMMARY_HEADER
            if k not in ("timestamp", "label", "model", "prompt")
        ]
        avg_row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "label": f"{base_label}_avg({args.repeat}回)",
            "model": args.model,
            "prompt": args.prompt,
        }
        for k in numeric_keys:
            values = [r[k] for r in rows if r.get(k) is not None]
            avg_row[k] = round(sum(values) / len(values), 3) if values else None

        write_header = not SUMMARY_CSV.exists()
        with open(SUMMARY_CSV, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADER)
            if write_header:
                writer.writeheader()
            writer.writerow(avg_row)

        print("\n===== 平均値(全" + str(args.repeat) + "回) =====")
        for k, v in avg_row.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
