"""
tts_voice_survey.py
----------------------
VOICEVOX ENGINE / AivisSpeech Engineの全話者×全スタイルに同一の台詞を読ませ、
聞き比べ用のwavを一括生成するツール。8日目ノート
(サポートAI作製計画/8日目外部アクセス(Tailscale)とSTT・TTSパイプライン.md)タスク1で使う。

使い方:
    python tts_voice_survey.py --engine-url http://127.0.0.1:50021 --label voicevox
    python tts_voice_survey.py --engine-url http://127.0.0.1:10101 --label aivis

    --text で読ませる台詞を変更できる(既定はクレアらしい定型文)。

出力先: scripts/results/voice_survey/<label>/<speaker>_<style>.wav
    (話者名・スタイル名にファイル名として使えない文字が含まれる場合は"_"に置換する)

1話者・1スタイルの合成に失敗しても全体を止めず、エラーを標準エラー出力に記録して
次へ進む(AivisHubから雑多にDLしたモデルの中に、起動直後は未ロードで失敗するものが
混じる可能性があるため)。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from tts_adapter import TTSAdapterError, get_speakers, iter_speaker_styles, synthesize

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = SCRIPT_DIR / "results" / "voice_survey"

DEFAULT_TEXT = "こんにちは、クレアです。今日も一日お疲れさまでした。"

_UNSAFE_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str) -> str:
    """ファイル名として使えない文字を"_"に置換する(Windowsの禁則文字対策)。"""
    cleaned = _UNSAFE_CHARS_RE.sub("_", name).strip()
    return cleaned or "unknown"


def run_survey(
    engine_url: str, label: str, text: str, output_root: Path, timeout: float
) -> tuple[int, int]:
    """指定エンジンの全話者×全スタイルにtextを読ませ、wavを保存する。

    戻り値: (成功件数, 失敗件数)
    """
    print(f"[{label}] {engine_url}/speakers を取得中...")
    speakers = get_speakers(engine_url, timeout=timeout)
    combos = iter_speaker_styles(speakers)
    print(f"[{label}] 話者{len(speakers)}件 / 話者×スタイル{len(combos)}件を検出")

    out_dir = output_root / label
    out_dir.mkdir(parents=True, exist_ok=True)

    ok_count = 0
    ng_count = 0
    for speaker_name, style_name, style_id in combos:
        filename = f"{sanitize_filename(speaker_name)}_{sanitize_filename(style_name)}.wav"
        out_path = out_dir / filename
        try:
            start = time.monotonic()
            wav_bytes = synthesize(engine_url, text, style_id, timeout=timeout)
            elapsed = time.monotonic() - start
            out_path.write_bytes(wav_bytes)
            print(f"  OK  {filename}  ({elapsed:.2f}s, {len(wav_bytes)} bytes)")
            ok_count += 1
        except TTSAdapterError as e:
            print(f"  NG  {filename}  {e}", file=sys.stderr)
            ng_count += 1

    print(f"[{label}] 完了: 成功{ok_count}件 / 失敗{ng_count}件 → {out_dir}")
    return ok_count, ng_count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VOICEVOX互換エンジンの全話者×全スタイルに同一台詞を読ませ、聞き比べ用wavを一括生成する"
    )
    parser.add_argument(
        "--engine-url",
        required=True,
        help="VOICEVOX互換APIのベースURL(例: http://127.0.0.1:50021)",
    )
    parser.add_argument(
        "--label", required=True, help="出力先サブフォルダ名(例: voicevox / aivis)"
    )
    parser.add_argument(
        "--text", default=DEFAULT_TEXT, help="読み上げさせる台詞(既定はクレアらしい定型文)"
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="出力先ルート(既定: scripts/results/voice_survey)",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="1リクエストあたりのタイムアウト秒数"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        ok_count, _ng_count = run_survey(
            engine_url=args.engine_url,
            label=args.label,
            text=args.text,
            output_root=Path(args.output_dir),
            timeout=args.timeout,
        )
    except TTSAdapterError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
