"""
tts_latency_bench.py
----------------------
VOICEVOX互換API(VOICEVOX ENGINE / AivisSpeech Engine)で、1文あたりの音声合成に
かかる時間を実測するツール。9日目ノート
(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)①の部品。

狙い:
  9日目で作る「文単位パイプライン」(生成しながら1文ずつ合成して鳴らす)が
  成立するかどうかは、「1文の合成時間 < その1文の再生時間」が成り立つかで決まる。
  これが成り立つ文字数の範囲を実測し、sentence_splitter.py の最大文字数の
  設計値として使う。

  あわせて、初回呼び出し(コールドスタート)と2回目以降の差(ウォームアップの要否)、
  話速(speedScale)ごとの所要時間の変化も測る。

使い方:
    # 基本(既定の文字数別サンプル文で計測)
    python tts_latency_bench.py --engine-url http://127.0.0.1:50021 --speaker 107

    # 話速を変えて比較する
    python tts_latency_bench.py --engine-url http://127.0.0.1:50021 --speaker 107 --speed-scale 1.2

    # 独自の文で測りたい場合(改行区切りのテキストファイル。1行=1文)
    python tts_latency_bench.py --engine-url http://127.0.0.1:50021 --speaker 107 --sentences-file my_sentences.txt

出力先: scripts/results/tts_latency/latency_<日時>.md (表形式で保存する。標準出力にも同じ表を出す)

標準ライブラリのみで実装(tts_adapter.py / ollama_client.py の既存方針を踏襲)。
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tts_adapter import TTSAdapterError, synthesize

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results" / "tts_latency"

# 9日目ノート①の想定表(10/30/60/120文字)に合わせた既定サンプル文。
# クレアの発話らしい定型文で、文字数が段階的に伸びるように作る。
DEFAULT_SENTENCES: list[str] = [
    "こんにちは、クレアです。",
    "今日も一日お疲れさまでした。何かお手伝いできることはありますか。",
    "少々お待ちください、今調べています。関連しそうな記憶が見つかったら、そのまま続けて回答します。",
    "確認したところ、いくつか候補が見つかりました。一つずつ簡単に説明しますので、"
    "気になるものがあれば遠慮なく聞き返してください。長くなりそうな場合は区切りながらお伝えします。",
]


@dataclass
class BenchResult:
    text: str
    char_count: int
    synth_seconds: float
    wav_seconds: float
    kind: str  # "cold" | "warm" | "normal" (cold/warmは先頭文のウォームアップ計測、normalは通常の1回計測)

    @property
    def real_time_ratio(self) -> float:
        """合成時間 / 再生時間。1.0未満なら『再生している間に次の合成が終わる』ので
        文単位パイプラインが途切れない。値が小さいほど余裕がある。"""
        if self.wav_seconds <= 0:
            return float("inf")
        return self.synth_seconds / self.wav_seconds

    @property
    def pipeline_ok(self) -> bool:
        return self.real_time_ratio < 1.0


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    """wavバイト列からヘッダ情報だけを読み、再生時間(秒)を計算する(標準ライブラリのwaveモジュール)。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        if rate <= 0:
            return 0.0
        return frames / float(rate)


def bench_one(
    engine_url: str,
    text: str,
    speaker_id: int,
    speed_scale: float | None,
    timeout: float,
    kind: str,
) -> BenchResult:
    start = time.monotonic()
    wav_bytes = synthesize(engine_url, text, speaker_id, speed_scale=speed_scale, timeout=timeout)
    synth_seconds = time.monotonic() - start
    wav_seconds = _wav_duration_seconds(wav_bytes)
    return BenchResult(
        text=text,
        char_count=len(text),
        synth_seconds=synth_seconds,
        wav_seconds=wav_seconds,
        kind=kind,
    )


def run_bench(
    engine_url: str,
    speaker_id: int,
    sentences: list[str],
    speed_scale: float | None,
    timeout: float,
    measure_warmup: bool,
) -> list[BenchResult]:
    """計測を実行する。戻り値は [cold?, warm_or_normal(先頭文), 2文目, 3文目, ...] の順。

    measure_warmup=Trueの場合、先頭の文だけ「初回(cold)」を追加でもう1回計測する
    (2回目=warmの計測は、そのまま通常のベンチ結果としても使い回すため、
    先頭文を3回計測するような無駄はしない)。
    """
    results: list[BenchResult] = []

    for i, text in enumerate(sentences):
        if i == 0 and measure_warmup:
            print(f"[warmup] 初回呼び出しを計測中: {text[:20]}...")
            cold = bench_one(engine_url, text, speaker_id, speed_scale, timeout, kind="cold")
            print(f"  cold: synth={cold.synth_seconds:.2f}s wav={cold.wav_seconds:.2f}s")
            results.append(cold)
            kind = "warm"
        else:
            kind = "normal"

        print(f"[bench] {len(text)}文字: {text[:30]}{'...' if len(text) > 30 else ''}")
        r = bench_one(engine_url, text, speaker_id, speed_scale, timeout, kind=kind)
        print(
            f"  synth={r.synth_seconds:.2f}s  wav={r.wav_seconds:.2f}s  "
            f"実時間比={r.real_time_ratio:.2f}  パイプライン成立={'OK' if r.pipeline_ok else 'NG'}"
        )
        results.append(r)

    return results


