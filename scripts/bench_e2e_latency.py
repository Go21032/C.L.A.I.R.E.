"""
bench_e2e_latency.py
----------------------
11日目①「エンドツーエンド遅延の実測」のためのベンチスクリプト。

既存の voice_gateway.run_turn() を再利用する形で、AI側パイプライン全体の
遅延を工程別に time.perf_counter() で挟んで実測する。STT は省略して
テキスト入力で直接 run_turn() を駆動する(STT 単体の遅延は stt_bench.py で
別途測る想定。9日目③/⑥のSTT構成をまたいで測る意味は薄いため)。

計測対象区間:
  T_total          = 実行開始 → state: idle 受信
  T_first_token    = 実行開始 → 最初の LLM トークン受信
                      (= ルーター判定 + RAG + LLM初トークン)
                      ※ _stream_reply() が yield する debug prefix は除外
  T_first_sentence = 実行開始 → 最初の sentence 受信
                      (= 上記 + 文確定までの生成)
  T_first_audio    = 実行開始 → 最初の audio 受信
                      (= 上記 + 最初のTTS合成)

  seg_route_rag_ttft = T_first_token - 0   (旧来の合算値。後方互換のため残す)
  seg_sentence_buffer = T_first_sentence - T_first_token
  seg_first_tts       = T_first_audio - T_first_sentence
  seg_rest            = T_total - T_first_audio

  2026-08-12改修: 上の seg_route_rag_ttft は「ルーター判定」「RAG検索」
  「LLM初トークン(TTFT)」の3工程が1つに潰れていて、11日目ノート表の
  該当3行(STT確定→ルーター判定完了 / ルーター判定→RAG検索完了 /
  RAG検索→LLM初トークン)を個別に埋められなかった(未計装のまま出荷していた
  不具合)。また CODE/CLARIFY は token イベントが出ないため
  seg_route_rag_ttft が丸ごと NaN になり、ルーター/RAG のコストが
  一切見えなくなっていた。
  そのため router.RouterSession.get_route() と memory_store.retrieve() を
  monkeypatch して個別に計測し、以下を追加で出力する(全ルート共通。
  token イベントの有無に依存しない):
    seg_router    = ルーター判定(get_route)の所要時間
    seg_rag       = RAG検索(memory_store.retrieve)の所要時間
                    (memory_store 無効/対象外routeなら NaN)
    seg_ttft_only = t_first_token - (seg_router + seg_rag相当)
                    (= ensure_model_ready の待ち + 純粋なLLM初トークン。
                     token が出ないroute(CODE/CLARIFY)ではNaN)

  なお「発話終了→VAD検出」「VAD検出→STT確定」の2区間は、このスクリプトが
  STTを意図的に省略している(テキスト入力で直接run_turn()を駆動する)ため
  自動計測の対象外。2026-08-12までは実機のブラウザ経由(11日目ノートのステップ5:
  DevTools Network タブでWSタイムスタンプを読む)で手動計測するしかなかったが、
  同日 `ws_e2e_bench.py` を追加し、voice_gateway.pyサーバへ実際にwavをリアルタイム
  ペースでストリーミングして自動計測できるようにした(VAD検出単独のタイムスタンプは
  無いため2区間の合算値のみ)。手動DevTools計測は自動化前のクロスチェック用として
  引き続き有効。

使い方:
    python bench_e2e_latency.py
    python bench_e2e_latency.py --out results/e2e_2026-08-12.json
    python bench_e2e_latency.py --rounds 3  # 各質問3回ずつ回して平均を取る
    python bench_e2e_latency.py --skip-preflight  # サーバ起動確認をスキップする

前提:
  - Ollama が http://localhost:11434 で起動済み
    (Windows版は通常インストール直後からタスクトレイに常駐しており、
     `ollama serve`を別途手動実行する必要はない。`ollama serve`を打って
     "bind: Only one usage of each socket address..." が出た場合は
     既に起動済みという意味なのでそのまま進めてよい)
  - VOICEVOX ENGINE が http://127.0.0.1:50021 で起動済み
  - 必要なモデル(gemma4-e4b-cpu, gpt-oss:20b, gemma4:26b,
    devstral-small-2:24b)が事前に ollama pull 済みであること
  - voice_gateway.py の FastAPI サーバ(--port 5055)は不要
    (run_turn() をWebSocket/FastAPIを介さず直接importして呼ぶため。
     5055番サーバが必要になるのはノートのステップ5「ブラウザ実機での
     クロスチェック」のときだけ)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import router as router_module  # noqa: E402
from voice_gateway import run_turn  # noqa: E402
from tts_adapter import synthesize as tts_synth  # noqa: E402
from openwebui_pipe import support_ai_auto_pipe  # noqa: E402
from openwebui_pipe.support_ai_auto_pipe import Pipe  # noqa: E402


# ---------------------------------------------------------------------------
# 事前起動確認(preflight): Ollama / VOICEVOX ENGINE が起動していない状態で
# ベンチを走らせると、例外が長いトレースバックとして出るだけで原因が
# わかりにくい(実際に踏んだ実機トラブル)。走り始める前に軽く叩いて、
# 分かりやすいメッセージで止める。
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def _http_ok(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        # 404等でもサーバ自体は応答しているので起動確認としてはOK扱い
        return True, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def preflight_check(tts_url: str) -> list[str]:
    """起動確認を行い、失敗したチェックのメッセージ一覧を返す(空なら全OK)。"""
    problems: list[str] = []

    ok, detail = _http_ok(f"{DEFAULT_OLLAMA_URL}/api/tags")
    if not ok:
        problems.append(
            f"Ollama ({DEFAULT_OLLAMA_URL}) に接続できません: {detail}\n"
            f"  → タスクトレイのOllamaが起動しているか確認するか、`ollama serve`を実行してください。"
        )

    ok, detail = _http_ok(f"{tts_url.rstrip('/')}/speakers")
    if not ok:
        problems.append(
            f"VOICEVOX ENGINE ({tts_url}) に接続できません: {detail}\n"
            f"  → run.exe(VOICEVOX ENGINE)を起動してください。"
        )

    return problems


# 各ルートの代表質問。意図的に "単純な質問" / "調べもの" / "コード依頼" /
# "曖昧な質問" を当て、ルーティングが意図どおりに振れるかも合わせて確認する。
# (CLARIFY は聞き返し文生成がPhi-4-mini(gemma4-e4b-cpu)側で完結するため
#  軽量モデルの基準値としても機能する)
QUESTIONS: dict[str, list[str]] = {
    "FAST":    ["今何時?", "1+1は?"],
    "DEEP":    ["ずん子について教えて", "東北ずん子の魅力をまとめて"],
    "CODE":    ["PythonでFizzBuzzを書いて", "このコードのバグを見つけて"],
    "CLARIFY": ["あれ教えて", "よくわからないやつ"],
}

# _stream_reply() が Valves.show_route_debug_prefix=True の場合に yield する
# debug prefix のフォーマット。LLM の出力ではないため TTFT 計測から除外する
# (除外しないと ensure_model_ready / generate_stream より前の時刻で記録されてしまい、
#  TTFT がほぼ0秒という誤った値になる)。
DEBUG_PREFIX_PREFIX = "[route: "

DEFAULT_TTS_URL = os.environ.get("TTS_ENGINE_URL", "http://127.0.0.1:50021")
DEFAULT_TTS_SPEAKER = int(os.environ.get("TTS_SPEAKER_ID", "107"))


# ---------------------------------------------------------------------------
# ルーター判定(get_route) / RAG検索(memory_store.retrieve)の個別計測
# ---------------------------------------------------------------------------
# Pipe.pipe()はブラックボックスとして呼ぶしかない(run_turn()経由で叩くのが
# このスクリプトの設計方針)ため、内部の工程別時間を取り出すには
# router.RouterSession.get_route / memory_store.retrieve をmonkeypatchして
# 呼び出し時刻を記録するしかない。measure_one()はシングルスレッドで
# 1問ずつ順番に処理するため、グローバルな_TIMING_STATEを使い回して問題ない。

_TIMING_STATE: dict[str, float] = {}

_orig_get_route = router_module.RouterSession.get_route


def _timed_get_route(self, session_id, text, call_model):  # noqa: ANN001
    t0 = time.perf_counter()
    try:
        return _orig_get_route(self, session_id, text, call_model)
    finally:
        _TIMING_STATE["router_dur"] = time.perf_counter() - t0


router_module.RouterSession.get_route = _timed_get_route

if support_ai_auto_pipe.memory_store is not None:
    _orig_retrieve = support_ai_auto_pipe.memory_store.retrieve

    def _timed_retrieve(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        t0 = time.perf_counter()
        try:
            return _orig_retrieve(*args, **kwargs)
        finally:
            _TIMING_STATE["rag_dur"] = time.perf_counter() - t0

    support_ai_auto_pipe.memory_store.retrieve = _timed_retrieve
    MEMORY_STORE_AVAILABLE = True
else:
    MEMORY_STORE_AVAILABLE = False


def measure_one(
    pipe: Pipe,
    chat_id: str,
    question: str,
    *,
    tts_url: str,
    tts_speaker: int,
) -> dict:
    """1質問分の遅延を測定する。

    voice_gateway.run_turn() を直接駆動(run_turn は WebSocket/FastAPIに
    依存しない純粋ジェネレータ)し、受け取るイベントの種別ごとに
    タイムスタンプを取る。run_turn 内のロジックには一切手を入れない。
    """
    _TIMING_STATE.clear()
    timings: dict[str, float] = {"t0": time.perf_counter()}
    seen: dict[str, bool] = {"token": False, "sentence": False, "audio": False}
    events_log: list[str] = []

    def synth(text: str) -> bytes:
        # synth が呼ばれる = 対応する sentence の TTS 合成が始まった瞬間。
        # 完了時刻 (t_first_audio) を記録する。
        if not seen["audio"]:
            timings["t_first_audio"] = time.perf_counter() - timings["t0"]
            seen["audio"] = True
        return tts_synth(tts_url, text, tts_speaker)

    for event in run_turn(pipe, chat_id, question, synthesize=synth):
        events_log.append(event["type"])
        etype = event["type"]

        if etype == "token" and not seen["token"]:
            text = event.get("text", "")
            # debug prefix は LLM の出力ではないため除外
            if text.startswith(DEBUG_PREFIX_PREFIX):
                continue
            timings["t_first_token"] = time.perf_counter() - timings["t0"]
            seen["token"] = True

        elif etype == "sentence" and not seen["sentence"]:
            timings["t_first_sentence"] = time.perf_counter() - timings["t0"]
            seen["sentence"] = True

        elif etype == "audio" and not seen["audio"]:
            # synth 内で先に記録済みだが、念のためここでも記録(run_turn 経由の
            # audio メッセージ到着時刻を別経路で残しておくと、片方の
            # 計測が漏れた場合の保険になる)。
            timings.setdefault("t_first_audio", time.perf_counter() - timings["t0"])
            seen["audio"] = True

        elif etype == "state" and event.get("value") == "idle":
            timings["t_end"] = time.perf_counter() - timings["t0"]

        elif etype == "error":
            timings.setdefault(
                "error", f"[{event.get('stage')}] {event.get('message')}"
            )

    if "t_end" not in timings:
        timings["t_end"] = time.perf_counter() - timings["t0"]

    # 派生値: 工程別区間(秒)
    t_first_token = timings.get("t_first_token")
    t_first_sentence = timings.get("t_first_sentence")
    t_first_audio = timings.get("t_first_audio")
    t_end = timings["t_end"]

    def _seg(a: float | None, b: float | None) -> float:
        # 該当区間が欠けるルート(例: CODE/CLARIFY は token が来ない)では NaN
        if a is None or b is None:
            return float("nan")
        return b - a

    timings["seg_route_rag_ttft"] = _seg(timings["t0"], t_first_token)
    timings["seg_sentence_buffer"] = _seg(t_first_token, t_first_sentence)
    timings["seg_first_tts"] = _seg(t_first_sentence, t_first_audio)
    timings["seg_rest"] = _seg(t_first_audio, t_end)

    # 2026-08-12改修: ルーター判定/RAG検索を個別の区間として切り出す
    # (11日目ノート表の「STT確定→ルーター判定完了」「ルーター判定→RAG検索完了」
    # 「RAG検索→LLM初トークン(TTFT)」の3行に対応)。
    # monkeypatch経由で計測しているため、token イベントが出ない CODE/CLARIFY でも
    # router_dur は取れる(rag_durはCLARIFYのように_recall自体を呼ばないrouteではNaN)。
    router_dur = _TIMING_STATE.get("router_dur")
    rag_dur = _TIMING_STATE.get("rag_dur")
    timings["seg_router"] = router_dur if router_dur is not None else float("nan")
    timings["seg_rag"] = rag_dur if rag_dur is not None else float("nan")
    if t_first_token is not None and router_dur is not None:
        known = router_dur + (rag_dur or 0.0)
        timings["seg_ttft_only"] = t_first_token - known
    else:
        timings["seg_ttft_only"] = float("nan")

    return {
        "chat_id": chat_id,
        "question": question,
        "events_count": len(events_log),
        "events_log": events_log,
        **timings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="11日目①: エンドツーエンド遅延ベンチ"
    )
    parser.add_argument(
        "--out",
        default=str(SCRIPT_DIR / "results" / "e2e_latency_latest.json"),
        help="結果JSONの出力先(既定: scripts/results/e2e_latency_latest.json)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="各ルート・各質問の繰り返し回数(平均値を取るために使う)",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Ollama/VOICEVOX ENGINEの起動確認をスキップする(通常は不要)",
    )
    args = parser.parse_args()

    if not args.skip_preflight:
        problems = preflight_check(DEFAULT_TTS_URL)
        if problems:
            print("[preflight] 起動確認でエラーが見つかりました。ベンチを開始できません:\n")
            for p in problems:
                print(f"- {p}\n")
            return 1
        print("[preflight] Ollama / VOICEVOX ENGINE の起動確認OK")

    pipe = Pipe()
    all_results: list[dict] = []
    for round_idx in range(args.rounds):
        for route, questions in QUESTIONS.items():
            for q in questions:
                chat_id = f"bench-{route}-r{round_idx}-{int(time.time())}"
                print(f"[r{round_idx}][{route}] {q[:30]:30s} -> ", end="", flush=True)
                try:
                    r = measure_one(
                        pipe, chat_id, q,
                        tts_url=DEFAULT_TTS_URL,
                        tts_speaker=DEFAULT_TTS_SPEAKER,
                    )
                except Exception as e:  # noqa: BLE001 - 1件失敗で全体停止させない
                    print(f"ERROR: {type(e).__name__}: {e}")
                    continue
                all_results.append({"round": round_idx, "route": route, **r})

                def _fmt(v: float) -> str:
                    # NaN (math.nan) は自分自身との比較で True になるので
                    # 「計測できなかった」を判別できる
                    return "   - " if v != v else f"{v:5.2f}s"

                router_t = r.get("seg_router", float("nan"))
                rag_t = r.get("seg_rag", float("nan"))
                ttft_only = r.get("seg_ttft_only", float("nan"))
                sbuf = r.get("seg_sentence_buffer", float("nan"))
                stts = r.get("seg_first_tts", float("nan"))
                srest = r.get("seg_rest", float("nan"))
                tend = r["t_end"]
                print(
                    f"router={_fmt(router_t)} "
                    f"rag={_fmt(rag_t)} "
                    f"ttft={_fmt(ttft_only)} "
                    f"sent_buf={_fmt(sbuf)} "
                    f"first_TTS={_fmt(stts)} "
                    f"rest={_fmt(srest)} "
                    f"total={tend:5.2f}s"
                )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nSaved {len(all_results)} records to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())