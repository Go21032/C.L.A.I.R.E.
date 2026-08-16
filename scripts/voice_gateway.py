r"""
voice_gateway.py
------------------
9日目ノート(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)
⑥「自前音声UI(案A′)」の本体。FastAPI + WebSocketで、ブラウザのマイク音声を
受け取り、STT(②③) → LLM(④のストリーミング) → 文分割(⑤) → TTS(8日目)を
1本のパイプラインでつなぎ、文が確定するそばから音声チャンクをブラウザへ返す。

8日目で決めた**案A′**(このゲートウェイが`support_ai_auto_pipe.Pipe`を直import
して`pipe()`を呼ぶ。Open WebUIのHTTPを経由しない)で実装する。⑥の作業内容
「案A′の実証」は`python -c "...from support_ai_auto_pipe import Pipe; ..."`で
別途確認済み(このファイルはその確認後に書く、という本ノートの依存順どおり)。

構成(このファイルが担う役割):
    [ブラウザ] --WebSocket(バイナリ=PCM音声 / テキスト=JSON制御)--> [voice_gateway.py]
       stt_engine.STTEngine.feed_audio() で暫定/確定テキストを得る
         → 確定したら run_turn() で Pipe.pipe() を直呼びし、
           トークン列を sentence_splitter で文に切り、1文ずつ tts_adapter.synthesize()
       すべてWSメッセージとしてブラウザへ順次push(音声はbase64)

WSメッセージ仕様(JSON。サーバ → クライアント):
    {"type": "partial_transcript", "text": str, "final": bool}   # STT暫定/確定(Vosk/faster-whisper)。
                                                                    # AIへは渡さず、クライアント側の
                                                                    # テキスト入力欄をリアルタイムに更新するだけ。
                                                                    # "final"は2026-08-12追加: on_partial(Vosk暫定)
                                                                    # では常にFalse、on_final(faster-whisper確定。
                                                                    # VADが発話終了を検知した直後)ではTrueになる。
                                                                    # クライアント(static/index.html)は既存どおり
                                                                    # msg.textしか見ないため後方互換。追加した狙いは
                                                                    # 11日目ノート⑤の自動計測(ws_e2e_bench.py)が
                                                                    # 「暫定表示の更新」と「VAD+STT確定の瞬間」を
                                                                    # 外部から区別できるようにするため
                                                                    # (この2つは元々同じmessage typeで区別不能だった)。
    {"type": "final_transcript", "text": str}                    # 実際にAIへ処理させる、確定・送信済みの発話
                                                                    # (テキスト入力欄で送信ボタン/Enterを押した内容。
                                                                    #  画面上部のログにユーザー発言として表示される)
    {"type": "token", "text": str}                                # LLM生成トークン(逐次)
    {"type": "sentence", "text": str}                             # 確定した1文
    {"type": "audio", "text": str, "wav_b64": str}                # 1文ぶんのTTS wav(base64)
    {"type": "state", "value": "listening"|"thinking"|"speaking"|"idle"}
    {"type": "error", "stage": str, "message": str}

WSメッセージ仕様(クライアント → サーバ。既存はバイナリ音声フレームのみ):
    {"type": "text_input", "text": str, "images": [str, ...], "attached_document": str, "web_search": bool}
                                                                    # テキスト入力欄の送信ボタン/Enter。
                                                                    # 音声由来・キーボード入力由来を問わず、
                                                                    # ユーザーが内容を確認・確定した発話はすべてここを通る。
                                                                    # "images"(11日目④-1で追加)は任意。
                                                                    # base64エンコード済み画像(data URL prefix無し)の
                                                                    # リストで、指定するとそのターンだけルーターを
                                                                    # 経由せず強制的にDEEP(gemma4:26b)へルーティング
                                                                    # される(run_turn()→support_ai_auto_pipe.Pipe参照)。
                                                                    # 📎の文書添付(/documents)と異なりDBへの永続登録はしない。
                                                                    # "attached_document"(13日目「直近添付ファイルを
                                                                    # 自動優先」対応で追加)は任意。直近📎アップロード
                                                                    # したファイル名を1回だけ同梱すると、そのターンの
                                                                    # 記憶検索がそのファイルへ優先的に絞り込まれる
                                                                    # (support_ai_auto_pipe.Pipe._recall()参照)。
                                                                    # "web_search"(14日目。13日目④で部品実装のみ
                                                                    # だったweb_search.pyを結線)は任意。Trueのとき、
                                                                    # support_ai_auto_pipe.Pipeがそのターンだけ
                                                                    # SearXNG検索を行い、結果をsystem文脈へ差し込んで
                                                                    # 応答末尾に出典(タイトル+URL)を添える。
    {"type": "cancel_turn"}                                       # 17日目追加。進行中のターン(応答生成)を
                                                                    # 打ち切る要求。サーバは`cancel_event`をセットし、
                                                                    # run_turn()がOllamaへのストリーム接続を閉じて
                                                                    # 応答生成そのものを打ち切る(GPU計算も止まる。
                                                                    # 単なるクライアント側の表示無視ではない)。
                                                                    # 進行中のターンが無ければ無視される。

HTTP エンドポイント(11日目④: PDF/Word/Excel/PowerPointのナレッジ取り込み。WSとは別立て):
    POST   /documents            multipart/form-data(file)。抽出→チャンク化→LanceDBへ永続登録。
                                  戻り値 {"filename": str, "chunks": int}
    GET    /documents            登録済みナレッジの一覧([{"filename","chunks","date"}, ...])
    DELETE /documents/{filename} 指定ファイル名のナレッジを削除。戻り値 {"filename","deleted"}
    実体は rag_memory/doc_ingest.py。画像は対象外(11日目ノートに追記した検討事項を参照)。

10日目ノート(サポートAI作製計画/10日目ウェイクワード・キーボード入力対応.md)で、
「AIが考えている間に次の発言をすると誤って次ターンとして処理される」不具合の
再発防止として、当初②ウェイクワード方式(Hey Siri型。「ねえクレア」と言わない限り
コマンド化しない)を実装したが、実機で使ってみたところ「無視されても気づけない」
「聞き取り結果がそのまま消えてしまう」という体験の悪さが判明した。そのため⑦で
ウェイクワード自動送信を全面的に撤回し、**音声認識結果(暫定・確定を問わず)は常に
テキスト入力欄へリアルタイムに反映するだけにとどめ、ユーザーが内容を確認・修正して
送信ボタン/Enterを押した時だけAIへ処理させる**方式に変更した(詳細はノート⑦参照。
`wake_word.py`/`decide_wake_action()`は不要になったため削除した)。

`run_turn()`はWebSocket/FastAPIに一切依存しない純粋なジェネレータにしてあり、
`tests/test_voice_gateway.py`でPipe/TTSをフェイクに差し替えてロジック
(トークン→文→音声の順序、エラー時にUIが無反応にならないこと)を検証している。
WebSocketハンドラ自体・ブラウザからの実際のマイク入力・実際のPipe/Vosk/faster-whisper/
VOICEVOXを組み合わせた通しの動作は自動テストの対象外(⑥残課題。実機確認が必要)。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
OPENWEBUI_PIPE_DIR = SCRIPT_DIR / "openwebui_pipe"
if str(OPENWEBUI_PIPE_DIR) not in sys.path:
    sys.path.insert(0, str(OPENWEBUI_PIPE_DIR))
# 11日目④: PDF/Word/Excel/PowerPoint取り込み(doc_ingest.py)は rag_memory/ 配下にある
# (memory_store.py/chunker.pyと同じ場所。config.yamlのパス解決を共有するため)。
RAG_MEMORY_DIR = SCRIPT_DIR / "rag_memory"
if str(RAG_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_MEMORY_DIR))

import code_executor  # noqa: E402 - 14日目③: /artifacts系のパス解決(resolve_safe_path)に使う
import google_workspace  # noqa: E402 - 14日目④: /google系のGoogle Docs/Sheets/Drive出力
from router import ROUTER_MODEL  # noqa: E402 - 14日目③:タイトル要約に使う常駐・軽量モデル
from sentence_splitter import SentenceSplitter  # noqa: E402
from session_store import Session, SessionStore  # noqa: E402 - 14日目②:チャット履歴の永続化
from wake_word import detect_wake_word  # noqa: E402 - 13日目②:ウェイクワードの送信ゲート判定

# fastapi/uvicornはcreate_app()/main()でのみ必要(run_turn()単体のユニットテストは
# これらを一切importしない)。ただしFastAPIのWebSocketルートは`from __future__ import
# annotations`環境下で`get_type_hints()`により型ヒントを実行時解決するため、
# `WebSocket`等をcreate_app()内でローカルimportすると「モジュールのグローバル名前空間に
# 存在しない」としてNameErrorになり、accept()の前に例外がexception middlewareへ
# 飲み込まれて**無言でWebSocketが閉じる**という実バグを踏んだ(実機確認の過程で発見)。
# そのため、これらは他のスクリプトのimport順の慣習(標準ライブラリ/検証対象を先頭でimport)
# 通りモジュールトップレベルでimportする。fastapi未インストール環境でも
# `run_turn()`は単体で使いたいことがあるため、失敗時はNoneのままにしてcreate_app()側で
# 分かりやすいエラーを出す。
try:
    from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover - fastapi未インストール環境でのrun_turn単体利用向け
    FastAPI = File = HTTPException = UploadFile = WebSocket = WebSocketDisconnect = FileResponse = StaticFiles = None  # type: ignore

DEFAULT_HOST = "127.0.0.1"  # tailnet外に晒さないため待受はlocalhost固定(外部公開はTailscale Serveに任せる)
DEFAULT_PORT = 5055
TARGET_SAMPLE_RATE = 16000
# 11日目④: ナレッジ取り込みの暴走防止(巨大PDF等の誤操作でメモリ/embedding時間を
# 食い潰さないための安全弁。上限自体に強い根拠はなく、実運用で必要なら調整する)。
DOC_UPLOAD_MAX_BYTES = 20 * 1024 * 1024

# 14日目「添付ファイルをそのまま1ターンだけプロンプトへ埋め込む」対応。
# 📎で添付した文書は、RAG検索(source絞り込み)経由だと上位limit件のチャンクしか
# 文脈に載らず、📷画像添付(生バイト列がそのまま毎回モデルへ渡る)ほど確実に
# 「今アップロードしたこのファイルの全文」を参照できない。抽出テキストがこの
# 文字数以下であれば、検索を挟まずそのまま次のターンのプロンプトへ丸ごと
# 埋め込んでよいとみなす閾値。手書きノートのデジタル化や数ページ程度のPDFの
# 全文はこの範囲に収まりつつ、DEEPモデル(gemma3:27b等)のコンテキスト長・
# VRAM消費を圧迫するほど大きくはならない、という実運用上の目安値(厳密な
# トークン数計算はしていない。必要になれば運用実績を見て調整する)。これを
# 超える大きな文書は、従来どおりsource絞り込みのRAG検索へフォールバックする。
DOC_INLINE_MAX_CHARS = 20000

# 12日目①残課題(分析3): VRAM推移からの推測でしか応答時間を測れなかったため、
# text_input受信〜応答完了(state: idleの直前)までの所要時間を実測してCSVへ残す。
DEFAULT_TIMING_CSV_PATH = SCRIPT_DIR / "results" / "response_timing.csv"
_TIMING_CSV_FIELDS = ["start_iso", "chat_id", "text", "images_count", "duration_sec"]


# ---------------------------------------------------------------------------
# 14日目③: セッションタイトルのLLM要約
# ---------------------------------------------------------------------------
# session_store.append_turn()が付ける暫定タイトルは「ユーザー発話の先頭30字を
# そのまま採用」のため、「この資料からおすすめな曲のタイトルは何かな」のような
# 入力をそのままタイトルにしてしまい、Claude/ChatGPT等の「内容を要約した」タイトルとは
# 体験が異なる。ここでは最初のやり取りが確定した後にバックグラウンドで軽量モデルへ
# 要約させ、session_store.set_auto_title()で暫定タイトルを置き換える(voice_gatewayの
# ws_endpoint側の配線を参照)。会話のテンポ(音声UIの応答速度)に影響を与えたくないため、
# 応答を送り終えたあと・別スレッドで叩く設計にしてあり、ユーザーの発話直後に同期で
# 呼ぶことはしない。
TITLE_SUMMARY_MODEL = ROUTER_MODEL  # ルーター分類と同じ常駐・軽量モデル。GPU版のFAST/DEEPとは競合しない
TITLE_SUMMARY_MAX_CHARS = 30
TITLE_SUMMARY_SYSTEM_PROMPT = (
    "あなたはチャットのタイトルを生成するアシスタントです。"
    "ユーザーの発言の内容を要約した短い日本語タイトルを1つだけ出力してください。"
    "8〜15字程度の名詞句とし、句読点・引用符・絵文字・説明文は付けないでください。"
    "タイトル以外は一切出力しないでください。"
)


def summarize_session_title(
    user_text: str, generate_fn: Callable[..., str] | None = None
) -> str | None:
    """ユーザーの最初の発話を短い日本語タイトルへ要約する。

    generate_fn: `ollama_client.generate`互換(キーワード引数`model`/`prompt`/`system`等を
    受け取り、生成テキストを返す)。テストでは実際のOllamaを叩かないフェイクへ差し替える。
    省略時は`ollama_client.generate`を使う。

    失敗時(Ollama停止中等の例外)・空応答時はNoneを返す。呼び出し側はNoneを見て、
    session_store.append_turn()が付けた暫定タイトル(先頭30字)をそのまま残せばよい。
    """
    if generate_fn is None:
        import ollama_client  # noqa: PLC0415 - 既定実装でのみ必要

        generate_fn = ollama_client.generate

    try:
        raw = generate_fn(
            model=TITLE_SUMMARY_MODEL,
            prompt=user_text,
            system=TITLE_SUMMARY_SYSTEM_PROMPT,
            options={"temperature": 0},
            think=False,
        )
    except Exception:  # noqa: BLE001 - Ollama停止中等。暫定タイトルのまま残す
        return None

    title = (raw or "").strip()
    if not title:
        return None
    title = title.splitlines()[0].strip()
    title = title.strip("「」『』\"'“”").strip()
    if not title:
        return None
    return title[:TITLE_SUMMARY_MAX_CHARS]


# ---------------------------------------------------------------------------
# run_turn(): WebSocket/FastAPIに依存しない、1ターン分のオーケストレーション
# ---------------------------------------------------------------------------


def _wav_message(text: str, wav_bytes: bytes) -> dict:
    return {"type": "audio", "text": text, "wav_b64": base64.b64encode(wav_bytes).decode("ascii")}


def _close_reply(reply) -> None:
    """17日目「裏側の処理も止める」対応: `pipe.pipe()`の戻り値(ストリーミング時は
    Iterator[str])を早期に打ち切る。`ollama_client.generate_stream()`が返す
    ジェネレータは`finally: resp.close()`でHTTP接続を閉じる設計になっている
    (呼び出し側がイテレートを途中でやめた場合に備えて元から用意されていた口)ため、
    ここで`.close()`を呼ぶだけで実際にOllamaへのストリーム接続が切れ、応答生成そのもの
    (GPU計算)を打ち切れる。`reply`が文字列(非ストリーミング経路)やcloseを持たない
    フェイクの場合は何もしない。"""
    close = getattr(reply, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - キャンセル処理自体を失敗させたくない
            pass


# 15日目(指示2): Web検索の出典(タイトル+URL)は画面表示には必要だが、読み上げると
# 「エイチティーティーピーエス、コロン、スラッシュスラッシュ…」のように無意味かつ長大な
# 音声になり、TTS(VOICEVOX)への負荷も増える(実機で計測されたsynthesis timeoutの一因)。
# sentence_splitter.py は改行も文の区切りとして扱うため、`web_search.format_for_prompt()`が
# 出す「[1] タイトル\n出典: https://...」形式は1行=1「文」になる。その各行のうち
# 出典行(URLを含む行/「出典」ラベル行/`[n]`から始まる出典番号行)だけをTTS対象から除外する。
# 画面表示(sentenceイベント自体)は従来どおり出すので、出典は見えるが読み上げられない。
_RE_URL = re.compile(r"https?://\S+")
_RE_CITATION_LABEL = re.compile(r"^(出典|参考|参考文献|source|references?)\s*[:：]?\s*$", re.IGNORECASE)
_RE_CITATION_ENTRY = re.compile(r"^\[\d+\]")


def _is_citation_text(text: str) -> bool:
    """読み上げから除外すべき出典/URL行かどうかを判定する(指示2)。"""
    stripped = text.strip()
    if not stripped:
        return True
    if _RE_URL.search(stripped):
        return True
    if _RE_CITATION_LABEL.match(stripped):
        return True
    if _RE_CITATION_ENTRY.match(stripped):
        return True
    return False


def make_csv_timing_recorder(csv_path: Path | str = DEFAULT_TIMING_CSV_PATH) -> Callable[[dict], None]:
    """`run_turn(timing_recorder=...)`にそのまま渡せる、CSV追記実装を返す。

    12日目①分析3の改善策2: 「VRAM推移からの推測」に頼らず、request/responseの
    タイムスタンプをそのままログへ残す。呼ばれるたびに1行追記し、ファイルが
    存在しない(=初回)ときだけヘッダ行を書く。`start_ts`(epoch秒。time.time()由来)は
    人間が読めるISO文字列に変換してから書き出す。
    """
    csv_path = Path(csv_path)

    def record(row: dict) -> None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        is_new_file = not csv_path.exists()
        with csv_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if is_new_file:
                writer.writerow(_TIMING_CSV_FIELDS)
            start_iso = datetime.fromtimestamp(row["start_ts"]).isoformat()
            writer.writerow(
                [
                    start_iso,
                    row["chat_id"],
                    row["text"],
                    row["images_count"],
                    round(row["duration_sec"], 3),
                ]
            )

    return record


def _sentences_from_reply(
    reply, splitter: SentenceSplitter, *, cancel_event: threading.Event | None = None
) -> Iterator[dict]:
    """`pipe.pipe()`の戻り値(str または トークンのIterator[str])を、
    `{"type": "token", ...}` / `{"type": "sentence", ...}` のdict列に変換する。

    9日目④の設計どおり、FAST/DEEPかつストリーミング要求時のみIterator[str]が返り、
    CODE/CLARIFY/タスク呼び出しはstrのまま返る。どちらの経路でも最終的に
    sentence_splitterへ通してから返す(strの場合もMarkdown除去等の正規化を効かせるため)。

    cancel_event: 17日目「裏側の処理も止める」対応。トークンを1つ受け取るたびに
    確認し、セットされていたら`reply`(Ollamaへのストリーム接続)を`_close_reply()`で
    閉じてただちに打ち切る(残りのトークンは待たない)。非ストリーミング経路(str)は
    元々一括で返ってきている(=待つべき残りの生成が無い)ためチェック不要。
    """
    if isinstance(reply, str):
        for sentence in splitter.feed(reply):
            yield {"type": "sentence", "text": sentence}
        for sentence in splitter.flush():
            yield {"type": "sentence", "text": sentence}
        return

    for token in reply:
        if cancel_event is not None and cancel_event.is_set():
            _close_reply(reply)
            return
        yield {"type": "token", "text": token}
        for sentence in splitter.feed(token):
            yield {"type": "sentence", "text": sentence}
    for sentence in splitter.flush():
        yield {"type": "sentence", "text": sentence}


def run_turn(
    pipe,
    chat_id: str,
    user_text: str,
    *,
    synthesize: Callable[[str], bytes],
    splitter_kwargs: dict | None = None,
    images: list[str] | None = None,
    attached_document: str | None = None,
    attached_document_text: str | None = None,
    web_search: bool = False,
    clock: Callable[[], float] = time.time,
    timing_recorder: Callable[[dict], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Iterator[dict]:
    """1ターン分の会話を実行し、WSへそのまま送れるdictメッセージを順次yieldする。

    `pipe`は`support_ai_auto_pipe.Pipe`互換オブジェクト(`.pipe(body, __metadata__=...)`を
    持つもの)。`body["stream"]=True`を渡す(FAST/DEEPならIterator[str]、それ以外はstrが返る。
    分岐の判断はPipe側の責務でここは戻り値の型を見るだけでよい)。

    例外は外へ投げない(9日目④の`_stream_reply()`と同じ方針。⑥の作業内容
    「エラー時にUIが無反応にならないようにする」の実装)。Pipe呼び出し自体が失敗した場合、
    TTS合成が1文だけ失敗した場合のいずれも`{"type": "error", ...}`を返しつつ、
    残りの処理(他の文の合成・最後のstate: idle送出)は続行する。

    images: 11日目④-1「画像添付時はDEEPへ強制ルーティング」対応。base64エンコード済み
    画像(data URL prefix無し)のリスト。指定されると、最後のuserメッセージ辞書に
    `"images"`キーとして載せて`pipe.pipe()`へ渡す(support_ai_auto_pipe.Pipe側の
    `_extract_last_user_images()`が読む独自convention)。Noneのままなら従来どおり
    `content`のみのメッセージになり、既存の呼び出し元の挙動は変わらない。

    attached_document: 13日目「直近添付ファイルを自動優先」対応。📎でアップロードされた
    直後の文書のファイル名。指定されると、imagesと同じ要領で最後のuserメッセージ辞書に
    `"attached_document"`キーとして載せて`pipe.pipe()`へ渡す(support_ai_auto_pipe.Pipe側の
    `_extract_last_user_attached_document()`が読む独自convention。記憶検索をそのファイルへ
    優先的に絞り込むために使われる)。Noneのままなら従来どおり付かない。

    attached_document_text: 14日目「添付ファイルをそのまま1ターンだけプロンプトへ埋め込む」
    対応。DOC_INLINE_MAX_CHARS以下の抽出テキストが/documentsのレスポンスに含まれていた
    場合、static/index.html側がそれを覚えておき、次の送信1回だけこのテキストとして
    同梱してくる。指定されると、attached_documentと同じ要領で最後のuserメッセージ辞書へ
    `"attached_document_text"`キーとして載る(support_ai_auto_pipe.Pipe側の
    `_extract_last_user_attached_document_text()`が読む。あればRAG検索を介さず
    そのままsystem文脈へ使われる)。Noneのままなら従来どおり付かない。

    web_search: 14日目(13日目④で部品実装のみ区切っていたWeb検索の結線)。
    静的UIのWeb検索トグルがONのときTrueが渡される。指定されると、imagesと同じ要領で
    最後のuserメッセージ辞書に`"web_search"`キーとして載せて`pipe.pipe()`へ渡す
    (support_ai_auto_pipe.Pipe側の`_extract_last_user_web_search()`が読む独自convention。
    SearXNG検索を行うかどうかの判断・実行はPipe側の責務で、ここでは配線するだけ)。
    Falseのままなら従来どおり付かない。

    clock / timing_recorder: 12日目①分析3の改善策2。「VRAM推移からの推測」に頼らず、
    text_input受信〜応答完了(state: idle直前)までの所要時間を実測するためのフック。
    timing_recorderを渡さない既存呼び出し元(既存テスト・現行の呼び出し箇所)は
    従来どおり何も記録されない(後方互換)。clockは主にテストで固定値を注入するための
    差し替え口で、実運用では既定のtime.time()のままでよい。

    cancel_event: 17日目「誤送信の即時停止」対応。呼び出し側(ws_endpoint)が
    ユーザーからの中断要求を受けてセットする`threading.Event`。セットされていたら、
    Ollamaへのストリーム接続を`_close_reply()`で閉じ(=GPU側の生成も打ち切られる)、
    残りのトークン処理・TTS合成をスキップして`state: idle`まで進める。Noneのままなら
    (=既存呼び出し元・既存テスト)従来どおりキャンセル不可の挙動になる(後方互換)。
    """
    start_ts = clock()

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _record_timing() -> None:
        if timing_recorder is None:
            return
        end_ts = clock()
        timing_recorder(
            {
                "chat_id": chat_id,
                "text": user_text,
                "images_count": len(images or []),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "duration_sec": end_ts - start_ts,
            }
        )

    yield {"type": "state", "value": "thinking"}

    splitter = SentenceSplitter(**(splitter_kwargs or {}))

    user_message: dict = {"role": "user", "content": user_text}
    if images:
        user_message["images"] = images
    if attached_document:
        user_message["attached_document"] = attached_document
    if attached_document_text:
        user_message["attached_document_text"] = attached_document_text
    if web_search:
        user_message["web_search"] = True

    try:
        reply = pipe.pipe(
            body={"messages": [user_message], "stream": True},
            __metadata__={"chat_id": chat_id},
        )
    except Exception as e:  # noqa: BLE001 - Pipe呼び出し失敗の理由をそのまま通知する
        yield {"type": "error", "stage": "pipe", "message": f"{type(e).__name__}: {e}"}
        _record_timing()
        yield {"type": "state", "value": "idle"}
        return

    if _cancelled():
        # 17日目: pipe.pipe()から戻った直後、最初のトークンを取り出す前に
        # 既にキャンセル要求が来ていたケース(=ストリーム接続はまだ何も読んでいない)。
        _close_reply(reply)
        _record_timing()
        yield {"type": "state", "value": "idle"}
        return

    for event in _sentences_from_reply(reply, splitter, cancel_event=cancel_event):
        yield event
        if _cancelled():
            # 17日目: `_sentences_from_reply()`内部のチェックより先にここで検知した
            # 場合(例:直後にトークンが来る前にキャンセルされた等)に備え、
            # ここでも明示的に閉じておく(`_close_reply()`は多重に呼んでも安全)。
            _close_reply(reply)
            break
        if event["type"] != "sentence":
            continue
        sentence = event["text"]
        if _is_citation_text(sentence):
            # 指示2: 出典/URL行は画面表示(上のyield)はするが読み上げない
            continue
        try:
            wav_bytes = synthesize(sentence)
        except Exception as e:  # noqa: BLE001 - 1文の合成失敗で残りの文の再生を止めない
            yield {"type": "error", "stage": "tts", "message": f"{type(e).__name__}: {e}"}
            continue
        yield _wav_message(sentence, wav_bytes)
        if _cancelled():
            _close_reply(reply)
            break

    _record_timing()
    yield {"type": "state", "value": "idle"}


# ---------------------------------------------------------------------------
# FastAPI + WebSocket: ブラウザとの実配線(⑥残課題:実機確認はまだ)
# ---------------------------------------------------------------------------


def create_app(
    *,
    pipe_factory: Callable[[], object] | None = None,
    stt_engine_factory: (
        Callable[[Callable[[str], None], Callable[[str], None], Callable[[str], None]], object] | None
    ) = None,
    synthesize: Callable[[str], bytes] | None = None,
    timing_recorder: Callable[[dict], None] | None = None,
    session_store: SessionStore | None = None,
    title_generator: Callable[[str], str | None] | None = None,
):
    """FastAPIアプリを組み立てる。

    実運用では引数なしで呼び、実物のPipe/STTEngine/tts_adapterを使う。
    引数はテスト・段階的な差し替えのために用意してある(現時点の自動テストは
    `run_turn()`単体で完結しており、このFastAPIアプリ自体の統合テストは未整備。
    9日目⑥残課題として実機確認と合わせて追加する)。

    timing_recorder: 12日目①分析3の改善策2。省略時は`make_csv_timing_recorder()`の既定
    (results/response_timing.csv への追記)を使う。テストで無効化・差し替えしたい場合のみ
    明示的に渡す。

    session_store: 14日目②。省略時はsession_store.DEFAULT_ROOT
    (scripts/data/sessions/)を使う。テストではtmp_pathへ差し替えたSessionStoreを渡す。

    title_generator: 14日目③。省略時は`summarize_session_title`(Ollamaへ問い合わせる)。
    テストでは実際のOllamaを叩かないフェイクへ差し替える。
    """
    if FastAPI is None:
        raise RuntimeError("fastapiが未インストールです(pip install fastapi uvicorn[standard])")

    app = FastAPI()
    static_dir = SCRIPT_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    session_store = session_store if session_store is not None else SessionStore()

    def _default_pipe_factory():
        from support_ai_auto_pipe import Pipe  # noqa: PLC0415

        return Pipe()

    def _default_synthesize():
        import tts_adapter  # noqa: PLC0415

        # ①で確定した声(VOICEVOX/東北ずん子/話者ID 107)。エンジンURL/話者IDは
        # 環境変数で上書きできるようにしておく(⑦でTailscale経由になっても差し替え不要)。
        import os  # noqa: PLC0415

        engine_url = os.environ.get("TTS_ENGINE_URL", "http://127.0.0.1:50021")
        speaker_id = int(os.environ.get("TTS_SPEAKER_ID", "107"))

        def synth(text: str) -> bytes:
            return tts_adapter.synthesize(engine_url, text, speaker_id)

        return synth

    def _default_stt_engine_factory(on_partial, on_final, on_error):
        import os  # noqa: PLC0415

        from stt_engine import create_default_engine  # noqa: PLC0415

        vosk_model_dir = os.environ.get(
            "VOSK_MODEL_DIR", str(Path.home() / "vosk_models" / "vosk-model-small-ja-0.22")
        )
        return create_default_engine(
            vosk_model_dir=vosk_model_dir,
            on_partial=on_partial,
            on_final=on_final,
            on_error=on_error,
        )

    pipe_factory = pipe_factory or _default_pipe_factory
    synthesize = synthesize or _default_synthesize()
    stt_engine_factory = stt_engine_factory or _default_stt_engine_factory
    timing_recorder = timing_recorder if timing_recorder is not None else make_csv_timing_recorder()
    title_generator = title_generator or summarize_session_title

    def _generate_and_persist_title(session_id: str, user_text: str) -> str | None:
        # 14日目③: loop.run_in_executor()で別スレッド実行される。応答は既に
        # クライアントへ送信済みのため、ここでOllamaを叩いて多少の時間がかかっても
        # 会話のテンポには影響しない。戻り値(成功時のみ非None)は呼び出し側
        # (ws_endpoint)がWSで"session_title_updated"を押し返すために使う
        # (14日目④: サイドバーの次回更新を待たず、その場でタイトルを変えたいという要望対応)。
        try:
            title = title_generator(user_text)
        except Exception:  # noqa: BLE001 - 失敗しても暫定タイトル(先頭30字)のまま残ればよい
            return None
        if not title:
            return None
        try:
            session_store.set_auto_title(session_id, title)
        except KeyError:
            return None  # 生成中にセッションが削除された等。通知もしない
        return title

    @app.get("/")
    def index():
        return FileResponse(str(static_dir / "index.html"))

    def _doc_ingest():
        import doc_ingest  # noqa: PLC0415 - rag_memory/ 配下。RAG_MEMORY_DIRをsys.pathへ追加済み

        return doc_ingest

    # 11日目④: PDF/Word/Excel/PowerPointの添付をナレッジ(LanceDB)へ永続登録するHTTP経路。
    # WebSocket(/ws)とは別立て(ファイルアップロードはHTTPの方が扱いやすいため)。
    # static/index.html の📎ボタンから叩かれる想定。
    @app.post("/documents")
    async def upload_document(file: UploadFile = File(...)):
        doc_ingest = _doc_ingest()
        filename = file.filename or ""
        suffix = Path(filename).suffix.lower()
        if suffix not in doc_ingest.SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"未対応のファイル形式です: {suffix or '(拡張子なし)'}"
                f"(対応: {sorted(doc_ingest.SUPPORTED_EXTENSIONS)})",
            )
        data = await file.read()
        if len(data) > DOC_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"ファイルサイズが大きすぎます(上限{DOC_UPLOAD_MAX_BYTES // (1024 * 1024)}MB)",
            )
        # 14日目: DOC_INLINE_MAX_CHARS判定にテキストそのものが要るため、ここで先に
        # 1回だけextract_text()を呼び、その結果をingest_document()へそのまま渡す
        # (ingest_document()内で再度抽出する二度手間を避ける)。
        try:
            text = doc_ingest.extract_text(filename, data)
        except doc_ingest.UnsupportedFileTypeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001 - 抽出失敗の理由をそのまま通知する
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
        try:
            result = doc_ingest.ingest_document(filename, data, text=text)
        except Exception as e:  # noqa: BLE001 - DB登録失敗の理由をそのまま通知する
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
        # 抽出テキストが閾値以下なら、次のターンでRAG検索を介さずそのまま
        # プロンプトへ埋め込めるようレスポンスへ同梱する(static/index.htmlの
        # lastAttachedDocumentText参照)。超える場合はnullにし、クライアント側に
        # 従来どおりのRAG(source絞り込み)フォールバックを使わせる。
        result["text"] = text if len(text) <= DOC_INLINE_MAX_CHARS else None
        return result

    @app.get("/documents")
    def list_documents_endpoint():
        return _doc_ingest().list_documents()

    @app.delete("/documents/{filename}")
    def delete_document_endpoint(filename: str):
        deleted = _doc_ingest().delete_document(filename)
        return {"filename": filename, "deleted": deleted}

    @app.get("/documents/{filename}/text")
    def get_document_text_endpoint(filename: str):
        """14日目①: ピン留めしたファイルの抽出全文を返す。

        POST /documentsのレスポンス(result["text"])と同じ判定基準で、
        DOC_INLINE_MAX_CHARS以下なら全文を、超える場合はnullを返す。
        nullのときクライアントはattached_document(ファイル名)だけを送り、
        従来どおりsource絞り込みRAGへフォールバックする。
        """
        doc_ingest = _doc_ingest()
        text = doc_ingest.get_document_text(filename)  # 未登録ならNone
        if text is None:
            raise HTTPException(status_code=404, detail=f"未登録のファイルです: {filename}")
        return {"filename": filename, "text": text if len(text) <= DOC_INLINE_MAX_CHARS else None}

    # 14日目②: チャット履歴(画面表示用の逐語ログ)の永続化。session_store.py参照。
    # サイドバーの動的描画・New Session・リネーム/削除/検索(static/index.html)から叩かれる。
    def _session_summary(session: Session) -> dict:
        return {
            "session_id": session.session_id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    def _session_detail(session: Session) -> dict:
        d = _session_summary(session)
        d["turns"] = [
            {"role": t.role, "text": t.text, "route": t.route, "ts": t.ts} for t in session.turns
        ]
        return d

    @app.get("/sessions")
    def list_sessions_endpoint(q: str | None = None):
        sessions = session_store.search_sessions(q) if q else session_store.list_sessions()
        return [_session_summary(s) for s in sessions]

    @app.post("/sessions")
    def create_session_endpoint():
        return _session_detail(session_store.create_session())

    @app.get("/sessions/{session_id}")
    def get_session_endpoint(session_id: str):
        session = session_store.load_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"未登録のセッションです: {session_id}")
        return _session_detail(session)

    @app.patch("/sessions/{session_id}")
    def rename_session_endpoint(session_id: str, body: dict):
        try:
            session_store.rename_session(session_id, body.get("title", ""))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return _session_detail(session_store.load_session(session_id))

    @app.delete("/sessions/{session_id}")
    def delete_session_endpoint(session_id: str):
        session_store.delete_session(session_id)
        return {"session_id": session_id, "deleted": True}

    # 14日目③: 資料・コード生成(Excel/PowerPoint/Word/JSON)の生成物一覧・ダウンロード。
    # code_executor.execute_python_file()がworkspace/へ書き出したファイルを、
    # static/index.htmlのチップ/サイドバー「🗂 生成物」から取得できるようにする。
    @app.get("/artifacts")
    def list_artifacts_endpoint():
        """workspace/配下の生成物一覧(name/size/modified)。UIのサイドバー描画用。"""
        ws = code_executor.WORKSPACE_DIR
        if not ws.exists():
            return []
        items = [
            {
                "filename": str(p.relative_to(ws)),
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            }
            for p in ws.rglob("*")
            if p.is_file()
        ]
        return sorted(items, key=lambda x: x["modified"], reverse=True)

    @app.get("/artifacts/{filename:path}")
    def download_artifact_endpoint(filename: str):
        """生成物のダウンロード。

        パス解決はcode_executor.resolve_safe_path()を再利用する。自前でパスを
        組み立てると"../voice_gateway.py"のようなworkspace外のファイルを配信して
        しまうため、④のGoogle認証トークン保護にも関わる既存の安全境界を必ず使い回す
        (UnsafePathErrorのときも存在を漏らさないよう404で応答する)。
        """
        try:
            target = code_executor.resolve_safe_path(filename, code_executor.WORKSPACE_DIR)
        except code_executor.UnsafePathError:
            raise HTTPException(status_code=404, detail="not found")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(str(target), filename=target.name)

    # 14日目④: Google Workspace連携(調査レポートのDocs/Sheets出力・生成物のDriveアップロード)。
    # google_workspace.py参照。LLMからは呼ばせず、必ずこのHTTP経路経由でのみ実行する。
    @app.get("/google/status")
    def google_status_endpoint():
        """UIが「未認証の案内」を出すか判断するための軽量チェック。

        google_workspace.check_status()はget_credentials()を呼ばないため、
        ここでブラウザ同意が勝手に開くことはない。
        """
        return google_workspace.check_status()

    @app.post("/google/export")
    def google_export_endpoint(body: dict):
        """直前のアシスタント応答、または生成物をGoogleへ出力し、URLを返す。

        body: {target: "docs"|"sheets"|"drive", text?, title?, rows?, artifact?}
        """
        target = body.get("target")
        title = body.get("title") or "C.L.A.I.R.E. 出力"
        try:
            if target == "docs":
                url = google_workspace.export_to_docs(title, body.get("text") or "")
            elif target == "sheets":
                url = google_workspace.export_to_sheets(title, body.get("rows") or [])
            elif target == "drive":
                artifact = body.get("artifact") or ""
                try:
                    resolved = code_executor.resolve_safe_path(artifact, code_executor.WORKSPACE_DIR)
                except code_executor.UnsafePathError:
                    raise HTTPException(status_code=404, detail="not found")
                if not resolved.is_file():
                    raise HTTPException(status_code=404, detail="not found")
                url = google_workspace.upload_to_drive(resolved)
            else:
                raise HTTPException(
                    status_code=400, detail=f'target は "docs"/"sheets"/"drive" のいずれかです: {target}'
                )
        except google_workspace.NotAuthenticatedError as e:
            # 例外スタックトレースを見せず、UIが再認証を促せる日本語メッセージだけを返す。
            raise HTTPException(status_code=401, detail=str(e)) from e
        return {"url": url}

    # 15日目(指示1): ウェイクワード検出が実際に効いているか不安、という声を受けて、
    # 「クレア/ねえクレア」を検出した瞬間に固定の音声応答を即座に返す(LLM応答を待たない)。
    # 音声そのものはconnectionをまたいで使い回せるので、create_app()スコープ(=このFastAPI
    # アプリインスタンス全体)で1回だけ合成してキャッシュする。
    # 15日目②(指示1): 「ごう様」と漢字で渡すとVOICEVOXが「ごうよう」と誤読することが
    # 実機で確認された(「様」は「さま」「よう」等の複数読みを持つ多音字で、単語辞書に
    # 依存する)。読みを一意にするため、送信文字列自体をひらがなにしておく。
    WAKE_REPLY_TEXT = "はい、ごうさま"
    WAKE_REPLY_COOLDOWN_SEC = 5.0  # 同じ発話中のpartial連呼で何度も鳴らさないためのクールダウン
    _wake_reply_audio_cache: list[bytes | None] = [None]

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        pipe = pipe_factory()
        # 14日目②: 従来は接続ごとの使い捨てID(voice-{id(websocket)})だった。
        # クライアントが"select_session"で送るsession_idに揃えることで、リロードや
        # 会話の切り替えをまたいでRAG記憶(chat_id)と画面の会話(session_id)を一致させる。
        # 未指定(=旧クライアント/session未選択)のときは従来どおりのフォールバックのままとし、
        # このセッションはsession_storeへの永続化もしない(selected_session_idがNoneの間)。
        chat_id = f"voice-{id(websocket)}"
        selected_session_id: str | None = None
        _last_wake_reply_ts = [0.0]
        loop = asyncio.get_running_loop()

        # マイクは常時ON+VAD自動送信、読み上げ中はマイクOFF(⑥仕様)。
        # 「読み上げ中はSTTへ音声を渡さない」制御はブラウザ側(mic mute)が主で、
        # ここではエコー対策の保険として speaking 中は確定転写のみ受け付ける実装にしてもよいが、
        # 初版はブラウザ側制御を信頼しシンプルに保つ(YAGNI)。

        # 17日目「誤送信の即時停止」対応: run_turn()はまだ同期ジェネレータ(Pipe.pipe()/
        # synthesize()が同期ブロッキング呼び出しのため)。これを直接
        # `for event in run_turn(...): await send_json(event)`のようにメインループ上で
        # 消費すると、生成が終わるまでイベントループごと固まり、ブラウザから送る
        # 「中断」メッセージすら受け取れない(過去バージョンの実際の制約だった)。
        # そのため生成中のターンは専用スレッドで`run_turn()`を消費し、asyncio.Queue経由で
        # イベントをメインループへ橋渡しする。これによりメインの受信ループ(下のwhile)は
        # 生成中も動き続け、"cancel_turn"を受け取り次第すぐに`cancel_event`をセットできる。
        # `_close_reply()`がOllamaへのストリーム接続を閉じるため、GPU側の生成も実際に止まる。
        turn_cancel_event: threading.Event | None = None
        turn_task: asyncio.Task | None = None
        send_lock = asyncio.Lock()  # turn_task側とメインループ側、両方からの並行send_jsonを直列化する

        async def send_json(msg: dict) -> None:
            async with send_lock:
                await websocket.send_json(msg)

        _TURN_DONE = object()  # ターン終了(=run_turn()のジェネレータが尽きた)を示す番兵

        async def _run_turn_and_forward(
            gen: Iterator[dict], title_source_text: str | None = None
        ) -> None:
            # title_source_text: 14日目③。このセッションの最初のやり取りのときだけ
            # 呼び出し側(_handle_final_transcript)がユーザー発話を渡してくる。Noneなら
            # (2ターン目以降・未選択・既にリネーム済み)タイトル要約は行わない。
            queue: asyncio.Queue = asyncio.Queue()

            def _drain() -> None:
                # 別スレッドで実行される(run_in_executor)。run_turn()自体は
                # WebSocket/asyncioに依存しない純粋なジェネレータなので、ここで
                # 同期的に消費してよい。queueへの投入はスレッドセーフなAPI経由で行う。
                try:
                    for event in gen:
                        loop.call_soon_threadsafe(queue.put_nowait, event)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, _TURN_DONE)

            executor_future = loop.run_in_executor(None, _drain)
            # 14日目②: static/index.htmlのassistantTurnEl組み立てと同じ方針(token/sentence
            # の二重計上を避ける)で、AIの応答全文を組み立てておく。run_turn()は最後に必ず
            # {"type": "state", "value": "idle"}をyieldする(⑥の設計。この時点で全文は
            # 揃っている)ため、そのイベントを転送する直前に確定させる。_TURN_DONE(=キューが
            # 尽きた後)まで待つと、クライアントが既にidleを受け取って次の操作(接続を閉じる等)を
            # 始めた後になりかねず、テスト・実運用の両方で永続化とのレースになるため。
            token_seen = False
            token_parts: list[str] = []
            sentence_parts: list[str] = []
            title_future: asyncio.Future | None = None
            title_session_id: str | None = None  # title_futureを起動した時点のselected_session_id

            def _persist_assistant_turn_if_selected() -> None:
                nonlocal title_future, title_session_id
                if selected_session_id is None:
                    return
                assistant_text = "".join(token_parts) if token_seen else "".join(sentence_parts)
                if assistant_text.strip():
                    session_store.append_turn(
                        selected_session_id, role="assistant", text=assistant_text
                    )
                if title_source_text is not None:
                    # 14日目③: 応答はこの直後のsend_json(event)で既に送信されるため、
                    # ここから別スレッドでOllamaを叩いても会話のテンポは崩れない。
                    title_session_id = selected_session_id
                    title_future = loop.run_in_executor(
                        None, _generate_and_persist_title, selected_session_id, title_source_text
                    )

            try:
                while True:
                    event = await queue.get()
                    if event is _TURN_DONE:
                        break
                    if event.get("type") == "token":
                        token_seen = True
                        token_parts.append(event.get("text", ""))
                    elif event.get("type") == "sentence" and not token_seen:
                        sentence_parts.append(event.get("text", ""))
                    if event.get("type") == "state" and event.get("value") == "idle":
                        _persist_assistant_turn_if_selected()
                    await send_json(event)
            finally:
                await executor_future  # スレッドが完全に終わるまで待つ(後始末の取りこぼし防止)
                if title_future is not None:
                    updated_title = await title_future
                    if updated_title:
                        # 14日目④: サイドバーの次回更新(会話切替・リロード等)を待たず、
                        # 要約が終わったこの時点で即座にクライアントへ知らせる。
                        await send_json(
                            {
                                "type": "session_title_updated",
                                "session_id": title_session_id,
                                "title": updated_title,
                            }
                        )

        # 19日目 修正: 2ターン目以降、「クレア起動」(ウェイクワード)の直後に3秒未満の
        # 間で次のコマンドを話すと、vad.VoskEndpointVADの「保留中に発話が再開したら
        # 区切りを取り消して発話継続とみなす」仕様により、ウェイクワード発話と後続の
        # コマンド発話が1つの確定テキストに連結されてしまうバグがあった
        # (実害: ①入力欄にウェイクワードの文字がコマンドと混ざって残る、
        #  ②on_partialとon_finalの両方でこの連結後の長い確定テキストに対して
        #  ウェイクワード判定が走り、5秒クールダウンをまたいで「はい、ごうさま」の
        #  応答音声が2回鳴る)。
        # 対策: ウェイクワードを検出したその場でVAD/Voskの保留を待たず強制的に
        # 発話を確定させ(`STTEngine.force_finalize_pending()`)、以降の音声を
        # 新しい発話として扱う。これにより後続のコマンドはウェイクワード抜きの
        # 独立した確定テキストになり、混入も二重検出も構造的に起きなくなる。
        # `force_finalize_pending()`はその場でon_final(=このtext自体)を再度呼ぶため、
        # 再入(無限ループ)を防ぐガードを設ける。
        _wake_force_finalizing = [False]

        def _check_wake_word(text: str) -> None:
            # 13日目②:ウェイクワード「クレア/ねえクレア」を検出したら`wake_detected`を
            # WSで送るだけの純粋な通知。判定自体はwake_word.py(サーバ側)で行い、
            # 「受付中(armed)にするかどうか」「何秒でタイムアウトするか」等の状態管理は
            # クライアント側(index.html)に持たせる(②方針の表参照)。
            if _wake_force_finalizing[0]:
                return  # force_finalize_pending()内で再度呼ばれた分は無視(再入防止)
            detection = detect_wake_word(text)
            if detection is not None:
                _schedule(send_json({"type": "wake_detected", "text_after": detection.text_after}))
                _schedule(_send_wake_reply())
                _wake_force_finalizing[0] = True
                try:
                    stt.force_finalize_pending()
                finally:
                    _wake_force_finalizing[0] = False

        async def _send_wake_reply() -> None:
            # 指示1: 検出のたびにVOICEVOXを叩くと待ち時間が出るうえ、on_partialは同じ
            # 発話中に何度も同じテキストで呼ばれうるため、クールダウン中は鳴らさない。
            now = time.monotonic()
            if now - _last_wake_reply_ts[0] < WAKE_REPLY_COOLDOWN_SEC:
                return
            _last_wake_reply_ts[0] = now
            if _wake_reply_audio_cache[0] is None:
                try:
                    _wake_reply_audio_cache[0] = synthesize(WAKE_REPLY_TEXT)
                except Exception:  # noqa: BLE001 - 応答音声が作れなくても検出通知自体は別途送っている
                    return
            await send_json(_wav_message(WAKE_REPLY_TEXT, _wake_reply_audio_cache[0]))

        def on_partial(text: str) -> None:
            _schedule(send_json({"type": "partial_transcript", "text": text, "final": False}))
            _check_wake_word(text)

        def on_final(text: str) -> None:
            # 10日目⑦:ウェイクワード「言えば即送信」の旧方式は撤回した。STTが確定させた
            # テキストもAIへは直接渡さず、partial_transcriptと同じ扱いでクライアントの
            # テキスト入力欄へ反映するだけにとどめる(ユーザーが内容を確認・修正して
            # 送信ボタン/Enter、または13日目③の自動送信条件を満たした時だけ、
            # 下のtext_input分岐からrun_turn()が呼ばれる)。
            # "final": True(2026-08-12追加)は、VADが発話終了を検知しfaster-whisperの
            # 確定転写が終わった瞬間を外部(ws_e2e_bench.py)が識別するためのフラグ。
            #
            # 20日目 修正: 「Hey, C.L.A.I.R.E.」のようにウェイクワード単体で発話すると、
            # `_check_wake_word`が呼ぶ`force_finalize_pending()`がその場でこの`on_final`を
            # 同じ生テキストで再度呼ぶ(`_wake_force_finalizing`のdocstring参照)。この再入時、
            # 従来はここが無条件に生テキストのままpartial_transcript(final=true)を送っていたため、
            # 直前の`wake_detected`でtext_after(トリム済み)へ書き換わった入力欄が生の
            # ウェイクワード文字列で再び上書きされ、かつこの時点では`wakeArmed`が既にtrueに
            # なっているため、クライアント側の自動送信ゲートを素通りしてウェイクワードの
            # 文字列そのものが誤送信される実バグがあった。
            # 再入中はウェイクワードのプレフィックスを取り除いてから送る(単体発話なら
            # 空文字になるので送信自体を抑止し、続けて発話していた場合は後続の本文だけを送る)。
            if _wake_force_finalizing[0]:
                detection = detect_wake_word(text)
                text = detection.text_after if detection is not None else text
                if not text:
                    return
            _schedule(send_json({"type": "partial_transcript", "text": text, "final": True}))
            _check_wake_word(text)

        def on_stt_error(message: str) -> None:
            # STTEngine._finalize()が確定転写(faster-whisper)の失敗をcatchして
            # ここへ回してくる(2026-08-11実機確認で発見した実バグの修正。
            # stt_engine.STTEngineのdocstring参照)。接続は落とさず、暫定表示は
            # 消してエラーを通知するだけにとどめ、次の発話をそのまま受け付ける。
            _schedule(send_json({"type": "error", "stage": "stt", "message": message}))

        pending_sends: list = []

        def _schedule(coro) -> None:
            # STTEngineのコールバックは同期関数として呼ばれるため、ここでは
            # コルーチンをためておき、feed_audio()の呼び出し元(下のメインループ)側で
            # awaitする。WebSocket送信は必ずイベントループ上で行う必要があるための橋渡し。
            pending_sends.append(coro)

        async def _handle_final_transcript(
            text: str,
            images: list[str] | None = None,
            attached_document: str | None = None,
            attached_document_text: str | None = None,
            web_search: bool = False,
        ) -> None:
            nonlocal turn_cancel_event, turn_task
            await send_json({"type": "final_transcript", "text": text})
            await send_json({"type": "state", "value": "speaking"})
            title_source_text: str | None = None
            if selected_session_id is not None:
                # 14日目③: 「このセッションでまだ一度も発話がなく、タイトルが手動
                # リネームもされていない」場合だけ、後段でLLM要約タイトルを生成させる。
                # append_turn()の直前に見ないと、直後のappend_turn()自体で
                # turns/titleが変わってしまい判定できなくなる。
                existing = session_store.load_session(selected_session_id)
                if existing is not None and not existing.title_is_custom and not existing.turns:
                    title_source_text = text
                # 14日目②: ユーザー発話は応答を待たずこの時点で確定して記録する
                # (AI応答側はストリームが尽きるまで組み立ててから_run_turn_and_forwardで追記)。
                session_store.append_turn(selected_session_id, role="user", text=text)
            turn_cancel_event = threading.Event()
            gen = run_turn(
                pipe,
                chat_id,
                text,
                synthesize=synthesize,
                images=images,
                attached_document=attached_document,
                attached_document_text=attached_document_text,
                web_search=web_search,
                timing_recorder=timing_recorder,
                cancel_event=turn_cancel_event,
            )
            # 17日目: ここでawaitして完了を待たない。タスクとして裏で走らせることで、
            # 下のメインループがすぐ次の`websocket.receive()`に戻り、生成中でも
            # マイク音声・"cancel_turn"メッセージを受け取り続けられるようにする。
            turn_task = asyncio.create_task(_run_turn_and_forward(gen, title_source_text))

        stt = stt_engine_factory(on_partial, on_final, on_stt_error)

        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    try:
                        stt.feed_audio(data)
                    except Exception as e:  # noqa: BLE001 - STT側の想定外の失敗で接続を落とさない
                        await send_json(
                            {"type": "error", "stage": "stt", "message": f"{type(e).__name__}: {e}"}
                        )
                else:
                    text_data = message.get("text")
                    if text_data is not None:
                        # 10日目③/⑦:テキスト入力欄の送信ボタン/Enter。音声由来・キーボード
                        # 入力由来を問わず、ユーザーが内容を確認・確定した発話はすべてここを
                        # 通って直接コマンド処理へ回る(現在はこれが唯一のコマンド化経路)。
                        try:
                            payload = json.loads(text_data)
                        except (TypeError, ValueError) as e:
                            await send_json(
                                {"type": "error", "stage": "text_input", "message": f"invalid JSON: {e}"}
                            )
                        else:
                            payload_type = payload.get("type")
                            if payload_type == "cancel_turn":
                                # 17日目「誤送信の即時停止」対応: 進行中のターンがあれば
                                # cancel_eventをセットする。実際にどこで打ち切られるかは
                                # run_turn()側(トークン受信のたび/文合成のたびにチェック)。
                                # 進行中のターンが無ければ何もしない(押しても無害)。
                                if turn_cancel_event is not None:
                                    turn_cancel_event.set()
                            elif payload_type == "select_session":
                                # 14日目②: chat_id(RAG記憶のキー)をsession_id(画面の会話)へ
                                # 揃える。static/index.htmlのselectSession()/newSession()から、
                                # WS接続確立(onopen)のたびに送られる。未登録のsession_id
                                # (削除済み・不正な値)は黙って無視し、従来のフォールバックの
                                # chat_idのまま(=このセッションはpersistしない)にとどめる。
                                raw_session_id = payload.get("session_id")
                                if (
                                    isinstance(raw_session_id, str)
                                    and raw_session_id
                                    and session_store.load_session(raw_session_id) is not None
                                ):
                                    chat_id = raw_session_id
                                    selected_session_id = raw_session_id
                            elif payload_type == "text_input":
                                user_text = (payload.get("text") or "").strip()
                                # 11日目④-1: 送信ボタン/Enter時に画像(base64、data URL
                                # prefix無し)が同梱されていれば受け取る。static/index.htmlの
                                # 📷ボタンで選択した画像は、ここを通ったターンだけの
                                # 一時的なコンテキストとして扱う(📎の文書添付のようにDBへ
                                # 永続登録はしない)。
                                raw_images = payload.get("images")
                                images = (
                                    [img for img in raw_images if isinstance(img, str) and img]
                                    if isinstance(raw_images, list)
                                    else None
                                )
                                # 13日目「直近添付ファイルを自動優先」対応: 📎で文書アップロード
                                # 直後のターンだけ、static/index.htmlが一度きり同梱してくる
                                # ファイル名を受け取り、そのままrun_turn()へ渡す(1ターン限りの
                                # 一時的な文脈。images同様DBへの永続登録はここでは行わない)。
                                raw_attached_document = payload.get("attached_document")
                                attached_document = (
                                    raw_attached_document
                                    if isinstance(raw_attached_document, str) and raw_attached_document
                                    else None
                                )
                                # 14日目: DOC_INLINE_MAX_CHARS以下の抽出テキストがあれば、
                                # static/index.html側がattached_documentと同じ「一度きり」
                                # 方式でこのキーへ同梱してくる(sendTextInput参照)。
                                raw_attached_document_text = payload.get("attached_document_text")
                                attached_document_text = (
                                    raw_attached_document_text
                                    if isinstance(raw_attached_document_text, str) and raw_attached_document_text
                                    else None
                                )
                                # 14日目: 13日目④で部品実装のみだったWeb検索を結線。
                                # static/index.html(sendTextInput)がWeb検索トグルON時だけ
                                # 同梱してくる真偽値。imagesやattached_documentと同じ配線パターン。
                                web_search = bool(payload.get("web_search"))
                                if user_text:
                                    # 17日目: 以前は生成中がメインループごと同期ブロックしていた
                                    # ため、次のtext_inputは物理的に受信自体できなかった(=二重起動
                                    # は起こり得なかった)。生成を裏スレッド化した今は受信自体は
                                    # できてしまうため、前のターンがまだ実行中なら明示的に弾く
                                    # (クライアント側もthinking/speaking中は送らない実装だが、
                                    # 二重にガードしておく)。
                                    if turn_task is not None and not turn_task.done():
                                        await send_json(
                                            {
                                                "type": "error",
                                                "stage": "text_input",
                                                "message": "前のターンの応答中のため送信を無視しました",
                                            }
                                        )
                                    else:
                                        await _handle_final_transcript(
                                            user_text,
                                            images,
                                            attached_document,
                                            attached_document_text,
                                            web_search,
                                        )
                while pending_sends:
                    await pending_sends.pop(0)
        except WebSocketDisconnect:
            pass
        finally:
            # 17日目: 切断時、進行中のターンが残っていれば打ち切りを要求してから
            # 後始末する(接続が無いのに裏でOllama生成だけ続く事故を防ぐ)。
            if turn_cancel_event is not None:
                turn_cancel_event.set()
            if turn_task is not None and not turn_task.done():
                try:
                    await turn_task
                except Exception:  # noqa: BLE001 - 切断後の後始末で例外を伝播させない
                    pass
            stt.flush()
            while pending_sends:
                await pending_sends.pop(0)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="voice_gateway: 自前音声UIのFastAPIゲートウェイ")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    import uvicorn  # noqa: PLC0415

    app = create_app()
    # 14日目: run_turn()内のPipe.pipe()呼び出し(Ollama generate/generate_stream、
    # 14日目で追加したweb_search.search())は同期ブロッキングであり、
    # 実行中はイベントループがWSのping/pongに応答できない(9日目ノート⑥残課題・
    # 13日目ノート「引き続き持ち越し」6に記載済みの既知の構造的課題)ため、
    # 既定のws_ping_timeout=20秒だと長い応答生成中にkeepalive timeoutで
    # 強制切断される実害があった。
    # 17日目: run_turn()の消費自体を専用スレッド+asyncio.Queueへ切り出し
    # (ws_endpoint参照)、生成中もイベントループが動き続けるよう修正したため、
    # 上記の「ping/pongに応答できない」制約自体は解消されている。ただし
    # タイムアウトを短く戻す積極的な理由も無いため(長い応答自体は変わらず
    # 起こりうる)、暫定値のまま維持する。
    uvicorn.run(app, host=args.host, port=args.port, ws_ping_interval=60.0, ws_ping_timeout=120.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
