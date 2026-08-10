"""
make_stt_bench_audio.py
--------------------------
stt_bench.py用のベンチ音声を、VOICEVOX(東北ずん子・話者ID 107。8日目②タスク1で
確定済みの声)で合成して作るツール。9日目ノート
(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)③の部品。

狙い:
  ③の作業内容にある「ベンチ用の音声を録る(固有名詞を含む台詞を数本。台詞そのものも
  ノートに残す)」を、実際にマイクで録音する代わりに、採用済みのTTS声(東北ずん子)で
  合成して用意する。台詞にはRAG検索で使う固有名詞(C.L.A.I.R.E. / LanceDB / Ruri /
  Tailscale / Obsidian / VOICEVOX)を必ず含める。

  短い台詞を複数(sample01〜04)に加え、③の「逐次表示(方式A)の実測」に使う長尺の台詞
  (sample_long.wav。発話20秒時点の再転写を測るため、既定speedScaleで20秒を超える
  長さになるよう文字数を確保している)も合成する。

  合成した台詞の原文は results/stt_bench/transcripts.md に書き出す
  (後で認識結果と突き合わせる基準として、必ずファイルに残す)。

前提: VOICEVOX ENGINEが起動していること(既定 http://127.0.0.1:50021)。

使い方:
    python make_stt_bench_audio.py --engine-url http://127.0.0.1:50021 --speaker 107

出力先: scripts/results/stt_bench/sample01.wav 〜 sample04.wav, sample_long.wav,
       scripts/results/stt_bench/transcripts.md

標準ライブラリ + tts_adapter.py(8日目に作成済みの部品)のみを使う。
"""

from __future__ import annotations

import argparse
import io
import sys
import wave
from datetime import datetime
from pathlib import Path

from tts_adapter import TTSAdapterError, synthesize

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results" / "stt_bench"

# 固有名詞を1〜2個ずつ含む短い台詞。stt_bench.pyのPROPER_NOUNSと対応させている。
SHORT_SAMPLES: list[tuple[str, str]] = [
    ("sample01", "クレアです。C.L.A.I.R.E.と呼んでください。"),
    ("sample02", "ノート検索にはLanceDBと、埋め込みモデルのRuriを使っています。"),
    ("sample03", "外部アクセスはTailscale経由で、Obsidianのvaultを参照します。"),
    ("sample04", "読み上げにはVOICEVOXの東北ずん子を使用しています。"),
]

# 逐次表示(方式A)の計測用に、発話20秒時点まで測れる長さの台詞。
# 9日目①の実測(既定速度で85文字=約14秒)から逆算し、20秒超になるよう150文字前後にしている。
LONG_SAMPLE: tuple[str, str] = (
    "sample_long",
    "こんにちは、クレアです。C.L.A.I.R.E.という名前で活動しています。"
    "ノート検索にはLanceDBというベクトルデータベースと、埋め込みモデルのRuriを組み合わせて使っています。"
    "外部からのアクセスはTailscaleを経由し、Obsidianのvaultに保存されたノートを参照します。"
    "音声の読み上げにはVOICEVOXの東北ずん子を採用しました。何かお手伝いできることがあれば、遠慮なく聞いてください。",
)


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        if rate <= 0:
            return 0.0
        return frames / float(rate)


def generate_samples(
    engine_url: str,
    speaker_id: int,
    speed_scale: float | None,
    output_dir: Path,
    timeout: float,
) -> list[tuple[str, str, float]]:
    """(ファイル名, 台詞, 再生秒数) のリストを返す。1件失敗しても残りは続行する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str, float]] = []

    for name, text in [*SHORT_SAMPLES, LONG_SAMPLE]:
        print(f"[synthesize] {name}: {text[:30]}{'...' if len(text) > 30 else ''}")
        try:
            wav_bytes = synthesize(engine_url, text, speaker_id, speed_scale=speed_scale, timeout=timeout)
        except TTSAdapterError as e:
            print(f"  NG: {e}", file=sys.stderr)
            continue
        duration = _wav_duration_seconds(wav_bytes)
        out_path = output_dir / f"{name}.wav"
        out_path.write_bytes(wav_bytes)
        print(f"  -> {out_path}({duration:.1f}秒)")
        results.append((name, text, duration))

    return results


def write_transcripts(results: list[tuple[str, str, float]], output_dir: Path, speaker_id: int, speed_scale: float | None) -> Path:
    lines: list[str] = []
    lines.append(f"# STTベンチ用音声の台詞一覧 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append("")
    lines.append("VOICEVOX(東北ずん子)で合成したstt_bench.py用のベンチ音声。認識結果と突き合わせる基準原文。")
    lines.append("")
    lines.append(f"- 話者ID: `{speaker_id}`(東北ずん子)")
    lines.append(f"- speedScale: `{speed_scale if speed_scale is not None else '既定値'}`")
    lines.append("")
    lines.append("| ファイル | 再生秒数 | 台詞(原文) |")
    lines.append("|---|---|---|")
    for name, text, duration in results:
        lines.append(f"| {name}.wav | {duration:.1f} | {text} |")
    lines.append("")

    out_path = output_dir / "transcripts.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VOICEVOX(東北ずん子)でstt_bench.py用のベンチ音声を合成する")
    parser.add_argument("--engine-url", default="http://127.0.0.1:50021", help="VOICEVOX ENGINEのベースURL")
    parser.add_argument("--speaker", type=int, default=107, help="話者ID(既定: 107=東北ずん子ノーマル)")
    parser.add_argument("--speed-scale", type=float, default=None, help="話速倍率。未指定なら既定値")
    parser.add_argument("--timeout", type=float, default=30.0, help="1リクエストあたりのタイムアウト秒数")
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力先ディレクトリ(既定: scripts/results/stt_bench)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)

    results = generate_samples(args.engine_url, args.speaker, args.speed_scale, output_dir, args.timeout)
    if not results:
        print("エラー: 1件も合成できませんでした(VOICEVOX ENGINEが起動しているか確認してください)", file=sys.stderr)
        return 1

    transcripts_path = write_transcripts(results, output_dir, args.speaker, args.speed_scale)
    print()
    print(f"[make_stt_bench_audio] {len(results)}件のwavを保存しました: {output_dir}")
    print(f"[make_stt_bench_audio] 台詞一覧を保存しました: {transcripts_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
