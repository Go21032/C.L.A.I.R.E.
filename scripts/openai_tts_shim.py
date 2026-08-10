"""
openai_tts_shim.py
----------------------
OpenAI互換TTS API(`POST /v1/audio/speech`)を、VOICEVOX互換API
(VOICEVOX ENGINE / AivisSpeech Engine)へ橋渡しする常駐サーバー。
8日目ノート(サポートAI作製計画/8日目外部アクセス(Tailscale)とSTT・TTSパイプライン.md)
タスク1の部品。

狙い:
  Open WebUIの音声設定(Settings → Audio → Text-to-Speech Engine)は「OpenAI」
  互換のエンドポイントしか選べない。VOICEVOX/AivisSpeechは別APIのため直接は
  繋げない。本shimを間に挟み、Open WebUIには「OpenAI TTSに見せかけて」実際は
  タスク1で確定した声(VOICEVOX互換API)で読み上げさせる。

  9日目に自前音声UIが完成するまでの繋ぎとして、キーボードで打つ普段のチャットも
  良い声で読み上げてもらうためのもの(声の評価自体にOpen WebUIは不要)。
  中身はtts_adapter.pyをそのまま使うので、9日目の自前UIでも捨て仕事にならない。

使い方:
    python openai_tts_shim.py --engine-url http://127.0.0.1:10101 --speaker 107 --port 5051

    Open WebUI側の設定:
        Settings → Audio → Text-to-Speech Engine: OpenAI
        API Base URL: http://127.0.0.1:5051/v1
        API Key: ダミー文字列で可(shim側では認証を検証しない)

    --speed-scale で常時の話速倍率を固定できる(未指定ならVOICEVOX側の既定値)。
    リクエストJSONの"speed"はOpenAI API互換のため受理するが、話速はVOICEVOX側の
    speedScaleで一括制御する設計のため、現状は無視して--speed-scaleを優先する。

エンドポイント:
    POST /v1/audio/speech
        リクエスト(OpenAI互換。使うのは"input"のみ、他フィールドは無視):
            {"model": "tts-1", "input": "読み上げるテキスト",
             "voice": "alloy", "response_format": "mp3", "speed": 1.0}
        レスポンス: 音声データ(wavバイト列、Content-Type: audio/wav)
            ※ response_formatの指定にかかわらず常にwavを返す(mp3変換はしない)。
              ブラウザの<audio>要素はwavを問題なく再生できるため実用上の支障はない。

    GET /healthz
        疎通確認用。{"status": "ok", "engine_url": ...} を返す。

標準ライブラリのみで実装(ollama_client.py / tts_adapter.pyの既存方針を踏襲。
Flask等の追加依存を持ち込まない)。

単体実行する(常駐サーバー。ログは標準出力。Ctrl+Cで停止)。
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tts_adapter import TTSAdapterError, synthesize

DEFAULT_PORT = 5051
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TIMEOUT = 30.0

# ハンドラは1リクエストごとにインスタンス化されるため、設定はモジュールレベルの
# 変数で共有する(argparseの結果をmain()からセットする)。
_ENGINE_URL = ""
_SPEAKER_ID = 0
_SPEED_SCALE: float | None = None
_TIMEOUT = DEFAULT_TIMEOUT


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class TTSShimHandler(BaseHTTPRequestHandler):
    server_version = "openai_tts_shim/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - BaseHTTPRequestHandler互換
        sys.stdout.write(f"[openai_tts_shim] {self.address_string()} - {fmt % args}\n")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler互換
        if self.path.rstrip("/") == "/healthz":
            _send_json(self, 200, {"status": "ok", "engine_url": _ENGINE_URL, "speaker": _SPEAKER_ID})
            return
        _send_json(self, 404, {"error": {"message": f"not found: {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler互換
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/v1/audio/speech", "/audio/speech"):
            _send_json(self, 404, {"error": {"message": f"not found: {self.path}"}})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError as e:
            _send_json(self, 400, {"error": {"message": f"リクエストJSONが不正: {e}"}})
            return

        text = body.get("input") or body.get("text")
        if not text:
            _send_json(self, 400, {"error": {"message": "'input'(読み上げるテキスト)が空です"}})
            return

        try:
            wav_bytes = synthesize(
                _ENGINE_URL,
                text,
                _SPEAKER_ID,
                speed_scale=_SPEED_SCALE,
                timeout=_TIMEOUT,
            )
        except TTSAdapterError as e:
            print(f"[openai_tts_shim] 合成失敗: {e}", file=sys.stderr)
            _send_json(self, 502, {"error": {"message": f"TTSエンジン呼び出しが失敗: {e}"}})
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav_bytes)))
        self.end_headers()
        self.wfile.write(wav_bytes)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenAI互換 /v1/audio/speech をVOICEVOX互換APIへ橋渡しするshimサーバー"
    )
    parser.add_argument(
        "--engine-url",
        required=True,
        help="VOICEVOX互換APIのベースURL(例: http://127.0.0.1:10101)",
    )
    parser.add_argument(
        "--speaker", type=int, required=True, help="確定した話者ID(タスク1で決めた/audio_query等のspeaker値)"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"待受ポート(既定: {DEFAULT_PORT})")
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"待受ホスト(既定: {DEFAULT_HOST}。tailnet外へは絶対に晒さないこと)"
    )
    parser.add_argument(
        "--speed-scale",
        type=float,
        default=None,
        help="話速倍率(1.0が標準)。未指定ならVOICEVOX側の既定値を使う",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="TTSエンジン呼び出しのタイムアウト秒数"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _ENGINE_URL, _SPEAKER_ID, _SPEED_SCALE, _TIMEOUT

    args = parse_args(argv)
    _ENGINE_URL = args.engine_url
    _SPEAKER_ID = args.speaker
    _SPEED_SCALE = args.speed_scale
    _TIMEOUT = args.timeout

    server = ThreadingHTTPServer((args.host, args.port), TTSShimHandler)
    base_url = f"http://{args.host}:{args.port}"
    print(f"[openai_tts_shim] 起動: {base_url}  (engine={_ENGINE_URL}, speaker={_SPEAKER_ID})")
    print(f"[openai_tts_shim] Open WebUI設定 → API Base URL: {base_url}/v1  / API Key: ダミー文字列で可")
    print("[openai_tts_shim] Ctrl+C で停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[openai_tts_shim] 停止します")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
