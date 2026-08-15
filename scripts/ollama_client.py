"""
ollama_client.py
-----------------
Ollama REST API(http://localhost:11434)への薄いラッパー。標準ライブラリのみで実装し、
requests等の追加依存を持ち込まない(monitor_ollama.pyの既存方針を踏襲)。

提供する関数:
  - generate(): /api/generate を叩き、生成テキストを1回分(stream=False)取得する。
    11日目④-1: `images`引数(base64エンコード済み画像のリスト)を渡せる
    (Ollamaのvision対応モデル向け。未指定なら従来どおりテキストのみのリクエストで、
    既存呼び出し元(router.py等)の挙動は変えない)。
  - generate_stream(): /api/generate を stream=True で叩き、トークンを逐次yieldする。
  - list_running_models(): /api/ps を叩き、現在ロード済みのモデル名一覧を取得する。
  - stop_model(): `ollama stop <model>` をCLI経由で実行する(/api/generateに
    keep_alive=0を送る方法もあるが、モデルがロードされていない場合のエラーを
    避けるため、monitor_ollama.pyと同じCLI経由の方式に統一する)。
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from typing import Iterator

DEFAULT_HOST = "http://localhost:11434"


class OllamaError(RuntimeError):
    """Ollama API呼び出しが失敗した場合に送出する。"""


def generate(
    model: str,
    prompt: str,
    system: str | None = None,
    host: str = DEFAULT_HOST,
    keep_alive: int | str = -1,
    timeout: float = 60.0,
    options: dict | None = None,
    think: bool | str | None = None,
    images: list[str] | None = None,
) -> str:
    """/api/generate を1回だけ叩き、レスポンステキストを返す(stream=False)。

    options: Ollamaの生成パラメータ(temperature等)をそのまま渡す辞書。
    未指定ならOllama側のモデル既定値(通常temperature=0.8)が使われ、
    呼び出しごとに出力がぶれる(router.pyの分類のような決定性が欲しい
    用途ではoptions={"temperature": 0}等を明示すること。6日目ノート
    「マツコ問題」の根本原因の対応)。

    think: Ollama /api/generate のトップレベル`think`フィールド。
    gemma4:e2b等、既定でthinkingモード(内部CoT)が有効なモデルを
    think:false固定で呼び出したい場合に指定する(7日目⓪)。
    Noneなら`think`フィールド自体を送らず、モデルの既定値に委ねる。

    images: base64エンコード済み画像(データURLのprefix無し、生のbase64文字列)のリスト。
    11日目④-1の検討事項どおり、`gemma4-e4b-cpu`/`gemma4:26b`は`/api/show`で
    `capabilities`に`vision`が含まれることを確認済み(vision_bench.py参照)。
    Noneなら`images`フィールド自体を送らず、既存呼び出し元(router.py等)の
    リクエストボディは一切変わらない(後方互換)。
    """
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive,
    }
    if system:
        body["system"] = system
    if options:
        body["options"] = options
    if think is not None:
        body["think"] = think
    if images:
        body["images"] = images

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(f"Ollama /api/generate 呼び出し失敗(model={model}): {e}") from e
    except json.JSONDecodeError as e:
        raise OllamaError(f"Ollama /api/generate のレスポンスがJSONとして不正: {e}") from e

    return payload.get("response", "")


def generate_stream(
    model: str,
    prompt: str,
    system: str | None = None,
    host: str = DEFAULT_HOST,
    keep_alive: int | str = -1,
    timeout: float = 60.0,
    options: dict | None = None,
    think: bool | str | None = None,
    images: list[str] | None = None,
) -> Iterator[str]:
    """/api/generate を stream=True で叩き、生成トークンを届いた順にyieldする。

    9日目ノート④に対応。`generate()`は生成が完全に終わるまで戻ってこないため、
    「1文できた時点でTTSへ渡す」文単位パイプライン(⑤)が原理的に組めなかった。
    本関数はOllamaが返すNDJSON(1行1チャンクのJSON)を1行ずつ読み、
    `response`フィールドをそのままyieldする。

    引数は`generate()`と同じものを同じ意味で受け取る(呼び出し側が
    `generate` ⇔ `generate_stream` を差し替えるだけで済むようにするため)。

    ■ 接続はこの関数を呼んだ時点で開く(遅延ジェネレータにしない)
    `def ... yield`の素のジェネレータにするとリクエスト送信自体が最初の
    `next()`まで遅延し、Ollamaが停止していても呼び出し側のtry/exceptが
    素通りしてしまう(=`generate()`と例外の出方が変わる)。それを避けるため、
    接続確立までを本関数で行い、読み取りループだけを内部ジェネレータに委ねる。

    ■ 例外は`generate()`と同じく`OllamaError`に統一する
    接続失敗・タイムアウト・不正JSON・チャンク内の`error`フィールドの
    いずれもOllamaErrorとして送出する。ただし読み取り途中で起きた失敗は
    (性質上)最初の呼び出しではなくイテレート中に送出される。

    images: `generate()`と同じ意味(base64エンコード済み画像のリスト)。
    11日目④-1「画像添付時はDEEPへ強制ルーティング」の実装で、DEEPルートは
    通常ストリーミング応答のため`generate()`だけでなく本関数にも同じ引数を追加した。
    Noneなら`images`フィールド自体を送らず、既存呼び出し元の挙動は変わらない。
    """
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": keep_alive,
    }
    if system:
        body["system"] = system
    if options:
        body["options"] = options
    if think is not None:
        body["think"] = think
    if images:
        body["images"] = images

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(f"Ollama /api/generate 呼び出し失敗(model={model}): {e}") from e

    return _iter_ndjson_response(resp, model)


def _iter_ndjson_response(resp, model: str) -> Iterator[str]:
    """stream=True のレスポンスを1行ずつ読み、`response`フィールドをyieldする。

    呼び出し側が途中でイテレートをやめた場合(音声UIで割り込みが入った等)でも
    ソケットを閉じ切るため、finallyでclose()する。
    """
    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as e:
                raise OllamaError(
                    f"Ollama /api/generate のストリーム行がJSONとして不正(model={model}): {e}"
                ) from e

            error = chunk.get("error")
            if error:
                raise OllamaError(
                    f"Ollama /api/generate がエラーを返しました(model={model}): {error}"
                )

            piece = chunk.get("response")
            if piece:
                yield piece
            if chunk.get("done"):
                break
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(
            f"Ollama /api/generate のストリーム読み取りが中断されました(model={model}): {e}"
        ) from e
    finally:
        resp.close()


def list_running_models(host: str = DEFAULT_HOST, timeout: float = 10.0) -> list[str]:
    """/api/ps を叩き、現在ロード済みのモデル名一覧を返す。"""
    req = urllib.request.Request(f"{host}/api/ps", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(f"Ollama /api/ps 呼び出し失敗: {e}") from e
    except json.JSONDecodeError as e:
        raise OllamaError(f"Ollama /api/ps のレスポンスがJSONとして不正: {e}") from e

    return [m["name"] for m in payload.get("models", [])]


def stop_model(model: str, timeout: float = 20.0) -> None:
    """`ollama stop <model>` をCLI経由で実行する(失敗しても例外にはしない=
    停止対象がそもそも起動していないケースを許容するため)。"""
    subprocess.run(
        ["ollama", "stop", model],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