def format_markdown(
    results: list[BenchResult],
    engine_url: str,
    speaker_id: int,
    speed_scale: float | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# TTS合成レイテンシ計測結果 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append("")
    lines.append(f"- エンジン: `{engine_url}`")
    lines.append(f"- 話者ID: `{speaker_id}`")
    lines.append(f"- 話速(speedScale): `{speed_scale if speed_scale is not None else '既定値'}`")
    lines.append("")

    cold_result = next((r for r in results if r.kind == "cold"), None)
    warm_result = next((r for r in results if r.kind == "warm"), None)
    if cold_result is not None and warm_result is not None:
        lines.append("## ウォームアップ差(初回 vs 2回目)")
        lines.append("")
        lines.append("| 状態 | 文字数 | 合成時間(秒) | 再生時間(秒) |")
        lines.append("|---|---|---|---|")
        lines.append(f"| 初回(cold) | {cold_result.char_count} | {cold_result.synth_seconds:.3f} | {cold_result.wav_seconds:.3f} |")
        lines.append(f"| 2回目(warm) | {warm_result.char_count} | {warm_result.synth_seconds:.3f} | {warm_result.wav_seconds:.3f} |")
        lines.append("")

    lines.append("## 文字数別の合成時間")
    lines.append("")
    lines.append("| 文字数 | 例文(先頭30文字) | 合成時間(秒) | 再生時間(秒) | 実時間比(合成/再生) | パイプライン成立 |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        if r.kind == "cold":
            continue  # ウォームアップ用のcold計測は本表には含めない(warmの方を通常結果として使う)
        excerpt = r.text[:30].replace("|", "\\|")
        ok = "OK" if r.pipeline_ok else "**NG**"
        lines.append(
            f"| {r.char_count} | {excerpt}{'...' if len(r.text) > 30 else ''} "
            f"| {r.synth_seconds:.3f} | {r.wav_seconds:.3f} | {r.real_time_ratio:.2f} | {ok} |"
        )
    lines.append("")

    table_results = [r for r in results if r.kind != "cold"]
    ok_max_chars = max((r.char_count for r in table_results if r.pipeline_ok), default=0)
    ng_entries = [r for r in table_results if not r.pipeline_ok]
    lines.append("## 結論(自動判定。目視でも必ず確認すること)")
    lines.append("")
    if ng_entries:
        min_ng_chars = min(r.char_count for r in ng_entries)
        lines.append(
            f"- パイプラインが成立した最大文字数の目安: **{ok_max_chars}文字**"
            f"(それ以上、{min_ng_chars}文字の例で実時間比が1.0を超過)"
        )
        lines.append(
            f"- → `sentence_splitter.py` の強制分割の閾値は **{ok_max_chars}文字以下** に設定することを推奨"
        )
    else:
        lines.append(f"- 計測した範囲(最大{ok_max_chars}文字)では、全て実時間比1.0未満でパイプラインが成立した")
        lines.append("- より長い文字数でも成立するか、`--sentences-file`でさらに長い文を追加して確認するとよい")
    lines.append("")

    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VOICEVOX互換APIで1文あたりの合成時間を実測し、文単位パイプラインの成立性を判定する"
    )
    parser.add_argument("--engine-url", required=True, help="VOICEVOX互換APIのベースURL(例: http://127.0.0.1:50021)")
    parser.add_argument("--speaker", type=int, required=True, help="話者ID(採用済みなら107=東北ずん子ノーマル)")
    parser.add_argument("--speed-scale", type=float, default=None, help="話速倍率(1.0が標準)。未指定なら既定値")
    parser.add_argument(
        "--sentences-file",
        default=None,
        help="独自の文で測りたい場合の入力ファイル(改行区切り、1行=1文)。未指定なら既定サンプル文を使う",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="1リクエストあたりのタイムアウト秒数")
    parser.add_argument(
        "--no-warmup", action="store_true", help="ウォームアップ差(初回 vs 2回目)の計測をスキップする"
    )
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力先ディレクトリ(既定: scripts/results/tts_latency)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.sentences_file:
        text = Path(args.sentences_file).read_text(encoding="utf-8")
        sentences = [line.strip() for line in text.splitlines() if line.strip()]
        if not sentences:
            print(f"エラー: {args.sentences_file} に有効な行がありません", file=sys.stderr)
            return 1
    else:
        sentences = DEFAULT_SENTENCES

    try:
        results = run_bench(
            engine_url=args.engine_url,
            speaker_id=args.speaker,
            sentences=sentences,
            speed_scale=args.speed_scale,
            timeout=args.timeout,
            measure_warmup=not args.no_warmup,
        )
    except TTSAdapterError as e:
        print(f"エラー: TTSエンジン呼び出しに失敗しました: {e}", file=sys.stderr)
        return 1

    markdown = format_markdown(results, args.engine_url, args.speaker, args.speed_scale)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"latency_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(markdown, encoding="utf-8")

    print()
    print(markdown)
    print(f"[tts_latency_bench] 結果を保存しました: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
