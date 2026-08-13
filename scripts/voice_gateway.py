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
    {"type": "text_input", "text": str, "images": [str, ...]}    # テキスト入力欄の送信ボタン/Enter。
                                                                    # 音声由来・キーボード入力由来を問わず、
                                                                    # ユーザーが内容を確認・確定した発話はすべてここを通る。
                                                                    # "images"(11日目④-1で追加)は任意。
                                                                    # base64エンコード済み画像(data URL prefix無し)の
                                                                    # リストで、指定するとそのターンだけルーターを
                                                                    # 経由せず強制的にDEEP(gemma4:26b)へルーティング
                                                                    # される(run_turn()→support_ai_auto_pipe.Pipe参照)。
                                                                    # 📎の文書添付(/documents)と異なりDBへの永続登録はしない。

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
import base64
import json
import sys
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

from sentence_splitter import SentenceSplitter  # noqa: E402

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


# ---------------------------------------------------------------------------
# run_turn(): WebSocket/FastAPIに依存しない、1ターン分のオーケストレーション
# ---------------------------------------------------------------------------


def _wav_message(text: str, wav_bytes: bytes) -> dict:
    return {"type": "audio", "text": text, "wav_b64": base64.b64encode(wav_bytes).decode("ascii")}


def _sentences_from_reply(reply, splitter: SentenceSplitter) -> Iterator[dict]:
    """`pipe.pipe()`の戻り値(str または トークンのIterator[str])を、
    `{"type": "token", ...}` / `{"type": "sentence", ...}` のdict列に変換する。

    9日目④の設計どおり、FAST/DEEPかつストリーミング要求時のみIterator[str]が返り、
    CODE/CLARIFY/タスク呼び出しはstrのまま返る。どちらの経路でも最終的に
    sentence_splitterへ通してから返す(strの場合もMarkdown除去等の正規化を効かせるため)。
    """
    if isinstance(reply, str):
        for sentence in splitter.feed(reply):
            yield {"type": "sentence", "text": sentence}
        for sentence in splitter.flush():
            yield {"type": "sentence", "text": sentence}
        return

    for token in reply:
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
    """
    yield {"type": "state", "value": "thinking"}

    splitter = SentenceSplitter(**(splitter_kwargs or {}))

    user_message: dict = {"role": "user", "content": user_text}
    if images:
        user_message["images"] = images

    try:
        reply = pipe.pipe(
            body={"messages": [user_message], "stream": True},
            __metadata__={"chat_id": chat_id},
        )
    except Exception as e:  # noqa: BLE001 - Pipe呼び出し失敗の理由をそのまま通知する
        yield {"type": "error", "stage": "pipe", "message": f"{type(e).__name__}: {e}"}
        yield {"type": "state", "value": "idle"}
        return

    for event in _sentences_from_reply(reply, splitter):
        yield event
        if event["type"] != "sentence":
            continue
        sentence = event["text"]
        try:
            wav_bytes = synthesize(sentence)
        except Exception as e:  # noqa: BLE001 - 1文の合成失敗で残りの文の再生を止めない
            yield {"type": "error", "stage": "tts", "message": f"{type(e).__name__}: {e}"}
            continue
        yield _wav_message(sentence, wav_bytes)

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
):
    """FastAPIアプリを組み立てる。

    実運用では引数なしで呼び、実物のPipe/STTEngine/tts_adapterを使う。
    引数はテスト・段階的な差し替えのために用意してある(現時点の自動テストは
    `run_turn()`単体で完結しており、このFastAPIアプリ自体の統合テストは未整備。
    9日目⑥残課題として実機確認と合わせて追加する)。
    """
    if FastAPI is None:
        raise RuntimeError("fastapiが未インストールです(pip install fastapi uvicorn[standard])")

    app = FastAPI()
    static_dir = SCRIPT_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
        try:
            return doc_ingest.ingest_document(filename, data)
        except doc_ingest.UnsupportedFileTypeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001 - 抽出/DB登録失敗の理由をそのまま通知する
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e

    @app.get("/documents")
    def list_documents_endpoint():
        return _doc_ingest().list_documents()

    @app.delete("/documents/{filename}")
    def delete_document_endpoint(filename: str):
        deleted = _doc_ingest().delete_document(filename)
        return {"filename": filename, "deleted": deleted}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        pipe = pipe_factory()
        chat_id = f"voice-{id(websocket)}"

        # マイクは常時ON+VAD自動送信、読み上げ中はマイクOFF(⑥仕様)。
        # 「読み上げ中はSTTへ音声を渡さない」制御はブラウザ側(mic mute)が主で、
        # ここではエコー対策の保険として speaking 中は確定転写のみ受け付ける実装にしてもよいが、
        # 初版はブラウザ側制御を信頼しシンプルに保つ(YAGNI)。

        async def send_json(msg: dict) -> None:
            await websocket.send_json(msg)

        def on_partial(text: str) -> None:
            _schedule(send_json({"type": "partial_transcript", "text": text, "final": False}))

        def on_final(text: str) -> None:
            # 10日目⑦:ウェイクワード自動送信は撤回した。STTが確定させたテキストも
            # AIへは渡さず、partial_transcriptと同じ扱いでクライアントのテキスト入力欄へ
            # 反映するだけにとどめる(ユーザーが内容を確認・修正して送信ボタン/Enterを
            # 押した時だけ、下のtext_input分岐からrun_turn()が呼ばれる)。
            # "final": True(2026-08-12追加)は、VADが発話終了を検知しfaster-whisperの
            # 確定転写が終わった瞬間を外部(ws_e2e_bench.py)が識別するためのフラグ。
            _schedule(send_json({"type": "partial_transcript", "text": text, "final": True}))

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

        async def _handle_final_transcript(text: str, images: list[str] | None = None) -> None:
            await send_json({"type": "final_transcript", "text": text})
            await send_json({"type": "state", "value": "speaking"})
            for event in run_turn(pipe, chat_id, text, synthesize=synthesize, images=images):
                await send_json(event)

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
                            if payload.get("type") == "text_input":
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
                                if user_text:
                                    await _handle_final_transcript(user_text, images)
                while pending_sends:
                    await pending_sends.pop(0)
        except WebSocketDisconnect:
            pass
        finally:
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
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
