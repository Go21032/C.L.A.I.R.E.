"""
ws_e2e_bench.py
------------------
11日目ノート①ステップ5(サポートAI作製計画/11日目Web検索対応・UIデザイン確定・
マルチモーダル対応調査.md)の自動化。

これまでステップ5は「Chromeでマイクに実際に話しかけ、DevToolsのNetworkタブで
WS `/ws` のフレームを目視で読んで時刻を控える」という手作業だった。このスクリプトは
その代わりに、本物の voice_gateway.py サーバ(FastAPI/WebSocket、実際のVosk +
faster-whisper + VAD + Ollama + VOICEVOXを裏で動かしている)に対してPythonの
WebSocketクライアントとして接続し、録音済みwavを実際のマイクと同じ速度
(100msチャンク、static/index.htmlのSEND_CHUNK_MSと同じペース)でストリーミングし、
サーバから返るメッセージの受信時刻を`time.perf_counter()`で自動記録する。

計測できる区間:
  t_speech_end   = 音声送信完了(wavの実データを送り終えた時刻。「発話終了」の代理値。
                   実際のマイクでは発話後に無音が続くだけなので、このスクリプトも
                   同様に末尾へ無音チャンクを追加してVADの発話終了検知を促す)
  t_stt_final    = `{"type":"partial_transcript","final":true}` を受信した時刻
                   (VAD発話終了検知 → faster-whisperの確定転写完了、の直後)
  seg_speech_end_to_stt_final = t_stt_final - t_speech_end
                   ノート表1・2行目「発話終了→VAD検出」「VAD検出→STT確定」の**合算値**。
                   voice_gateway.py側にVAD検出だけを単独通知するメッセージが無い
                   (vad.VoskEndpointVADの発話終了イベントは、stt_engine.STTEngine
                   ._finalize()の中でfaster-whisperの確定転写へそのまま連続して
                   渡され、同じPython呼び出しの中で完結する)ため、2区間へは分解できない。
                   ノートの表には合算値として記入し、備考にその旨を残すこと。

  さらに、STT確定後は自動で`{"type":"text_input", "text": <確定テキスト>}`を送って
  run_turn()を最後まで走らせ、bench_e2e_latency.pyと同じ項目
  (t_first_token / t_first_sentence / t_first_audio / t_end)も記録する。
  これはCLIベンチ(bench_e2e_latency.py)の値との突合(ノートの「CLI版の結果と突合」)
  を自動でやるためのおまけで、ステップ5の必須要件ではない。

このスクリプトが測るのはあくまで「サーバ(uvicorn)が受け取ってから」の時間であり、
実際のブラウザのマイクデバイス初期化・getUserMedia・ScriptProcessorNodeの処理コスト・
スピーカー再生そのものは含まない。その意味で真の「実機」計測の完全な代替ではないが、
DevTools目視読み取りより遥かに高精度・再現性がある、かつステップ2〜4と同様に
繰り返し実行(--rounds)して平均を取れる。

前提:
  - voice_gateway.py が起動済みであること:
      python voice_gateway.py --host 127.0.0.1 --port 5055
  - 2026-08-12改修版のvoice_gateway.py であること(on_finalが送るpartial_transcriptに
    "final": true が付くようになった改修。古いバージョンのままだとt_stt_finalが
    永遠に検出できずタイムアウトする)
  - Ollama / VOICEVOX ENGINE も起動済み(text_input自動送信後にrun_turn()が
    実際にLLM/TTSを叩くため。bench_e2e_latency.pyと同じ前提)

使い方:
    # 既定のサンプル音声(results/stt_bench/sample01.wav)を1回だけ計測
    python ws_e2e_bench.py

    # 音声ファイル・接続先・繰り返し回数を指定
    python ws_e2e_bench.py --audio results/stt_bench/sample01.wav --url ws://127.0.0.1:5055/ws --rounds 3

    # STT確定の計測だけでよく、そのままrun_turn()まで走らせたくない場合
    python ws_e2e_bench.py --no-auto-continue

    # 結果をJSONに保存(既定でも保存する。パスを変えたい場合)
    python ws_e2e_bench.py --out results/ws_e2e_2026-08-12.json

出力先(既定): scripts/results/ws_e2e_bench/ws_e2e_<日時>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import websockets

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_AUDIO = SCRIPT_DIR / "results" / "stt_bench" / "sample01.wav"
DEFAULT_URL = "ws://127.0.0.1:5055/ws"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results" / "ws_e2e_bench"

TARGET_SAMPLE_RATE = 16000  # voice_gateway.py / stt_engine.py が前提とするレート
CHUNK_MS = 100  # static/index.htmlのSEND_CHUNK_MSと同じペースで送る(③実機比較結果⑥)
DEFAULT_TRAILING_SILENCE_MS = 2000  # VoskEndpointVADが発話終了を検知するための無音パディング
DEFAULT_STT_TIMEOUT_S = 15.0
DEFAULT_TURN_TIMEOUT_S = 90.0


class WsBenchError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 音声の読み込み・16kHz/mono/16bit PCMへの変換
# ---------------------------------------------------------------------------


def _resample_linear(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """static/index.htmlのdownsampleAndEncode()と同じ線形補間方式。

    音質より実装のシンプルさ・依存追加なしを優先する(クライアント側の簡易リサンプルと
    挙動を揃えておいたほうが、実ブラウザ経由の計測との差異が説明しやすいため)。
    """
    if from_rate == to_rate:
        return samples
    ratio = from_rate / to_rate
    new_length = int(round(len(samples) / ratio))
    src_index = np.arange(new_length) * ratio
    i0 = np.floor(src_index).astype(np.int64)
    i1 = np.minimum(i0 + 1, len(samples) - 1)
    frac = src_index - i0
    return samples[i0] * (1 - frac) + samples[i1] * frac


def load_pcm16_16k_mono(path: Path) -> bytes:
    """wavファイルを16kHz/mono/16bit PCMの生バイト列(ヘッダ無し)に変換する。

    voice_gateway.py の `stt.feed_audio(data)` はWAVヘッダを一切解釈せず、
    バイナリWSフレームをそのままPCMとして扱う(static/index.html側もPCM16の
    生バイトを送っている)ため、ここで確実にヘッダを外し、レート/チャンネル数を
    揃えておく必要がある。
    """
    with wave.open(str(path), "rb") as w:
        n_channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        raw = w.readframes(w.getnframes())

    if sampwidth != 2:
        raise WsBenchError(
            f"16bit PCM以外のwavは未対応です(sampwidth={sampwidth}, file={path})"
        )

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    samples = _resample_linear(samples, framerate, TARGET_SAMPLE_RATE)
    samples = np.clip(samples, -32768, 32767).astype("<i2")
    return samples.tobytes()


def chunk_pcm(pcm: bytes, chunk_ms: int) -> list[bytes]:
    chunk_bytes = int(TARGET_SAMPLE_RATE * chunk_ms / 1000) * 2  # 16bit = 2 bytes/sample
    return [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)] or [b""]


# ---------------------------------------------------------------------------
# 1回分の計測
# ---------------------------------------------------------------------------


@dataclass
class BenchRun:
    audio_path: str
    timings: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    stt_text: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "audio_path": self.audio_path,
            "stt_text": self.stt_text,
            "error": self.error,
            **self.timings,
        }


async def run_one(
    url: str,
    audio_path: Path,
    *,
    chunk_ms: int = CHUNK_MS,
    trailing_silence_ms: int = DEFAULT_TRAILING_SILENCE_MS,
    stt_timeout_s: float = DEFAULT_STT_TIMEOUT_S,
    turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S,
    auto_continue: bool = True,
) -> BenchRun:
    pcm = load_pcm16_16k_mono(audio_path)
    speech_chunks = chunk_pcm(pcm, chunk_ms)
    chunk_bytes = len(speech_chunks[0]) or int(TARGET_SAMPLE_RATE * chunk_ms / 1000) * 2
    silence_chunk = b"\x00" * chunk_bytes
    n_silence_chunks = max(1, int(round(trailing_silence_ms / chunk_ms)))
    chunk_interval = chunk_ms / 1000.0

    run = BenchRun(audio_path=str(audio_path))

    try:
        async with websockets.connect(url, max_size=None) as ws:
            t0 = time.perf_counter()
            stt_final_event = asyncio.Event()
            turn_done_event = asyncio.Event()

            async def receiver() -> None:
                try:
                    async for raw in ws:
                        t = time.perf_counter() - t0
                        try:
                            msg = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        run.messages.append({"t": round(t, 4), **msg})

                        mtype = msg.get("type")
                        if mtype == "partial_transcript":
                            if msg.get("final") and run.stt_text is None:
                                run.stt_text = msg.get("text", "")
                                run.timings["t_stt_final"] = t
                                stt_final_event.set()
                        elif mtype == "token" and "t_first_token" not in run.timings:
                            run.timings["t_first_token"] = t
                        elif mtype == "sentence" and "t_first_sentence" not in run.timings:
                            run.timings["t_first_sentence"] = t
                        elif mtype == "audio" and "t_first_audio" not in run.timings:
                            run.timings["t_first_audio"] = t
                        elif mtype == "state" and msg.get("value") == "idle":
                            run.timings.setdefault("t_end", t)
                            turn_done_event.set()
                        elif mtype == "error":
                            run.timings.setdefault(
                                "error_event", f"[{msg.get('stage')}] {msg.get('message')}"
                            )
                except websockets.exceptions.ConnectionClosed:
                    pass

            recv_task = asyncio.create_task(receiver())

            # --- 実際のマイクと同じペースで音声チャンクを送る ---
            for chunk in speech_chunks:
                await ws.send(chunk)
                await asyncio.sleep(chunk_interval)
            run.timings["t_speech_end"] = time.perf_counter() - t0

            # --- VADが発話終了を検知できるよう無音を送り続ける ---
            for _ in range(n_silence_chunks):
                await ws.send(silence_chunk)
                await asyncio.sleep(chunk_interval)

            try:
                await asyncio.wait_for(stt_final_event.wait(), timeout=stt_timeout_s)
            except asyncio.TimeoutError:
                run.timings["stt_final_timeout"] = True

            if auto_continue and run.stt_text:
                run.timings["t_text_input_sent"] = time.perf_counter() - t0
                await ws.send(json.dumps({"type": "text_input", "text": run.stt_text}))
                try:
                    await asyncio.wait_for(turn_done_event.wait(), timeout=turn_timeout_s)
                except asyncio.TimeoutError:
                    run.timings["turn_timeout"] = True

            recv_task.cancel()
            try:
                await recv_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    except OSError as e:
        run.error = (
            f"{type(e).__name__}: {e} -- voice_gateway.pyサーバ({url})に接続できません。"
            "先に `python voice_gateway.py --host 127.0.0.1 --port 5055` を起動してください。"
        )
        return run
    except Exception as e:  # noqa: BLE001 - 1回分の失敗で全体を止めない
        run.error = f"{type(e).__name__}: {e}"
        return run

    # --- 派生区間 ---
    t_speech_end = run.timings.get("t_speech_end")
    t_stt_final = run.timings.get("t_stt_final")
    if t_speech_end is not None and t_stt_final is not None:
        run.timings["seg_speech_end_to_stt_final"] = t_stt_final - t_speech_end
    else:
        run.timings["seg_speech_end_to_stt_final"] = float("nan")

    t_text_input_sent = run.timings.get("t_text_input_sent")

    def _seg_from_send(key: str) -> float:
        v = run.timings.get(key)
        if v is None or t_text_input_sent is None:
            return float("nan")
        return v - t_text_input_sent

    run.timings["seg_first_token_after_send"] = _seg_from_send("t_first_token")
    run.timings["seg_first_sentence_after_send"] = _seg_from_send("t_first_sentence")
    run.timings["seg_first_audio_after_send"] = _seg_from_send("t_first_audio")
    run.timings["seg_total_after_send"] = _seg_from_send("t_end")

    return run


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and v != v):  # NaN self-inequality
        return "   -  "
    if isinstance(v, bool):
        return str(v)
    return f"{v:6.3f}s"


async def _main_async(args: argparse.Namespace) -> int:
    audio_paths = [Path(p) for p in (args.audio or [str(DEFAULT_AUDIO)])]
    missing = [p for p in audio_paths if not p.exists()]
    if missing:
        print(f"エラー: 存在しない音声ファイルがあります: {missing}", file=sys.stderr)
        return 1

    all_runs: list[dict] = []
    for round_idx in range(args.rounds):
        for audio_path in audio_paths:
            print(f"[r{round_idx}] {audio_path.name:30s} -> ", end="", flush=True)
            run = await run_one(
                args.url,
                audio_path,
                chunk_ms=args.chunk_ms,
                trailing_silence_ms=args.trailing_silence_ms,
                stt_timeout_s=args.stt_timeout,
                turn_timeout_s=args.turn_timeout,
                auto_continue=not args.no_auto_continue,
            )
            record = {"round": round_idx, **run.as_dict()}
            if not args.no_messages:
                record["messages"] = run.messages
            all_runs.append(record)

            if run.error:
                print(f"ERROR: {run.error}")
                continue

            print(
                f"speech_end={_fmt(run.timings.get('t_speech_end'))} "
                f"stt_final={_fmt(run.timings.get('t_stt_final'))} "
                f"speech_end→stt_final={_fmt(run.timings.get('seg_speech_end_to_stt_final'))} "
                f"stt_text={run.stt_text!r}"
            )
            if not args.no_auto_continue and run.stt_text:
                print(
                    "         "
                    f"first_token={_fmt(run.timings.get('seg_first_token_after_send'))} "
                    f"first_sentence={_fmt(run.timings.get('seg_first_sentence_after_send'))} "
                    f"first_audio={_fmt(run.timings.get('seg_first_audio_after_send'))} "
                    f"total={_fmt(run.timings.get('seg_total_after_send'))}"
                )

    out_path = Path(args.out) if args.out else DEFAULT_OUTPUT_DIR / f"ws_e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_runs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {len(all_runs)} records to {out_path}")

    ok = [r for r in all_runs if not r.get("error")]
    if ok:
        segs = [r["seg_speech_end_to_stt_final"] for r in ok if r.get("seg_speech_end_to_stt_final") == r.get("seg_speech_end_to_stt_final")]
        if segs:
            avg = sum(segs) / len(segs)
            print(
                f"\n発話終了→STT確定(VAD検出含む合算値)の平均: {avg:.3f}s (N={len(segs)})"
            )
            print(
                "※ ノート表の「発話終了→VAD検出」「VAD検出→STT確定」の2行へは分解できないため、"
                "1行にまとめて記入するか、備考に「合算値」と明記すること。"
            )
    return 0 if all(not r.get("error") for r in all_runs) else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="11日目ノート①ステップ5の自動化: voice_gateway.pyへ実際にwavをストリーミングし、"
        "STT確定(VAD検出込み)とrun_turn()全体の遅延を自動計測する"
    )
    parser.add_argument(
        "--audio",
        action="append",
        help=f"送信するwav(16bit PCM。レート/チャンネル数は自動変換)。複数指定可(既定: {DEFAULT_AUDIO})",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"voice_gateway.pyのWebSocket URL(既定: {DEFAULT_URL})")
    parser.add_argument("--rounds", type=int, default=1, help="繰り返し回数(平均を取るために使う)")
    parser.add_argument("--chunk-ms", type=int, default=CHUNK_MS, help=f"1フレームあたりの送信間隔ms(既定: {CHUNK_MS}. static/index.htmlのSEND_CHUNK_MSと同じ)")
    parser.add_argument(
        "--trailing-silence-ms",
        type=int,
        default=DEFAULT_TRAILING_SILENCE_MS,
        help=f"音声送信後に付け足す無音の長さms(VADの発話終了検知用。既定: {DEFAULT_TRAILING_SILENCE_MS})",
    )
    parser.add_argument("--stt-timeout", type=float, default=DEFAULT_STT_TIMEOUT_S, help="STT確定を待つ最大秒数")
    parser.add_argument("--turn-timeout", type=float, default=DEFAULT_TURN_TIMEOUT_S, help="run_turn()完了(state:idle)を待つ最大秒数")
    parser.add_argument(
        "--no-auto-continue",
        action="store_true",
        help="STT確定後にtext_inputを自動送信せず、STT区間の計測だけで終える",
    )
    parser.add_argument("--no-messages", action="store_true", help="JSON出力から生メッセージ列(messages)を省く(サイズを抑えたい場合)")
    parser.add_argument("--out", default=None, help=f"結果JSONの出力先(既定: {DEFAULT_OUTPUT_DIR}/ws_e2e_<日時>.json)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
