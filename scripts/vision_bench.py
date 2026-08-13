"""
vision_bench.py
----------------
11日目ノート④-1(サポートAI作製計画/11日目Web検索対応・UIデザイン確定・マルチモーダル対応調査.md)の
残課題「vision対応と判明した場合、画像1枚を実際に渡してVRAMピークと応答内容の妥当性を確認する」に
対応するツール。

前提(④-1で確認済み): `ollama show gemma4-e4b-cpu` / `ollama show gemma4:26b` の
`capabilities` に `vision` が含まれており、追加モデルの導入なしで画像入力を渡せる。
このスクリプトはその前提のもと、実際に画像1枚を渡して
  1. 応答内容が画像の中身と合っているか(自動判定はできないため、応答をそのまま出力し目視確認に委ねる)
  2. VRAMピークが③で判明した「残り約450MiB」という逼迫状況で問題を起こさないか
の2点を実測する。

使い方:
    python vision_bench.py --model gemma4:26b --image path/to/photo.jpg
    python vision_bench.py --model gemma4-e4b-cpu --image path/to/photo.jpg --prompt "この画像は何ですか?"

    2モデルまとめて比較(ルーター用gemma4-e4b-cpu・DEEP用gemma4:26bの両方を1回で計測):
    python vision_bench.py --models gemma4-e4b-cpu,gemma4:26b --image path/to/photo.jpg

出力先: scripts/results/vision_bench/vision_bench_<日時>.md(標準出力にも同じ内容を出す)

VRAM計測は monitor_ollama.py の GpuSampler をそのまま再利用する(nvidia-smiを1秒間隔でポーリング)。
モデル呼び出し自体は `ollama run` CLI ではなく ollama_client.generate() 経由(/api/generate)で行う。
CLIの`ollama run`は対話モード前提でbase64画像を直接渡す口が無く、スクリプトから安定して
画像を渡すには /api/generate の `images` フィールド(base64リスト)を使うAPI経由が適している。
"""
from __future__ import annotations

import argparse
import base64
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import ollama_client  # noqa: E402
from monitor_ollama import GpuSampler, get_gpu_snapshot  # noqa: E402

DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results" / "vision_bench"
DEFAULT_PROMPT = "この画像に何が写っているか、日本語で具体的に説明してください。"


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def bench_one(
    model: str,
    image_b64: str,
    prompt: str,
    host: str,
    timeout: float,
    interval: float,
) -> dict:
    """指定モデルへ画像+プロンプトを1回投げ、応答とVRAM推移を記録して返す。"""
    vram_before = get_gpu_snapshot()

    sampler = GpuSampler(interval=interval)
    sampler.start()
    t0 = time.monotonic()
    error: str | None = None
    response_text = ""
    try:
        response_text = ollama_client.generate(
            model=model,
            prompt=prompt,
            host=host,
            timeout=timeout,
            images=[image_b64],
        )
    except ollama_client.OllamaError as e:
        error = str(e)
    elapsed = time.monotonic() - t0
    sampler.stop()

    samples = sampler.samples
    vram_after = get_gpu_snapshot()
    if samples:
        vram_peak = max(s["vram_used_mib"] for s in samples)
        vram_avg = sum(s["vram_used_mib"] for s in samples) / len(samples)
        vram_total = samples[0]["vram_total_mib"]
    else:
        vram_peak = vram_avg = vram_total = None

    return {
        "model": model,
        "prompt": prompt,
        "response": response_text,
        "error": error,
        "elapsed_s": round(elapsed, 2),
        "vram_before_mib": vram_before["vram_used_mib"] if vram_before else None,
        "vram_after_mib": vram_after["vram_used_mib"] if vram_after else None,
        "vram_peak_mib": vram_peak,
        "vram_avg_mib": round(vram_avg, 1) if vram_avg is not None else None,
        "vram_total_mib": vram_total,
        "sample_count": len(samples),
    }


def format_markdown(results: list[dict], image_path: Path) -> str:
    lines: list[str] = []
    lines.append(f"# vision対応モデル 画像入力ベンチ結果 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append("")
    lines.append(f"- 画像: `{image_path}`")
    lines.append("")
    lines.append("| モデル | 所要時間(秒) | VRAM前(MiB) | VRAMピーク(MiB) | VRAM後(MiB) | VRAM総量(MiB) | エラー |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        err = r["error"] or "-"
        lines.append(
            f"| {r['model']} | {r['elapsed_s']} | {r['vram_before_mib']} | {r['vram_peak_mib']} | "
            f"{r['vram_after_mib']} | {r['vram_total_mib']} | {err} |"
        )
    lines.append("")

    lines.append("## 応答内容(目視で画像の中身と合っているか確認すること)")
    lines.append("")
    for r in results:
        lines.append(f"### {r['model']}")
        lines.append("")
        lines.append(f"プロンプト: {r['prompt']}")
        lines.append("")
        if r["error"]:
            lines.append(f"**エラー**: {r['error']}")
        else:
            lines.append("```")
            lines.append(r["response"])
            lines.append("```")
        lines.append("")

    lines.append("## 自動判定できない点(必ず目視確認すること)")
    lines.append("")
    lines.append("- 応答内容が実際の画像の中身と一致しているか(このスクリプトは応答を表示するだけで、正誤判定はしない)")
    lines.append("- VRAMピークが16GB環境の残り枠(11日目ノート結果1: 通常運用でピーク15843MiB・残り約450MiB)を圧迫していないか")
    lines.append("")

    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="vision対応モデルへ画像を渡し、応答とVRAMピークを実測する(11日目④-1)"
    )
    parser.add_argument("--image", required=True, help="渡す画像ファイルのパス")
    parser.add_argument(
        "--model", default=None, help="単一モデルで計測する場合のモデル名(--modelsとどちらか必須)"
    )
    parser.add_argument(
        "--models",
        default=None,
        help="カンマ区切りで複数モデルを計測する場合(例: 'gemma4-e4b-cpu,gemma4:26b')。--modelより優先",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="画像と一緒に送るプロンプト")
    parser.add_argument("--host", default=ollama_client.DEFAULT_HOST, help="OllamaのベースURL")
    parser.add_argument("--timeout", type=float, default=120.0, help="1リクエストあたりのタイムアウト秒数")
    parser.add_argument("--interval", type=float, default=1.0, help="nvidia-smiのポーリング間隔(秒)")
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力先ディレクトリ(既定: scripts/results/vision_bench)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    elif args.model:
        models = [args.model]
    else:
        print("エラー: --model か --models のどちらかを指定してください", file=sys.stderr)
        return 1

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"エラー: 画像ファイルが見つかりません: {image_path}", file=sys.stderr)
        return 1

    image_b64 = _encode_image(image_path)

    results = []
    for model in models:
        print(f"[bench] {model} へ画像を送信中...")
        r = bench_one(model, image_b64, args.prompt, args.host, args.timeout, args.interval)
        if r["error"]:
            print(f"  -> エラー: {r['error']}")
        else:
            print(f"  -> {r['elapsed_s']}秒 / VRAMピーク {r['vram_peak_mib']}MiB")
            print(f"  応答: {r['response'][:200]}{'...' if len(r['response']) > 200 else ''}")
        results.append(r)

    markdown = format_markdown(results, image_path)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"vision_bench_{ts_str}.md"
    out_path.write_text(markdown, encoding="utf-8")

    print(f"\n[info] 結果を保存: {out_path}")
    print("\n" + markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
