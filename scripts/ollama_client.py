"""
ollama_client.py
-----------------
Ollama REST API(http://localhost:11434)への薄いラッパー。標準ライブラリのみで実装し、
requests等の追加依存を持ち込まない(monitor_ollama.pyの既存方針を踏襲)。

提供する関数:
  - generate(): /api/generate を叩き、生成テキストを1回分(stream=False)取得する。
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
    think: bool | None = None,
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
