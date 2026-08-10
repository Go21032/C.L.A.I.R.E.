"""
stt_bench.py
--------------
faster-whisper経由で、素のWhisper(small / medium)と日本語特化モデル(Kotoba-Whisper)の
所要時間・認識結果を同一wavで比較するツール。9日目ノート
(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)③の部品。

狙い:
  9日目②で「faster-whisper + device=cuda」がsm_120で動くことは実証済み。
  ③はその上で「どのモデルを使うか」(認識精度・速度のトレードオフ)と、
  「逐次表示(方式A: 定期再転写)が体感を損なわずに成立するか」を実測で決める。

  Kotoba-Whisperは素のWhisperと同じCTranslate2エンジン(faster-whisper)経由で
  ロードできるCT2変換版を前提にしている(--models kotoba の既定パスは
  MODEL_REGISTRY参照。ローカルにCT2変換済みモデルがある場合は --model-path で上書きする)。

使い方:
    # 同一wavに対し既定の3構成(small/medium/kotoba)を比較する
    python stt_bench.py --audio results/stt_bench/sample01.wav

    # 複数wav・モデルを絞って比較する
    python stt_bench.py --audio-dir results/stt_bench --models small,medium

    # 固有名詞ヒントを与えた場合の差を見る
    python stt_bench.py --audio results/stt_bench/sample01.wav --initial-prompt "C.L.A.I.R.E., LanceDB, Ruri, Tailscale, Obsidian, VOICEVOX"

    # 逐次表示(方式A: 定期再転写)の成立性を測る(1つのwav・1モデルに対して発話5/10/20秒時点を再転写)
    python stt_bench.py --audio results/stt_bench/sample_long.wav --incremental --incremental-model small

出力先:
  通常モード: scripts/results/stt_bench/bench_<日時>.md
  --incremental: scripts/results/stt_bench/incremental_<日時>.md
  (いずれも表形式で保存する。標準出力にも同じ内容を出す)

標準ライブラリ + faster-whisper(検証対象そのもの)のみを使う。
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results" / "stt_bench"

DEFAULT_DEVICE = "cuda"
DEFAULT_COMPUTE_TYPE = "int8_float16"

# 9日目②の実機検証で「device=cuda, compute_type=int8_float16」が通ることを確認済み。
# ここではそれを既定値として使う。CPUで試したい場合は --device cpu --compute-type int8。
MODEL_REGISTRY: dict[str, tuple[str, str]] = {
    # key: (表示用ラベル, faster-whisperへ渡すmodel_size_or_path)
    "small": ("faster-whisper small(素のWhisper)", "small"),
    "medium": ("faster-whisper medium(素のWhisper)", "medium"),
    # Kotoba-WhisperはCTranslate2(faster-whisper)変換版のHF repo idを指定する。
    # ローカルにダウンロード済みのCT2フォルダがあれば --model-path でそちらを優先できる。
    "kotoba": ("Kotoba-Whisper v2.0(日本語特化・CT2変換版)", "kotoba-tech/kotoba-whisper-v2.0-faster"),
}
DEFAULT_MODEL_KEYS = ["small", "medium", "kotoba"]

# ③の作業内容にある固有名詞チェック対象。認識結果に「そのままの綴りで」現れるかを
# 簡易チェックする(Whisperは通常かな/漢字で書き起こすため、ヒットしなくても即NGとは
# 限らない。認識結果全文を必ず目視確認すること)。
PROPER_NOUNS = ["C.L.A.I.R.E.", "LanceDB", "Ruri", "Tailscale", "Obsidian", "VOICEVOX"]

DEFAULT_INCREMENTAL_MARKS = [5.0, 10.0, 20.0]
# 逐次表示の更新間隔として「体感を損なわない」とみなす目安(暫定値)。
# 実際の閾値はUI側の再転写間隔設計と合わせて9日目当日に確定する。
DEFAULT_INCREMENTAL_THRESHOLD_SECONDS = 1.0


class STTBenchError(RuntimeError):
    """モデルのロードや推論に失敗した場合に送出する。"""


@dataclass
class BenchResult:
    model_key: str
    label: str
    device: str
    compute_type: str
    audio_path: str
    audio_seconds: float
    transcribe_seconds: float
    text: str
    language: str
    language_probability: float
    proper_noun_hits: dict[str, bool] = field(default_factory=dict)
    error: str | None = None

    @property
    def real_time_ratio(self) -> float:
        """転写時間 / 音声長。1.0未満なら音声の長さより速く転写できている。"""
        if self.audio_seconds <= 0:
            return float("inf")
        return self.transcribe_seconds / self.audio_seconds

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class IncrementalPoint:
    mark_seconds: float
    transcribe_seconds: float
    text: str
    error: str | None = None

    @property
    def viable(self) -> bool:
        return self.error is None and self.transcribe_seconds < DEFAULT_INCREMENTAL_THRESHOLD_SECONDS


def check_proper_nouns(text: str) -> dict[str, bool]:
    lowered = text.lower()
    return {noun: noun.lower() in lowered for noun in PROPER_NOUNS}


def load_model(model_path: str, device: str, compute_type: str):
    """WhisperModelをロードする。失敗時はSTTBenchErrorへ変換して呼び出し元に理由を残す。"""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise STTBenchError(f"faster-whisperが未インストール: {e}") from e

    try:
        return WhisperModel(model_path, device=device, compute_type=compute_type)
    except Exception as e:  # noqa: BLE001 - ロード失敗理由をそのまま記録するため広く捕捉
        raise STTBenchError(
            f"WhisperModel({model_path!r}, device={device!r}, compute_type={compute_type!r}) のロードに失敗: "
            f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        ) from e


def transcribe_file(model, audio_path: Path, initial_prompt: str | None) -> tuple[str, float, float, str, float]:
    """1ファイルを転写する。戻り値: (認識テキスト, 転写秒数, 音声長秒数, 言語, 言語確度)。"""
    start = time.monotonic()
    segments, info = model.transcribe(
        str(audio_path), language="ja", initial_prompt=initial_prompt
    )
    text = "".join(seg.text for seg in segments)
    elapsed = time.monotonic() - start
    return text.strip(), elapsed, info.duration, info.language, info.language_probability


def run_bench(
    audio_paths: list[Path],
    model_keys: list[str],
    device: str,
    compute_type: str,
    initial_prompt: str | None,
    model_path_overrides: dict[str, str],
) -> list[BenchResult]:
    results: list[BenchResult] = []

    for model_key in model_keys:
        if model_key not in MODEL_REGISTRY:
            print(f"[警告] 未知のモデルキー '{model_key}' はスキップします(既知: {list(MODEL_REGISTRY)})", file=sys.stderr)
            continue
        label, default_path = MODEL_REGISTRY[model_key]
        model_path = model_path_overrides.get(model_key, default_path)

        print(f"[load] {label} ({model_path}, device={device}, compute_type={compute_type}) をロード中...")
        try:
            model = load_model(model_path, device, compute_type)
        except STTBenchError as e:
            print(f"  NG: {e}", file=sys.stderr)
            for audio_path in audio_paths:
                results.append(
                    BenchResult(
                        model_key=model_key,
                        label=label,
                        device=device,
                        compute_type=compute_type,
                        audio_path=str(audio_path),
                        audio_seconds=0.0,
                        transcribe_seconds=0.0,
                        text="",
                        language="",
                        language_probability=0.0,
                        error=str(e),
                    )
                )
            continue

        for audio_path in audio_paths:
            print(f"  [transcribe] {audio_path.name}")
            try:
                text, elapsed, duration, language, lang_prob = transcribe_file(model, audio_path, initial_prompt)
                hits = check_proper_nouns(text)
                r = BenchResult(
                    model_key=model_key,
                    label=label,
                    device=device,
                    compute_type=compute_type,
                    audio_path=str(audio_path),
                    audio_seconds=duration,
                    transcribe_seconds=elapsed,
                    text=text,
                    language=language,
                    language_probability=lang_prob,
                    proper_noun_hits=hits,
                )
                print(
                    f"    transcribe={elapsed:.2f}s audio={duration:.2f}s "
                    f"実時間比={r.real_time_ratio:.2f} text={text[:40]}{'...' if len(text) > 40 else ''}"
                )
            except Exception as e:  # noqa: BLE001 - 1ファイル分の失敗で全体を止めない
                r = BenchResult(
                    model_key=model_key,
                    label=label,
                    device=device,
                    compute_type=compute_type,
                    audio_path=str(audio_path),
                    audio_seconds=0.0,
                    transcribe_seconds=0.0,
                    text="",
                    language="",
                    language_probability=0.0,
                    error=f"{type(e).__name__}: {e}",
                )
                print(f"    NG: {r.error}", file=sys.stderr)
            results.append(r)

    return results


def run_incremental(
    audio_path: Path,
    model_key: str,
    device: str,
    compute_type: str,
    marks: list[float],
    model_path_overrides: dict[str, str],
) -> tuple[str, list[IncrementalPoint], str | None]:
    """方式A(定期再転写)の成立性を測る: 音声の先頭からmarks秒までのプレフィックスを
    切り出し、それぞれ転写にかかる時間を計測する。「発話が伸びるほど毎回の転写が
    重くなる」傾向が実用範囲かを見るのが目的。

    戻り値: (使用したモデルのラベル, 計測点のリスト, 全体エラー(あれば))
    """
    if model_key not in MODEL_REGISTRY:
        return "", [], f"未知のモデルキー '{model_key}'(既知: {list(MODEL_REGISTRY)})"
    label, default_path = MODEL_REGISTRY[model_key]
    model_path = model_path_overrides.get(model_key, default_path)

    try:
        from faster_whisper.audio import decode_audio  # type: ignore
    except ImportError as e:
        return label, [], f"faster-whisperが未インストール: {e}"

    try:
        model = load_model(model_path, device, compute_type)
    except STTBenchError as e:
        return label, [], str(e)

    try:
        waveform = decode_audio(str(audio_path), sampling_rate=16000)
    except Exception as e:  # noqa: BLE001
        return label, [], f"音声デコードに失敗: {type(e).__name__}: {e}"

    total_seconds = len(waveform) / 16000.0
    points: list[IncrementalPoint] = []
    for mark in marks:
        if mark > total_seconds:
            print(f"  [skip] {mark:.0f}秒時点は音声長({total_seconds:.1f}秒)を超えるためスキップ")
            continue
        prefix = waveform[: int(mark * 16000)]
        print(f"  [incremental] 発話{mark:.0f}秒時点までの再転写を計測中...")
        try:
            start = time.monotonic()
            segments, _info = model.transcribe(prefix, language="ja")
            text = "".join(seg.text for seg in segments).strip()
            elapsed = time.monotonic() - start
            points.append(IncrementalPoint(mark_seconds=mark, transcribe_seconds=elapsed, text=text))
            print(f"    転写時間={elapsed:.2f}s  text={text[:40]}{'...' if len(text) > 40 else ''}")
        except Exception as e:  # noqa: BLE001
            points.append(
                IncrementalPoint(mark_seconds=mark, transcribe_seconds=0.0, text="", error=f"{type(e).__name__}: {e}")
            )
            print(f"    NG: {points[-1].error}", file=sys.stderr)

    return label, points, None


def format_markdown_bench(
    results: list[BenchResult],
    device: str,
    compute_type: str,
    initial_prompt: str | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# STTベンチ結果 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append("")
    lines.append(f"- デバイス: `{device}` / compute_type: `{compute_type}`")
    lines.append(f"- initial_prompt: `{initial_prompt if initial_prompt else '(未指定)'}`")
    lines.append("")

    lines.append("## モデル×音声ファイルの結果")
    lines.append("")
    lines.append("| エンジン/モデル | 音声ファイル | 所要時間(秒) | 音声長(秒) | 実時間比 | 認識結果 |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        audio_name = Path(r.audio_path).name
        if not r.ok:
            lines.append(f"| {r.label} | {audio_name} | - | - | - | **NG**: {r.error.splitlines()[0].replace('|', chr(92) + '|')} |")
            continue
        text_escaped = r.text.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r.label} | {audio_name} | {r.transcribe_seconds:.3f} | {r.audio_seconds:.3f} "
            f"| {r.real_time_ratio:.2f} | {text_escaped} |"
        )
    lines.append("")

    lines.append("## 固有名詞の認識(簡易チェック。綴りの完全一致のみを見た機械判定)")
    lines.append("")
    header = "| エンジン/モデル | 音声ファイル | " + " | ".join(PROPER_NOUNS) + " |"
    sep = "|---|---|" + "|".join(["---"] * len(PROPER_NOUNS)) + "|"
    lines.append(header)
    lines.append(sep)
    for r in results:
        if not r.ok:
            continue
        audio_name = Path(r.audio_path).name
        cells = ["OK" if r.proper_noun_hits.get(noun) else "崩れ" for noun in PROPER_NOUNS]
        lines.append(f"| {r.label} | {audio_name} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "> 上表は「認識結果に綴りがそのまま含まれるか」だけを見た機械的なチェック。"
        "Whisperは通常かな/漢字で書き起こすため、崩れ扱いでも実際には音として近い"
        "(例:「ボイスボックス」)場合がある。**上の認識結果全文を必ず目視で確認すること**。"
    )
    lines.append("")

    ok_results = [r for r in results if r.ok]
    ng_results = [r for r in results if not r.ok]
    lines.append("## 結論(自動集計。目視でも必ず確認すること)")
    lines.append("")
    if ok_results:
        by_model: dict[str, list[BenchResult]] = {}
        for r in ok_results:
            by_model.setdefault(r.model_key, []).append(r)
        for model_key, rs in by_model.items():
            avg_ratio = sum(r.real_time_ratio for r in rs) / len(rs)
            lines.append(f"- {rs[0].label}: 平均実時間比 {avg_ratio:.2f}(N={len(rs)})")
    if ng_results:
        lines.append("")
        lines.append("**ロード/転写に失敗したモデルがある:**")
        for r in ng_results:
            lines.append(f"- {r.label}: {r.error.splitlines()[0]}")
    lines.append("")

    return "\n".join(lines)


def format_markdown_incremental(
    audio_path: Path,
    label: str,
    points: list[IncrementalPoint],
    overall_error: str | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# STT逐次表示(方式A)ベンチ結果 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append("")
    lines.append(f"- 音声ファイル: `{audio_path}`")
    lines.append(f"- モデル: {label}")
    lines.append(f"- 成立判定の暫定閾値: 転写時間 < {DEFAULT_INCREMENTAL_THRESHOLD_SECONDS:.1f}秒")
    lines.append("")

    if overall_error:
        lines.append(f"**NG: {overall_error}**")
        lines.append("")
        return "\n".join(lines)

    lines.append("| 発話経過時点(秒) | 再転写所要時間(秒) | 暫定表示の更新間隔として成立 | 認識結果(先頭40字) |")
    lines.append("|---|---|---|---|")
    for p in points:
        if p.error:
            lines.append(f"| {p.mark_seconds:.0f} | - | **NG** | {p.error} |")
            continue
        verdict = "OK" if p.viable else "**NG(遅い)**"
        text_excerpt = p.text[:40].replace("|", "\\|")
        lines.append(f"| {p.mark_seconds:.0f} | {p.transcribe_seconds:.3f} | {verdict} | {text_excerpt} |")
    lines.append("")

    ok_points = [p for p in points if p.error is None]
    lines.append("## 結論(自動判定。目視でも必ず確認すること)")
    lines.append("")
    if not ok_points:
        lines.append("- 有効な計測点がない(全て失敗)。方式Bの検討が必要。")
    elif all(p.viable for p in ok_points):
        lines.append(
            f"- 計測した全ての時点で再転写が{DEFAULT_INCREMENTAL_THRESHOLD_SECONDS:.1f}秒未満で終わっている。"
            "**方式A(定期再転写)が成立する可能性が高い。**"
        )
        lines.append("- ただし閾値は暫定値。UI側の想定再転写間隔と突き合わせて最終判断すること。")
    else:
        slow_marks = [p.mark_seconds for p in ok_points if not p.viable]
        lines.append(
            f"- 発話{slow_marks}秒時点で再転写が閾値({DEFAULT_INCREMENTAL_THRESHOLD_SECONDS:.1f}秒)を超えた。"
            "**発話が伸びると方式Aの更新間隔が体感を損なう可能性がある。方式B(2段構え)を検討すること。**"
        )
    lines.append("")

    return "\n".join(lines)


def _resolve_audio_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if args.audio:
        paths.extend(Path(p) for p in args.audio)
    if args.audio_dir:
        paths.extend(sorted(Path(args.audio_dir).glob("sample*.wav")))
    return [p for p in dict.fromkeys(paths)]  # 重複除去(順序維持)


def _parse_model_path_overrides(raw: list[str] | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in raw or []:
        if "=" not in item:
            print(f"[警告] --model-path は key=path の形式で指定してください(無視: {item})", file=sys.stderr)
            continue
        key, path = item.split("=", 1)
        overrides[key.strip()] = path.strip()
    return overrides


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="faster-whisper経由で複数モデル(素のWhisper/Kotoba-Whisper)の速度・認識結果を比較する"
    )
    parser.add_argument("--audio", action="append", help="対象wavファイル(複数指定可。--audio a.wav --audio b.wav)")
    parser.add_argument("--audio-dir", default=None, help="このディレクトリ内の sample*.wav をまとめて対象にする")
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODEL_KEYS),
        help=f"比較するモデル(カンマ区切り。既知キー: {list(MODEL_REGISTRY)})",
    )
    parser.add_argument(
        "--model-path",
        action="append",
        help="モデルキーのパス/repo idを上書きする(key=path形式。例: kotoba=./local_ct2_model)。複数指定可",
    )
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"faster-whisperのdevice(既定: {DEFAULT_DEVICE})")
    parser.add_argument(
        "--compute-type", default=DEFAULT_COMPUTE_TYPE, help=f"faster-whisperのcompute_type(既定: {DEFAULT_COMPUTE_TYPE})"
    )
    parser.add_argument("--initial-prompt", default=None, help="固有名詞などの語彙ヒント(Whisperのinitial_promptへ渡す)")
    parser.add_argument("--incremental", action="store_true", help="逐次表示(方式A)の成立性を測るモードに切り替える")
    parser.add_argument(
        "--incremental-model", default=DEFAULT_MODEL_KEYS[0], help=f"--incremental時に使う単一モデル(既定: {DEFAULT_MODEL_KEYS[0]})"
    )
    parser.add_argument(
        "--incremental-marks",
        default=",".join(str(m) for m in DEFAULT_INCREMENTAL_MARKS),
        help="再転写を測る発話経過時点(秒、カンマ区切り)",
    )
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力先ディレクトリ(既定: scripts/results/stt_bench)"
    )
    parser.add_argument("--list-models", action="store_true", help="既知のモデルキー一覧を表示して終了する")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list_models:
        for key, (label, path) in MODEL_REGISTRY.items():
            print(f"{key}: {label} (既定パス: {path})")
        return 0

    audio_paths = _resolve_audio_paths(args)
    if not audio_paths:
        print("エラー: --audio または --audio-dir で対象wavを指定してください", file=sys.stderr)
        return 1
    missing = [p for p in audio_paths if not p.exists()]
    if missing:
        print(f"エラー: 存在しない音声ファイルがあります: {missing}", file=sys.stderr)
        return 1

    model_path_overrides = _parse_model_path_overrides(args.model_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.incremental:
        marks = [float(x) for x in args.incremental_marks.split(",") if x.strip()]
        label, points, overall_error = run_incremental(
            audio_paths[0], args.incremental_model, args.device, args.compute_type, marks, model_path_overrides
        )
        markdown = format_markdown_incremental(audio_paths[0], label, points, overall_error)
        out_path = output_dir / f"incremental_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        out_path.write_text(markdown, encoding="utf-8")
        print()
        print(markdown)
        print(f"[stt_bench] 結果を保存しました: {out_path}")
        return 0 if overall_error is None else 1

    model_keys = [k.strip() for k in args.models.split(",") if k.strip()]
    results = run_bench(
        audio_paths, model_keys, args.device, args.compute_type, args.initial_prompt, model_path_overrides
    )
    markdown = format_markdown_bench(results, args.device, args.compute_type, args.initial_prompt)
    out_path = output_dir / f"bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(markdown, encoding="utf-8")

    print()
    print(markdown)
    print(f"[stt_bench] 結果を保存しました: {out_path}")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
