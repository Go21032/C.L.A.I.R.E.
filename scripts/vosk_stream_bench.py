r"""
vosk_stream_bench.py
--------------------
方式B(2段構え)の暫定表示側を担う軽量エンジン Vosk が、
「発話中に部分結果を低遅延で出し続けられるか」を実測するツール。
9日目ノート(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)③の部品。

狙い:
  ③の実測で方式A(定期再転写)は発話15〜20秒あたりで閾値(1回1.0秒)を超え、
  30秒地点では1回1.5秒まで伸びた(音声1秒あたり約+58msでほぼ線形に悪化する)。
  方式BはVoskのようなストリーミング型エンジンに暫定表示を任せる案なので、
  ここで確認すべきは「速いか」ではなく **「発話が伸びても1チャンクの処理時間が
  一定のままか(累積しないか)」** の1点。

  そのため本スクリプトは、wavをマイク入力に見立てて一定長のチャンクに切り、
  1チャンクずつ AcceptWaveform に流しながら、
    - チャンクごとの処理時間(= 追いつけているか。realtime factor)
    - 発話前半と後半で処理時間が増えていないか(= 方式Aとの決定的な差)
    - 部分結果(partial)が何回・どの間隔で更新されるか(= 暫定表示の滑らかさ)
    - Voskの最終テキスト(= 暫定表示としての読みやすさ。確定はWhisperが取り直す)
  を計測してMarkdownに保存する。

使い方:
    # 事前準備(モデルのダウンロード。48MBの小型日本語モデル)
    #   https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip を
    #   ~/vosk_models/vosk-model-small-ja-0.22 に展開しておく
    #
    #   【重要】モデルの置き場所に日本語(非ASCII)を含めるとロードに失敗する。
    #   vault内(...\サポートAI作製計画\scripts\models\...)に置くと
    #   "Folder ... does not contain model files" / "Failed to create a model" で落ちる。
    #   そのためvault外のASCIIパス(既定: ~/vosk_models)を使う。
    python vosk_stream_bench.py --audio results/stt_bench/sample_long.wav

    # チャンク長(暫定表示の更新粒度)を変えて比較する
    python vosk_stream_bench.py --audio results/stt_bench/sample_long.wav --chunk-ms 100

出力先:
  scripts/results/stt_bench/vosk_stream_<日時>.md

既存スクリプト(tts_latency_bench.py / check_faster_whisper_gpu.py / stt_bench.py)の方針を踏襲:
標準ライブラリ + 検証対象そのもの(vosk)だけを使い、失敗しても切り分け情報を必ず残す。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results" / "stt_bench"
# Voskのモデルパスは非ASCII(日本語)を含むとロードに失敗するため、vault内ではなく
# ホーム直下のASCIIパスを既定にする。環境変数 VOSK_MODEL_DIR でも上書きできる。
DEFAULT_MODEL_DIR = Path(os.environ.get("VOSK_MODEL_DIR") or (Path.home() / "vosk_models" / "vosk-model-small-ja-0.22"))

TARGET_SAMPLE_RATE = 16000
DEFAULT_CHUNK_MS = 200

# 方式Bが成立するとみなす目安:
#  (1) 1チャンクの処理時間 < チャンク長 (= 実時間で追いつける)。余裕を見て50%を目標にする
#  (2) 発話後半のチャンク処理時間が前半の TREND_LIMIT 倍を超えない (= 累積しない)
REALTIME_MARGIN = 0.5
TREND_LIMIT = 1.5


class VoskBenchError(RuntimeError):
    """モデルのロードや音声デコードに失敗した場合に送出する。"""


@dataclass
class ChunkPoint:
    index: int
    elapsed_audio_seconds: float  # このチャンクを流し終えた時点の発話経過秒
    process_seconds: float  # このチャンクの AcceptWaveform にかかった時間
    partial_text: str
    partial_changed: bool


@dataclass
class StreamResult:
    audio_path: str
    audio_seconds: float
    chunk_ms: int
    model_dir: str
    points: list[ChunkPoint] = field(default_factory=list)
    final_text: str = ""
    total_process_seconds: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def chunk_seconds(self) -> float:
        return self.chunk_ms / 1000.0

    @property
    def max_process_seconds(self) -> float:
        return max((p.process_seconds for p in self.points), default=0.0)

    @property
    def max_point(self) -> ChunkPoint | None:
        """最遅チャンク。初回チャンク(遅延初期化)が外れ値になっていないかの確認用。"""
        return max(self.points, key=lambda p: p.process_seconds, default=None)

    @property
    def p95_process_seconds(self) -> float:
        """95パーセンタイル。単発の外れ値に判定を引きずられないための指標。"""
        if not self.points:
            return 0.0
        values = sorted(p.process_seconds for p in self.points)
        idx = min(len(values) - 1, int(len(values) * 0.95))
        return values[idx]

    @property
    def mean_process_seconds(self) -> float:
        return statistics.mean([p.process_seconds for p in self.points]) if self.points else 0.0

    @property
    def realtime_factor(self) -> float:
        """全チャンクの処理時間合計 / 音声長。1.0未満なら実時間で追いつける。"""
        if self.audio_seconds <= 0:
            return float("inf")
        return self.total_process_seconds / self.audio_seconds

    def halves_mean(self) -> tuple[float, float]:
        """(前半の平均処理時間, 後半の平均処理時間)。方式Aのような累積悪化がないかを見る。"""
        if len(self.points) < 4:
            return (0.0, 0.0)
        half = len(self.points) // 2
        first = statistics.mean([p.process_seconds for p in self.points[:half]])
        second = statistics.mean([p.process_seconds for p in self.points[half:]])
        return (first, second)

    @property
    def partial_update_count(self) -> int:
        return sum(1 for p in self.points if p.partial_changed)


def decode_to_pcm16(audio_path: Path) -> tuple[bytes, float]:
    """wavを16kHz/モノラル/16bitのPCMバイト列にする。戻り値: (pcm_bytes, 音声長秒)。

    まず標準ライブラリのwaveで読み、条件が合わなければ faster-whisper が同梱する
    デコーダ(PyAV)でリサンプルする。②③で既に導入済みの依存のみで完結させる。
    """
    try:
        with wave.open(str(audio_path), "rb") as wf:
            if wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == TARGET_SAMPLE_RATE:
                frames = wf.readframes(wf.getnframes())
                return frames, wf.getnframes() / float(TARGET_SAMPLE_RATE)
    except wave.Error:
        pass  # wav以外/圧縮wavならデコーダ側に任せる

    try:
        from faster_whisper.audio import decode_audio  # type: ignore
    except ImportError as e:
        raise VoskBenchError(
            f"16kHz/モノラル/16bit以外のwavをリサンプルするためにfaster-whisperが必要: {e}"
        ) from e

    try:
        waveform = decode_audio(str(audio_path), sampling_rate=TARGET_SAMPLE_RATE)
    except Exception as e:  # noqa: BLE001 - 失敗理由をそのまま残す
        raise VoskBenchError(f"音声デコードに失敗: {type(e).__name__}: {e}") from e

    import array

    pcm = array.array("h", (int(max(-1.0, min(1.0, float(s))) * 32767) for s in waveform))
    return pcm.tobytes(), len(waveform) / float(TARGET_SAMPLE_RATE)


def run_stream(audio_path: Path, model_dir: Path, chunk_ms: int) -> StreamResult:
    result = StreamResult(
        audio_path=str(audio_path), audio_seconds=0.0, chunk_ms=chunk_ms, model_dir=str(model_dir)
    )

    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore
    except ImportError as e:
        result.error = f"voskが未インストール: {e}(pip install vosk)"
        return result

    if not model_dir.exists():
        result.error = (
            f"Voskモデルが見つからない: {model_dir}\n"
            "https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip を展開しておくこと"
        )
        return result

    SetLogLevel(-1)  # Kaldiの詳細ログを抑制(計測ノイズを減らす)

    try:
        pcm, audio_seconds = decode_to_pcm16(audio_path)
    except VoskBenchError as e:
        result.error = str(e)
        return result
    result.audio_seconds = audio_seconds

    print(f"[load] Voskモデルをロード中: {model_dir}")
    load_start = time.perf_counter()
    try:
        model = Model(str(model_dir))
    except Exception as e:  # noqa: BLE001
        result.error = f"Voskモデルのロードに失敗: {type(e).__name__}: {e}"
        return result
    print(f"  ロード完了: {time.perf_counter() - load_start:.2f}秒")

    rec = KaldiRecognizer(model, TARGET_SAMPLE_RATE)
    bytes_per_chunk = int(TARGET_SAMPLE_RATE * (chunk_ms / 1000.0)) * 2

    last_partial = ""
    total_process = 0.0
    for i in range(0, len(pcm), bytes_per_chunk):
        chunk = pcm[i : i + bytes_per_chunk]
        index = i // bytes_per_chunk
        start = time.perf_counter()
        accepted = rec.AcceptWaveform(chunk)
        if accepted:
            payload = json.loads(rec.Result())
            text = payload.get("text", "")
        else:
            payload = json.loads(rec.PartialResult())
            text = payload.get("partial", "")
        process = time.perf_counter() - start
        total_process += process

        changed = text != last_partial and bool(text)
        if changed:
            last_partial = text
        result.points.append(
            ChunkPoint(
                index=index,
                elapsed_audio_seconds=min((i + len(chunk)) / 2 / TARGET_SAMPLE_RATE, audio_seconds),
                process_seconds=process,
                partial_text=text,
                partial_changed=changed,
            )
        )

    try:
        final_payload = json.loads(rec.FinalResult())
        result.final_text = final_payload.get("text", "")
    except Exception as e:  # noqa: BLE001
        result.final_text = f"(FinalResult取得に失敗: {type(e).__name__}: {e})"

    result.total_process_seconds = total_process
    return result


def format_markdown(result: StreamResult, whisper_reference: str | None) -> str:
    lines: list[str] = []
    lines.append(f"# 方式B(Vosk暫定表示)ストリーミング実測 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append("")
    lines.append(f"- 音声ファイル: `{result.audio_path}`(音声長 {result.audio_seconds:.1f}秒)")
    lines.append(f"- モデル: `{result.model_dir}`")
    lines.append(f"- チャンク長: {result.chunk_ms}ms(= 暫定表示の更新粒度)")
    lines.append(
        f"- 成立判定の目安: (1) 1チャンクの処理時間 < チャンク長×{REALTIME_MARGIN:.1f} "
        f"(2) 後半の平均処理時間 < 前半の{TREND_LIMIT:.1f}倍"
    )
    lines.append("")

    if not result.ok:
        lines.append(f"**NG: {result.error}**")
        lines.append("")
        return "\n".join(lines)

    lines.append("## サマリ")
    lines.append("")
    first_half, second_half = result.halves_mean()
    trend = (second_half / first_half) if first_half > 0 else float("inf")
    # 判定はp95で行う。1チャンクだけの外れ値(初回の遅延初期化など)で
    # 方式全体をNGにしないため。最大値は別行で必ず併記して目視できるようにする。
    rt_ok = result.p95_process_seconds < result.chunk_seconds * REALTIME_MARGIN
    trend_ok = trend <= TREND_LIMIT
    max_point = result.max_point
    lines.append("| 指標 | 値 | 判定 |")
    lines.append("|---|---|---|")
    lines.append(f"| チャンク数 | {len(result.points)} | - |")
    lines.append(
        f"| 1チャンクの処理時間(平均 / p95) | {result.mean_process_seconds * 1000:.1f}ms / "
        f"{result.p95_process_seconds * 1000:.1f}ms | {'OK' if rt_ok else '**NG**'}"
        f"(チャンク長{result.chunk_ms}ms) |"
    )
    if max_point is not None:
        lines.append(
            f"| 最遅チャンク(外れ値確認用) | {max_point.process_seconds * 1000:.1f}ms "
            f"(第{max_point.index}チャンク / 発話{max_point.elapsed_audio_seconds:.1f}秒地点) | 参考 |"
        )
    lines.append(
        f"| 前半平均 → 後半平均 | {first_half * 1000:.1f}ms → {second_half * 1000:.1f}ms "
        f"({trend:.2f}倍) | {'OK(累積しない)' if trend_ok else '**NG(累積悪化)**'} |"
    )
    lines.append(
        f"| リアルタイム係数(全処理時間/音声長) | {result.realtime_factor:.3f} | "
        f"{'OK' if result.realtime_factor < 1.0 else '**NG**'} |"
    )
    lines.append(f"| 部分結果の更新回数 | {result.partial_update_count}回 | - |")
    lines.append("")

    lines.append("## 発話経過ごとの処理時間(方式Aとの比較用に5秒刻みで抜粋)")
    lines.append("")
    lines.append("| 発話経過(秒) | このチャンクの処理時間(ms) | その時点の暫定テキスト(先頭40字) |")
    lines.append("|---|---|---|")
    next_mark = 5.0
    for p in result.points:
        if p.elapsed_audio_seconds >= next_mark:
            excerpt = p.partial_text[:40].replace("|", "\\|")
            lines.append(f"| {next_mark:.0f} | {p.process_seconds * 1000:.1f} | {excerpt} |")
            next_mark += 5.0
    lines.append("")

    lines.append("## 最終テキスト")
    lines.append("")
    lines.append(f"- Vosk(暫定表示側): {result.final_text or '(空)'}")
    if whisper_reference:
        lines.append(f"- 参考(原文): {whisper_reference}")
    lines.append("")

    lines.append("## 結論(自動判定。目視でも必ず確認すること)")
    lines.append("")
    if rt_ok and trend_ok:
        lines.append(
            "- **方式Bの暫定表示側は成立する。** 1チャンクの処理時間がチャンク長より十分短く、"
            "かつ発話が伸びても処理時間が増えない(方式Aの非線形悪化が起きない)。"
        )
    elif trend_ok and not rt_ok:
        lines.append(
            "- 処理時間は累積しないが、1チャンクの処理がチャンク長に対して重い。"
            "**チャンク長を伸ばす(--chunk-ms)か、より小さいモデルを検討すること。**"
        )
    else:
        lines.append("- **発話が伸びるにつれ処理時間が増えている。方式Bの前提が崩れるため要再検討。**")
    lines.append("")
    lines.append(
        "- Voskの最終テキストは確定結果ではない(確定はWhisperで取り直す)。"
        "ここで見るべきは精度そのものではなく「暫定表示として文字が出続けるか」。"
    )
    lines.append("")

    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="方式B(Vosk暫定表示)のストリーミング成立性を実測する")
    parser.add_argument("--audio", required=True, help="対象wavファイル")
    parser.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR), help=f"Voskモデルのディレクトリ(既定: {DEFAULT_MODEL_DIR})")
    parser.add_argument("--chunk-ms", type=int, default=DEFAULT_CHUNK_MS, help=f"1チャンクの長さ(ms。既定: {DEFAULT_CHUNK_MS})")
    parser.add_argument("--reference", default=None, help="比較用の原文(あればMarkdownに併記する)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力先ディレクトリ")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"エラー: 音声ファイルが存在しません: {audio_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = run_stream(audio_path, Path(args.model_dir), args.chunk_ms)
    markdown = format_markdown(result, args.reference)

    out_path = output_dir / f"vosk_stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(markdown, encoding="utf-8")

    print()
    print(markdown)
    print(f"[vosk_stream_bench] 結果を保存しました: {out_path}")

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
