"""
tts_adapter.py
----------------
VOICEVOX ENGINE / AivisSpeech Engine 共通のHTTPアダプタ。
8日目ノート(サポートAI作製計画/8日目外部アクセス(Tailscale)とSTT・TTSパイプライン.md)
タスク1の部品。

両エンジンはVOICEVOX互換のHTTP APIを実装しているため、engine_url(ポート番号)を
差し替えるだけで同じコードで話せる(VOICEVOX: http://127.0.0.1:50021 /
AivisSpeech: http://127.0.0.1:10101)。

提供する関数:
  - get_speakers(): GET /speakers を叩き、話者一覧(name/speaker_uuid/styles)を取得する。
  - iter_speaker_styles(): get_speakers()の結果を (話者名, スタイル名, スタイルID) の
    フラットな一覧に変換する(全話者×全スタイルを列挙したい場合に使う)。
  - audio_query(): POST /audio_query を叩き、音声合成用クエリを取得する。
  - synthesize(): テキスト→wavバイト列を生成する(audio_query → synthesis の2段呼び出し)。

標準ライブラリのみで実装(ollama_client.pyの既存方針を踏襲。requests等を追加しない)。

単体実行はしない(他スクリプトからimportして使う部品)。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 30.0


class TTSAdapterError(RuntimeError):
    """VOICEVOX互換API呼び出しが失敗した場合に送出する。"""


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise TTSAdapterError(f"{method} {url} が失敗(HTTP {e.code}): {body}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise TTSAdapterError(f"{method} {url} が失敗: {e}") from e


def get_speakers(engine_url: str, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """GET /speakers を叩き、話者一覧を返す。

    戻り値の各要素はVOICEVOX互換APIの形式そのまま:
      {"name": str, "speaker_uuid": str,
       "styles": [{"name": str, "id": int, "type": str}, ...], "version": str}
    """
    url = f"{engine_url.rstrip('/')}/speakers"
    raw = _request(url, timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise TTSAdapterError(f"GET {url} のレスポンスがJSONとして不正: {e}") from e


def iter_speaker_styles(speakers: list[dict]) -> list[tuple[str, str, int]]:
    """get_speakers()の結果を (話者名, スタイル名, スタイルID) のフラットな一覧に変換する。"""
    result: list[tuple[str, str, int]] = []
    for speaker in speakers:
        speaker_name = speaker.get("name", "unknown")
        for style in speaker.get("styles", []):
            result.append((speaker_name, style.get("name", "unknown"), style["id"]))
    return result


def audio_query(
    engine_url: str, text: str, speaker_id: int, timeout: float = DEFAULT_TIMEOUT
) -> dict:
    """POST /audio_query を叩き、音声合成用クエリ(音高・話速等のパラメータ)を取得する。"""
    params = urllib.parse.urlencode({"text": text, "speaker": speaker_id})
    url = f"{engine_url.rstrip('/')}/audio_query?{params}"
    raw = _request(url, method="POST", timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise TTSAdapterError(f"POST {url} のレスポンスがJSONとして不正: {e}") from e


def synthesize(
    engine_url: str,
    text: str,
    speaker_id: int,
    speed_scale: float | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    """テキストをwavバイト列に変換する(audio_query → synthesis の2段呼び出し)。

    speed_scale: 話速の倍率(1.0が標準)。指定するとaudio_queryで得たクエリの
    "speedScale"を書き換えてからsynthesisへ渡す。Noneならクエリの既定値のまま。
    """
    query = audio_query(engine_url, text, speaker_id, timeout=timeout)
    if speed_scale is not None:
        query["speedScale"] = speed_scale

    params = urllib.parse.urlencode({"speaker": speaker_id})
    url = f"{engine_url.rstrip('/')}/synthesis?{params}"
    data = json.dumps(query).encode("utf-8")
    return _request(
        url,
        method="POST",
        data=data,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
