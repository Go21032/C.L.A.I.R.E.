"""
tests/test_voice_gateway.py
------------------------------
9日目ノート⑥:`voice_gateway.run_turn()` のユニットテスト。

`run_turn()`はFastAPI/WebSocketに依存しない「1ターン分の会話オーケストレーション」の
純粋なジェネレータ。WebSocket自体・実際のPipe/TTS/STTはここでは検証しない
(実機確認は10日目ノート⑤の残課題)。ここでは「トークン→文→音声」のオーケストレーション、
エラー時にUIが無反応にならないことをフェイクで検証する。

(10日目ノート②で追加したウェイクワード判定(`decide_wake_action()`)は、⑦の設計変更で
撤回・削除した。`wake_word.py`/`tests/test_wake_word.py`も合わせて削除済み。経緯は
10日目ノート⑦参照。13日目②で「自動送信してよいかの送信ゲート」として`wake_word.py`を
再導入し、`ws_endpoint`のSTTコールバックから`wake_detected`イベントを送るよう配線した。
`TestWakeWordWiring`はその配線だけを検証する(判定ロジック自体はtest_wake_word.py)。)
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from voice_gateway import create_app, run_turn  # noqa: E402


class FakePipe:
    """support_ai_auto_pipe.Pipe互換の最小フェイク。pipe()の戻り値を差し替えるだけ。"""

    def __init__(self, reply):
        self._reply = reply
        self.calls: list[dict] = []

    def pipe(self, body, __user__=None, __metadata__=None, **_):
        self.calls.append({"body": body, "metadata": __metadata__})
        return self._reply


def fake_synthesize_factory(fail_on: set[str] | None = None):
    calls: list[str] = []
    fail_on = fail_on or set()

    def synthesize(text: str) -> bytes:
        calls.append(text)
        if text in fail_on:
            raise RuntimeError(f"simulated TTS failure for: {text}")
        return f"WAV({text})".encode("utf-8")

    return synthesize, calls


class TestStreamingReply(unittest.TestCase):
    def test_tokens_are_split_into_sentences_and_synthesized_in_order(self):
        # SentenceSplitterの既定min_chars=10だと短文は次の文と結合される
        # (sentence_splitter.py側の意図した仕様。tests/test_sentence_splitter.pyでも
        # 単独の分割挙動を見たいテストではmin_chars=0にしている)。
        # ここではrun_turn()の「トークン→文→音声」の順序そのものを見たいので同様に無効化する。
        tokens = iter(["こんにち", "はとても", "元気です。", "元気ですかとても"])
        pipe = FakePipe(tokens)
        synthesize, synth_calls = fake_synthesize_factory()

        events = list(
            run_turn(
                pipe, "chat-1", "こんにちは", synthesize=synthesize, splitter_kwargs={"min_chars": 0}
            )
        )

        types = [e["type"] for e in events]
        self.assertEqual(types[0], "state")
        self.assertEqual(events[0]["value"], "thinking")
        self.assertIn("token", types)
        sentence_events = [e for e in events if e["type"] == "sentence"]
        self.assertEqual(
            [e["text"] for e in sentence_events], ["こんにちはとても元気です。", "元気ですかとても"]
        )
        audio_events = [e for e in events if e["type"] == "audio"]
        self.assertEqual(
            [e["text"] for e in audio_events], ["こんにちはとても元気です。", "元気ですかとても"]
        )
        self.assertEqual(audio_events[0]["wav_b64"], _b64("WAV(こんにちはとても元気です。)"))
        self.assertEqual(types[-1], "state")
        self.assertEqual(events[-1]["value"], "idle")
        # 合成は文が確定した順に呼ばれる(=最初の文の再生が早く始まる、8日目の1分問題の解消策)
        self.assertEqual(synth_calls, ["こんにちはとても元気です。", "元気ですかとても"])

    def test_pipe_is_called_with_streaming_flag_and_chat_id(self):
        pipe = FakePipe(iter(["はい。"]))
        synthesize, _ = fake_synthesize_factory()

        list(run_turn(pipe, "chat-42", "test", synthesize=synthesize))

        call = pipe.calls[0]
        self.assertEqual(call["body"]["stream"], True)
        self.assertEqual(call["body"]["messages"], [{"role": "user", "content": "test"}])
        self.assertEqual(call["metadata"]["chat_id"], "chat-42")

    def test_images_are_included_in_user_message_when_given(self):
        # 11日目④-1: images引数を渡すと、最後のuserメッセージ辞書へ"images"キーとして
        # 載る(support_ai_auto_pipe.Pipe._extract_last_user_images()が読む独自convention)。
        pipe = FakePipe(iter(["この画像は…"]))
        synthesize, _ = fake_synthesize_factory()

        list(run_turn(pipe, "chat-1", "この画像は何?", synthesize=synthesize, images=["QUJD"]))

        call = pipe.calls[0]
        self.assertEqual(
            call["body"]["messages"],
            [{"role": "user", "content": "この画像は何?", "images": ["QUJD"]}],
        )

    def test_no_images_keeps_message_without_images_key(self):
        """後方互換の確認: imagesを渡さない既存呼び出し元はメッセージ辞書にimagesキーが付かない。"""
        pipe = FakePipe(iter(["はい。"]))
        synthesize, _ = fake_synthesize_factory()

        list(run_turn(pipe, "chat-1", "test", synthesize=synthesize))

        self.assertNotIn("images", pipe.calls[0]["body"]["messages"][0])

    def test_attached_document_is_included_in_user_message_when_given(self):
        # 13日目「直近添付ファイルを自動優先」対応: attached_document引数を渡すと、
        # 最後のuserメッセージ辞書へ"attached_document"キーとして載る
        # (support_ai_auto_pipe.Pipe._extract_last_user_attached_document()が読む
        # 独自convention。imagesと同じ配線パターン)。
        pipe = FakePipe(iter(["要約しました。"]))
        synthesize, _ = fake_synthesize_factory()

        list(
            run_turn(
                pipe,
                "chat-1",
                "このファイルの内容を要約して",
                synthesize=synthesize,
                attached_document="workout.pdf",
            )
        )

        call = pipe.calls[0]
        self.assertEqual(
            call["body"]["messages"],
            [
                {
                    "role": "user",
                    "content": "このファイルの内容を要約して",
                    "attached_document": "workout.pdf",
                }
            ],
        )

    def test_no_attached_document_keeps_message_without_attached_document_key(self):
        """後方互換の確認: attached_documentを渡さない既存呼び出し元は
        メッセージ辞書にattached_documentキーが付かない。"""
        pipe = FakePipe(iter(["はい。"]))
        synthesize, _ = fake_synthesize_factory()

        list(run_turn(pipe, "chat-1", "test", synthesize=synthesize))

        self.assertNotIn("attached_document", pipe.calls[0]["body"]["messages"][0])

    def test_web_search_is_included_in_user_message_when_given(self):
        # 14日目: 13日目④で部品実装のみだったWeb検索を結線。web_search=Trueを渡すと、
        # imagesやattached_documentと同じ要領で最後のuserメッセージ辞書へ
        # "web_search"キーとして載る(support_ai_auto_pipe.Pipe._extract_last_user_web_search()
        # が読む独自convention)。
        pipe = FakePipe(iter(["今日は晴れです。"]))
        synthesize, _ = fake_synthesize_factory()

        list(
            run_turn(
                pipe,
                "chat-1",
                "今日の東京の天気は?",
                synthesize=synthesize,
                web_search=True,
            )
        )

        call = pipe.calls[0]
        self.assertEqual(
            call["body"]["messages"],
            [{"role": "user", "content": "今日の東京の天気は?", "web_search": True}],
        )

    def test_no_web_search_keeps_message_without_web_search_key(self):
        """後方互換の確認: web_searchを渡さない既存呼び出し元は
        メッセージ辞書にweb_searchキーが付かない。"""
        pipe = FakePipe(iter(["はい。"]))
        synthesize, _ = fake_synthesize_factory()

        list(run_turn(pipe, "chat-1", "test", synthesize=synthesize))

        self.assertNotIn("web_search", pipe.calls[0]["body"]["messages"][0])


class TestTimingRecorder(unittest.TestCase):
    """12日目①残課題: VRAM推移からの推測に頼らず、text_input受信〜応答完了の所要時間を
    正確に実測するためのtiming_recorderフック。"""

    def test_timing_recorder_is_called_once_with_duration_and_metadata(self):
        pipe = FakePipe(iter(["はい。"]))
        synthesize, _ = fake_synthesize_factory()
        clock_values = iter([100.0, 107.5])  # start, end
        recorded: list[dict] = []

        list(
            run_turn(
                pipe,
                "chat-1",
                "この画像は何?",
                synthesize=synthesize,
                images=["QUJD"],
                clock=lambda: next(clock_values),
                timing_recorder=recorded.append,
            )
        )

        self.assertEqual(len(recorded), 1)
        row = recorded[0]
        self.assertEqual(row["chat_id"], "chat-1")
        self.assertEqual(row["text"], "この画像は何?")
        self.assertEqual(row["images_count"], 1)
        self.assertEqual(row["start_ts"], 100.0)
        self.assertEqual(row["end_ts"], 107.5)
        self.assertAlmostEqual(row["duration_sec"], 7.5)

    def test_timing_recorder_is_optional(self):
        """timing_recorderを渡さない既存呼び出し元(⑨のテストは全部これ)は従来どおり動く。"""
        pipe = FakePipe(iter(["はい。"]))
        synthesize, _ = fake_synthesize_factory()

        events = list(run_turn(pipe, "chat-1", "test", synthesize=synthesize))

        self.assertEqual(events[-1], {"type": "state", "value": "idle"})

    def test_timing_recorder_runs_even_when_pipe_raises(self):
        """Pipe呼び出し失敗時もタイミングを記録できる(エラー分岐からもduration_secが欲しい)。"""

        class ExplodingPipe:
            def pipe(self, body, __user__=None, __metadata__=None, **_):
                raise RuntimeError("boom")

        synthesize, _ = fake_synthesize_factory()
        clock_values = iter([10.0, 12.0])
        recorded: list[dict] = []

        list(
            run_turn(
                ExplodingPipe(),
                "chat-1",
                "test",
                synthesize=synthesize,
                clock=lambda: next(clock_values),
                timing_recorder=recorded.append,
            )
        )

        self.assertEqual(len(recorded), 1)
        self.assertAlmostEqual(recorded[0]["duration_sec"], 2.0)


class TestDefaultTimingRecorderCsv(unittest.TestCase):
    """voice_gateway.make_csv_timing_recorder(): 実運用でCSVへ1行追記するデフォルト実装。"""

    def test_appends_header_and_row_on_first_call(self):
        import csv
        import tempfile

        from voice_gateway import make_csv_timing_recorder

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "response_timing.csv"
            recorder = make_csv_timing_recorder(csv_path)

            recorder(
                {
                    "chat_id": "chat-1",
                    "text": "こんにちは",
                    "images_count": 0,
                    "start_ts": 1000.0,
                    "end_ts": 1003.25,
                    "duration_sec": 3.25,
                }
            )

            with csv_path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
        self.assertEqual(
            rows[0], ["start_iso", "chat_id", "text", "images_count", "duration_sec"]
        )
        self.assertEqual(rows[1][1:], ["chat-1", "こんにちは", "0", "3.25"])

    def test_second_call_appends_without_duplicating_header(self):
        import csv
        import tempfile

        from voice_gateway import make_csv_timing_recorder

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "response_timing.csv"
            recorder = make_csv_timing_recorder(csv_path)
            row = {
                "chat_id": "chat-1",
                "text": "test",
                "images_count": 0,
                "start_ts": 0.0,
                "end_ts": 1.0,
                "duration_sec": 1.0,
            }
            recorder(row)
            recorder(row)

            with csv_path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
        self.assertEqual(len(rows), 3)  # header + 2 data rows
        self.assertEqual(rows.count(["start_iso", "chat_id", "text", "images_count", "duration_sec"]), 1)


class TestNonStreamingReply(unittest.TestCase):
    def test_plain_string_reply_is_still_split_and_spoken(self):
        """CODE/CLARIFYルート等、pipe()がstrを返す経路(9日目④で意図的にストリーミング対象外)。"""
        pipe = FakePipe("承知しましたので、これから実行します。準備ができました。")
        synthesize, synth_calls = fake_synthesize_factory()

        events = list(
            run_turn(pipe, "chat-1", "実行して", synthesize=synthesize, splitter_kwargs={"min_chars": 0})
        )

        sentence_events = [e["text"] for e in events if e["type"] == "sentence"]
        self.assertEqual(sentence_events, ["承知しましたので、これから実行します。", "準備ができました。"])
        self.assertEqual(synth_calls, ["承知しましたので、これから実行します。", "準備ができました。"])


class TestErrorHandling(unittest.TestCase):
    def test_pipe_exception_yields_error_event_and_still_reaches_idle(self):
        class ExplodingPipe:
            def pipe(self, body, __user__=None, __metadata__=None, **_):
                raise RuntimeError("boom")

        synthesize, synth_calls = fake_synthesize_factory()

        events = list(run_turn(ExplodingPipe(), "chat-1", "test", synthesize=synthesize))

        self.assertTrue(any(e["type"] == "error" for e in events))
        self.assertEqual(events[-1], {"type": "state", "value": "idle"})
        self.assertEqual(synth_calls, [])

    def test_tts_failure_on_one_sentence_does_not_stop_the_others(self):
        pipe = FakePipe(iter(["これはだめな文です。", "こちらは大丈夫な文です。"]))
        synthesize, synth_calls = fake_synthesize_factory(fail_on={"これはだめな文です。"})

        events = list(
            run_turn(pipe, "chat-1", "test", synthesize=synthesize, splitter_kwargs={"min_chars": 0})
        )

        error_events = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0]["stage"], "tts")
        audio_events = [e["text"] for e in events if e["type"] == "audio"]
        self.assertEqual(audio_events, ["こちらは大丈夫な文です。"])
        self.assertEqual(events[-1], {"type": "state", "value": "idle"})


class TestCitationsAreNotSpoken(unittest.TestCase):
    """15日目(指示2): 出典/URL行は画面表示(sentenceイベント)はするが、TTSには回さない。"""

    def test_source_lines_are_shown_but_not_synthesized(self):
        reply = (
            "クロスフィットの代表的な自重ワークアウトです。\n"
            "出典:\n"
            "[1] クロスフィット初心者 - Reddit - https://www.reddit.com/r/crossfit/comments/x\n"
            "[2] Tom Holland's CrossFit Workout - https://www.esquire.com/a\n"
        )
        pipe = FakePipe(reply)
        synthesize, synth_calls = fake_synthesize_factory()

        events = list(
            run_turn(pipe, "chat-1", "Cindyについて教えて", synthesize=synthesize, splitter_kwargs={"min_chars": 0})
        )

        sentence_texts = [e["text"] for e in events if e["type"] == "sentence"]
        self.assertTrue(any("出典" in t or "[1]" in t for t in sentence_texts))
        # 出典/URLを含む行はTTSへ渡されない(URLを読み上げる/timeoutする実害の再発防止)
        self.assertEqual(synth_calls, ["クロスフィットの代表的な自重ワークアウトです。"])
        audio_texts = [e["text"] for e in events if e["type"] == "audio"]
        self.assertEqual(audio_texts, ["クロスフィットの代表的な自重ワークアウトです。"])


class TestCancellation(unittest.TestCase):
    """17日目「誤送信の即時停止」対応: `run_turn(cancel_event=...)`による早期打ち切り。

    ここでは`run_turn()`単体(WebSocket抜き)の挙動だけを検証する。実際に
    Ollamaへのストリーム接続が閉じられる(=GPU計算も止まる)ことは、
    `reply`側のgeneratorが`.close()`で`finally`を実行するかどうかで確認する
    (ollama_client.generate_stream()が返す実物のgeneratorと同じ構造)。
    """

    def test_cancel_requested_before_any_token_stops_without_synthesizing(self):
        def reply_gen():
            yield "a"
            yield "b"

        pipe = FakePipe(reply_gen())
        synthesize, synth_calls = fake_synthesize_factory()
        cancel_event = threading.Event()
        cancel_event.set()  # pipe.pipe()が返ってきた時点で、既に中断要求済み

        events = list(run_turn(pipe, "chat-1", "test", synthesize=synthesize, cancel_event=cancel_event))

        self.assertEqual(events, [{"type": "state", "value": "thinking"}, {"type": "state", "value": "idle"}])
        self.assertEqual(synth_calls, [])

    def test_cancel_mid_stream_stops_remaining_tokens_and_closes_reply(self):
        closed = []

        def reply_gen():
            try:
                yield "これは"
                yield "長い"
                yield "文章です。"
            finally:
                # ollama_client._iter_ndjson_response()と同じ構造(finallyでresp.close())。
                # ここが呼ばれる=中断時に実際にストリーム接続が閉じられたことの確認。
                closed.append(True)

        pipe = FakePipe(reply_gen())
        synthesize, synth_calls = fake_synthesize_factory()
        cancel_event = threading.Event()

        events = []
        for event in run_turn(pipe, "chat-1", "test", synthesize=synthesize, cancel_event=cancel_event):
            events.append(event)
            if event.get("type") == "token" and event["text"] == "これは":
                cancel_event.set()  # 1トークン目を受け取った直後に中断要求が来たことにする

        token_texts = [e["text"] for e in events if e["type"] == "token"]
        self.assertEqual(token_texts, ["これは"])  # 2トークン目以降は処理されない
        self.assertEqual(synth_calls, [])  # 文が確定する前に打ち切られたので合成は一切走らない
        self.assertEqual(events[-1], {"type": "state", "value": "idle"})
        self.assertEqual(closed, [True])  # ストリーム接続が実際に閉じられた

    def test_cancel_after_a_sentence_is_spoken_stops_before_the_next_one(self):
        def reply_gen():
            yield "最初の文です。"
            yield "次の文です。"

        pipe = FakePipe(reply_gen())
        synthesize, synth_calls = fake_synthesize_factory()
        cancel_event = threading.Event()

        events = []
        for event in run_turn(
            pipe, "chat-1", "test", synthesize=synthesize, cancel_event=cancel_event,
            splitter_kwargs={"min_chars": 0},
        ):
            events.append(event)
            if event["type"] == "audio":
                cancel_event.set()  # 1文目の音声が出た直後に中断要求

        audio_texts = [e["text"] for e in events if e["type"] == "audio"]
        self.assertEqual(audio_texts, ["最初の文です。"])
        self.assertEqual(synth_calls, ["最初の文です。"])
        self.assertEqual(events[-1], {"type": "state", "value": "idle"})

    def test_without_cancel_event_behaves_exactly_as_before(self):
        """後方互換: cancel_eventを渡さない既存呼び出し元は従来どおり動く。"""
        pipe = FakePipe(iter(["hello", " world."]))
        synthesize, synth_calls = fake_synthesize_factory()

        events = list(run_turn(pipe, "chat-1", "hi", synthesize=synthesize))

        self.assertEqual(events[-1], {"type": "state", "value": "idle"})
        self.assertTrue(synth_calls)


def _b64(s: str) -> str:
    import base64

    return base64.b64encode(s.encode("utf-8")).decode("ascii")


class FakeSttEngine:
    """13日目②配線テスト用フェイク。feed_audio()を呼ぶとon_partial/on_finalへ
    あらかじめ渡された固定テキストをそのまま流す(Vosk/faster-whisperには一切触れない)。

    19日目 修正: `voice_gateway._check_wake_word`がウェイクワード検出時に
    `stt.force_finalize_pending()`を呼ぶよう配線したため、フェイクにも同名メソッドを
    用意する。`force_finalize_on_final_text`を渡すと、実物のSTTEngineと同じように
    「force_finalize_pending()の中でon_finalが(同じ/別の)テキストで再度呼ばれる」
    状況を模擬できる(再入防止ガードの検証用)。
    """

    def __init__(
        self,
        on_partial,
        on_final,
        on_stt_error,
        *,
        partial_text="",
        final_text="",
        force_finalize_on_final_text: str | None = None,
    ):
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_stt_error = on_stt_error
        self._partial_text = partial_text
        self._final_text = final_text
        self._force_finalize_on_final_text = force_finalize_on_final_text
        self.force_finalize_calls = 0

    def feed_audio(self, chunk: bytes) -> None:
        if self._partial_text:
            self._on_partial(self._partial_text)
        if self._final_text:
            self._on_final(self._final_text)

    def force_finalize_pending(self) -> None:
        self.force_finalize_calls += 1
        if self._force_finalize_on_final_text is not None:
            self._on_final(self._force_finalize_on_final_text)

    def flush(self) -> None:
        pass


class TestWakeWordWiring(unittest.TestCase):
    """13日目②:`ws_endpoint`のSTTコールバックがウェイクワード検出時に
    `wake_detected`をWSへ送ること(判定自体はwake_word.detect_wake_wordに委譲)。"""

    def _connect(self, *, partial_text="", final_text=""):
        from fastapi.testclient import TestClient

        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error, partial_text=partial_text, final_text=final_text
            ),
            synthesize=lambda text: b"",
        )
        client = TestClient(app)
        return client.websocket_connect("/ws")

    def test_wake_detected_is_sent_when_partial_contains_wake_word(self):
        with self._connect(partial_text="クレア 明日の天気は") as ws:
            ws.send_bytes(b"\x00\x01")
            msg1 = ws.receive_json()
            msg2 = ws.receive_json()

        self.assertEqual(msg1["type"], "partial_transcript")
        self.assertEqual(msg2, {"type": "wake_detected", "text_after": "明日の天気は"})

    def test_wake_word_triggers_an_immediate_spoken_ack(self):
        """指示1: 「クレア」検出直後、LLM応答を待たずに固定音声応答が届くこと。"""

        def synth(text: str) -> bytes:
            return f"WAV({text})".encode("utf-8")

        from fastapi.testclient import TestClient

        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error, partial_text="クレア 明日の天気は"
            ),
            synthesize=synth,
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_bytes(b"\x00\x01")
            msg1 = ws.receive_json()  # partial_transcript
            msg2 = ws.receive_json()  # wake_detected
            msg3 = ws.receive_json()  # 即時音声応答

        self.assertEqual(msg1["type"], "partial_transcript")
        self.assertEqual(msg2["type"], "wake_detected")
        self.assertEqual(msg3["type"], "audio")
        self.assertEqual(msg3["text"], "はい、ごうさま")

    def test_wake_word_ack_is_not_repeated_within_cooldown(self):
        """同じ発話中にon_partial/on_finalの両方で検出されても、応答音声は1回だけ鳴る。"""

        def synth(text: str) -> bytes:
            return f"WAV({text})".encode("utf-8")

        from fastapi.testclient import TestClient

        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error, partial_text="クレア", final_text="クレア"
            ),
            synthesize=synth,
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_bytes(b"\x00\x01")
            received = [ws.receive_json() for _ in range(5)]

        audio_msgs = [m for m in received if m["type"] == "audio"]
        self.assertEqual(len(audio_msgs), 1)
        self.assertEqual(audio_msgs[0]["text"], "はい、ごうさま")

    def test_no_wake_detected_when_wake_word_absent(self):
        with self._connect(partial_text="明日の天気は") as ws:
            ws.send_bytes(b"\x00\x01")
            msg1 = ws.receive_json()

        self.assertEqual(msg1, {"type": "partial_transcript", "text": "明日の天気は", "final": False})

    def test_wake_word_detection_forces_stt_to_finalize_immediately(self):
        """19日目 修正: ウェイクワード検出時、VADの無音保持を待たず`stt`側へ即座に
        区切りを強制させる(`force_finalize_pending()`)。これにより、直後3秒未満の
        ポーズで話し始める次のコマンドがウェイクワード発話と連結されなくなる。"""
        from fastapi.testclient import TestClient

        stt_holder: list = []

        def factory(on_partial, on_final, on_stt_error):
            engine = FakeSttEngine(on_partial, on_final, on_stt_error, partial_text="クレア起動")
            stt_holder.append(engine)
            return engine

        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=factory,
            synthesize=lambda text: b"",
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_bytes(b"\x00\x01")
            ws.receive_json()  # partial_transcript
            ws.receive_json()  # wake_detected
            ws.receive_json()  # audio(はい、ごうさま)

        self.assertEqual(stt_holder[0].force_finalize_calls, 1)

    def test_wake_word_re_detected_during_forced_finalize_does_not_loop(self):
        """19日目 修正の再入防止ガード: `force_finalize_pending()`が内部で同じ
        ウェイクワード入りテキストを`on_final`へもう一度流しても(実物のSTTEngineの
        `_finalize()`と同じ挙動)、`_check_wake_word`が無限に呼び合ってハング/多重の
        `wake_detected`送信を起こさないこと。"""
        from fastapi.testclient import TestClient

        stt_holder: list = []

        def factory(on_partial, on_final, on_stt_error):
            engine = FakeSttEngine(
                on_partial,
                on_final,
                on_stt_error,
                partial_text="クレア起動",
                force_finalize_on_final_text="クレア起動",  # 実物同様、確定時も同じ文言で再通知
            )
            stt_holder.append(engine)
            return engine

        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=factory,
            synthesize=lambda text: b"",
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_bytes(b"\x00\x01")
            received = [ws.receive_json() for _ in range(4)]
            # ここでハングせず4件受信できた時点で、無限再帰していないことの証拠になる。
            ws.send_json({"type": "text_input", "text": "接続が生きていることの確認"})
            confirm = ws.receive_json()

        wake_msgs = [m for m in received if m["type"] == "wake_detected"]
        self.assertEqual(len(wake_msgs), 1)  # 再入分は握りつぶされ、二重送信されない
        self.assertEqual(stt_holder[0].force_finalize_calls, 1)
        self.assertEqual(confirm, {"type": "final_transcript", "text": "接続が生きていることの確認"})


class TestCancelTurnWiring(unittest.TestCase):
    """17日目「誤送信の即時停止」対応: WSの"cancel_turn"メッセージが実際に応答生成を
    打ち切ること、かつ生成中でも受信ループが固まらず"cancel_turn"を受け取れることを検証する。

    生成側(FakePipeが返すgenerator)をテスト側から`threading.Event`で意図的に足止めして
    「まだ生成が終わっていない」状態を作り、その最中にcancel_turnを送っても
    (旧実装のように受信ループごと固まって)無視されないことを確認する。
    """

    def test_cancel_turn_stops_generation_before_remaining_tokens_are_sent(self):
        import time

        release = threading.Event()
        started = threading.Event()

        def slow_reply():
            yield "最初のトークン"
            started.set()
            release.wait(timeout=5)  # cancel_turnが届くまで、実際の長い生成を模擬的に足止めする
            yield "本来なら続くはずのトークン"

        def synth(text: str) -> bytes:
            return b""

        from fastapi.testclient import TestClient

        app = create_app(
            pipe_factory=lambda: FakePipe(slow_reply()),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error
            ),
            synthesize=synth,
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "text_input", "text": "こんにちは"})
            msg_final = ws.receive_json()
            msg_speaking = ws.receive_json()
            msg_thinking = ws.receive_json()
            msg_token1 = ws.receive_json()

            # 生成が実際にブロック中の地点(=まだ終わっていない)まで進んだことを確認してから、
            # 中断メッセージを送る。旧実装(メインループが同期ブロック)ならこの送信自体が
            # 生成完了までサーバに届かず、テストがreceive_json()でハングして失敗する。
            self.assertTrue(started.wait(timeout=5))
            ws.send_json({"type": "cancel_turn"})
            time.sleep(0.2)  # サーバがcancel_turnを処理する猶予(受信ループが動き続けている証拠にもなる)
            release.set()  # 足止めを解除。cancel_event済みなので後続トークンは握りつぶされるはず

            msg_after_cancel = ws.receive_json()

        self.assertEqual(msg_final, {"type": "final_transcript", "text": "こんにちは"})
        self.assertEqual(msg_speaking, {"type": "state", "value": "speaking"})
        self.assertEqual(msg_thinking, {"type": "state", "value": "thinking"})
        self.assertEqual(msg_token1, {"type": "token", "text": "最初のトークン"})
        # 2個目のトークンは送られず、いきなりidleへ戻る
        self.assertEqual(msg_after_cancel, {"type": "state", "value": "idle"})

    def test_cancel_turn_without_an_active_turn_is_ignored(self):
        """進行中のターンが無いときにcancel_turnを送っても何も起きない(エラーにもならない)。"""
        from fastapi.testclient import TestClient

        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error
            ),
            synthesize=lambda text: b"",
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "cancel_turn"})
            # 何も送り返してこないはず。次に自分から送ったtext_inputへの応答が正常に届くことで
            # 接続が壊れていないことを確認する。
            ws.send_json({"type": "text_input", "text": "テスト"})
            msg = ws.receive_json()

        self.assertEqual(msg, {"type": "final_transcript", "text": "テスト"})


class TestDocumentEndpoints(unittest.TestCase):
    """11日目④: PDF/Word/Excel/PowerPointナレッジ取り込みのHTTPエンドポイント(/documents)。

    実際のLanceDB/embeddingには触れず、rag_memory.doc_ingest の各関数をモックへ差し替えて
    ルーティング・エラーハンドリング(未対応拡張子・サイズ超過)だけを検証する。
    """

    def setUp(self):
        from fastapi.testclient import TestClient

        # pipe_factory/stt_engine_factory/synthesizeはこのテストでは使わないが、
        # 実物(Ollama/Vosk/VOICEVOX)への依存を避けるためダミーを渡す(9日目⑥のcreate_app設計どおり)。
        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=lambda *_: object(),
            synthesize=lambda text: b"",
        )
        self.client = TestClient(app)

        import doc_ingest

        self.doc_ingest = doc_ingest

    def test_upload_unsupported_extension_returns_400(self):
        resp = self.client.post(
            "/documents", files={"file": ("image.png", io_bytes(b"dummy"), "image/png")}
        )
        self.assertEqual(resp.status_code, 400)

    def test_upload_success_calls_ingest_and_returns_result(self):
        # voice_gateway.py の /documents は DOC_INLINE_MAX_CHARS 判定のため、
        # ingest_document() の前に doc_ingest.extract_text() を1回呼んで実テキストを
        # 得る設計になっている(ingest_document内での再抽出を避けるため)。ここではPDFの
        # 実バイナリを用意していないダミーデータなので、extract_text()もモックして
        # 実際のpdfplumberによるPDFパースを走らせない(ルーティング/配線の検証に限定する)。
        with (
            patch.object(self.doc_ingest, "extract_text", return_value="dummy text") as mocked_extract,
            patch.object(
                self.doc_ingest, "ingest_document", return_value={"filename": "a.pdf", "chunks": 3}
            ) as mocked_ingest,
        ):
            resp = self.client.post(
                "/documents", files={"file": ("a.pdf", io_bytes(b"%PDF-1.4 dummy"), "application/pdf")}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"filename": "a.pdf", "chunks": 3, "text": "dummy text"})
        mocked_extract.assert_called_once()
        mocked_ingest.assert_called_once()

    def test_upload_oversized_file_returns_413(self):
        import voice_gateway as vg

        oversized = b"x" * (vg.DOC_UPLOAD_MAX_BYTES + 1)
        resp = self.client.post(
            "/documents", files={"file": ("big.pdf", io_bytes(oversized), "application/pdf")}
        )
        self.assertEqual(resp.status_code, 413)

    def test_list_documents_returns_doc_ingest_result(self):
        fake_list = [{"filename": "a.pdf", "chunks": 3, "date": "2026-08-13T00:00:00"}]
        with patch.object(self.doc_ingest, "list_documents", return_value=fake_list):
            resp = self.client.get("/documents")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), fake_list)

    def test_delete_document_returns_deleted_count(self):
        with patch.object(self.doc_ingest, "delete_document", return_value=2) as mocked:
            resp = self.client.delete("/documents/a.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"filename": "a.pdf", "deleted": 2})
        mocked.assert_called_once_with("a.pdf")


class TestDocumentTextEndpoint(unittest.TestCase):
    """14日目①: ピン留めしたファイルの全文をクライアントが取り直すための経路。

    サーバ再起動やページリロード後もピンを維持するため、クライアントは
    localStorageに覚えたファイル名だけを頼りに、このエンドポイントで全文を
    取り直す(⓪-3で死んでいたattached_document_text経路をここで生かす)。
    """

    def setUp(self):
        from fastapi.testclient import TestClient

        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=lambda *_: object(),
            synthesize=lambda text: b"",
        )
        self.client = TestClient(app)

        import doc_ingest

        self.doc_ingest = doc_ingest

    def test_returns_extracted_text_when_under_threshold(self):
        with patch.object(self.doc_ingest, "get_document_text", return_value="腕立て30回") as mocked:
            resp = self.client.get("/documents/workout.pdf/text")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"filename": "workout.pdf", "text": "腕立て30回"})
        mocked.assert_called_once_with("workout.pdf")

    def test_returns_null_text_when_over_inline_threshold(self):
        import voice_gateway as vg

        huge_text = "x" * (vg.DOC_INLINE_MAX_CHARS + 1)
        with patch.object(self.doc_ingest, "get_document_text", return_value=huge_text):
            resp = self.client.get("/documents/huge.pdf/text")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["text"])

    def test_returns_404_for_unregistered_filename(self):
        with patch.object(self.doc_ingest, "get_document_text", return_value=None):
            resp = self.client.get("/documents/unknown.pdf/text")
        self.assertEqual(resp.status_code, 404)


class TestSessionEndpoints(unittest.TestCase):
    """14日目②: チャット履歴の永続化(GET/POST/PATCH/DELETE /sessions系)。

    session_store.SessionStoreはtmp_pathへ差し替え、実ファイルを汚さずに検証する。
    """

    def setUp(self):
        import tempfile

        from fastapi.testclient import TestClient

        from session_store import SessionStore

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = SessionStore(root=Path(self._tmpdir.name))
        app = create_app(
            pipe_factory=lambda: FakePipe("dummy"),
            stt_engine_factory=lambda *_: object(),
            synthesize=lambda text: b"",
            session_store=self.store,
        )
        self.client = TestClient(app)

    def test_post_sessions_creates_a_new_session(self):
        resp = self.client.post("/sessions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["session_id"].startswith("sess-"))
        self.assertEqual(body["title"], "新しい会話")

    def test_get_sessions_lists_created_sessions(self):
        self.client.post("/sessions")
        self.client.post("/sessions")
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)

    def test_get_session_by_id_returns_turns(self):
        s = self.client.post("/sessions").json()
        self.store.append_turn(s["session_id"], role="user", text="こんにちは", route="FAST")
        resp = self.client.get(f"/sessions/{s['session_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["turns"]), 1)

    def test_get_session_unknown_id_returns_404(self):
        resp = self.client.get("/sessions/sess-does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_patch_session_renames_it(self):
        s = self.client.post("/sessions").json()
        resp = self.client.patch(f"/sessions/{s['session_id']}", json={"title": "筋トレメニュー相談"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "筋トレメニュー相談")

    def test_patch_session_unknown_id_returns_404(self):
        resp = self.client.patch("/sessions/sess-does-not-exist", json={"title": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_delete_session_removes_it(self):
        s = self.client.post("/sessions").json()
        resp = self.client.delete(f"/sessions/{s['session_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get("/sessions").json(), [])

    def test_get_sessions_with_query_filters_by_title_and_body(self):
        s1 = self.client.post("/sessions").json()
        self.store.append_turn(s1["session_id"], role="user", text="筋トレメニューを考えて", route="FAST")
        s2 = self.client.post("/sessions").json()
        self.store.append_turn(s2["session_id"], role="user", text="明日の天気は?", route="FAST")

        resp = self.client.get("/sessions", params={"q": "筋トレ"})
        ids = [s["session_id"] for s in resp.json()]
        self.assertEqual(ids, [s1["session_id"]])


class TestSessionPersistenceWiring(unittest.TestCase):
    """14日目②: `select_session`メッセージでchat_id=session_idへ揃え、
    ターン確定のたびにsession_store.append_turn()が呼ばれること。
    """

    def setUp(self):
        import tempfile

        from session_store import SessionStore

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = SessionStore(root=Path(self._tmpdir.name))

    def _make_app(self, reply="dummy応答"):
        return create_app(
            pipe_factory=lambda: FakePipe(reply),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error
            ),
            synthesize=lambda text: b"",
            session_store=self.store,
        )

    def test_select_session_switches_chat_id_used_by_pipe(self):
        from fastapi.testclient import TestClient

        session = self.store.create_session()
        pipe_holder: list = []

        def factory():
            pipe = FakePipe("こんにちは")
            pipe_holder.append(pipe)
            return pipe

        app = create_app(
            pipe_factory=factory,
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error
            ),
            synthesize=lambda text: b"",
            session_store=self.store,
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_session", "session_id": session.session_id})
            ws.send_json({"type": "text_input", "text": "テスト"})
            while True:
                msg = ws.receive_json()
                if msg == {"type": "state", "value": "idle"}:
                    break

        self.assertEqual(pipe_holder[0].calls[0]["metadata"]["chat_id"], session.session_id)

    def test_turns_are_persisted_to_the_selected_session(self):
        from fastapi.testclient import TestClient

        session = self.store.create_session()
        app = self._make_app(reply="はい、承知しました")
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_session", "session_id": session.session_id})
            ws.send_json({"type": "text_input", "text": "明日の予定を教えて"})
            while True:
                msg = ws.receive_json()
                if msg == {"type": "state", "value": "idle"}:
                    break

        loaded = self.store.load_session(session.session_id)
        self.assertEqual([t.role for t in loaded.turns], ["user", "assistant"])
        self.assertEqual(loaded.turns[0].text, "明日の予定を教えて")
        self.assertEqual(loaded.turns[1].text, "はい、承知しました")

    def test_unknown_session_id_is_ignored_and_falls_back_to_default_chat_id(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_session", "session_id": "sess-does-not-exist"})
            ws.send_json({"type": "text_input", "text": "テスト"})
            while True:
                msg = ws.receive_json()
                if msg == {"type": "state", "value": "idle"}:
                    break

        # 未登録セッションが黙って無視され、以後も応答は正常に返る(例外で切断されない)
        self.assertIsNone(self.store.load_session("sess-does-not-exist"))

    def test_without_select_session_nothing_is_persisted(self):
        """後方互換: select_sessionを送らない旧クライアントでは、従来どおり永続化されない
        (使い捨てのchat_idはSessionStoreのファイルに対応しないため)。"""
        from fastapi.testclient import TestClient

        app = self._make_app()
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "text_input", "text": "テスト"})
            while True:
                msg = ws.receive_json()
                if msg == {"type": "state", "value": "idle"}:
                    break

        self.assertEqual(self.store.list_sessions(), [])


class TestSummarizeSessionTitle(unittest.TestCase):
    """14日目③: `summarize_session_title()`単体。Ollamaは叩かず、generate_fnを差し替えて検証する。

    背景: append_turn()の暫定タイトル(ユーザー発話の先頭30字そのまま)は
    「この資料からおすすめな曲のタイトルは何かな」のような入力をそのままタイトルに
    してしまい、Claude等の「内容の要約」タイトルと体験が異なる。この関数はその
    ギャップを埋めるためのLLM要約ステップ。
    """

    def test_uses_router_model_and_user_text_as_prompt(self):
        from voice_gateway import TITLE_SUMMARY_MODEL, summarize_session_title

        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return "おすすめの曲"

        title = summarize_session_title(
            "この資料からおすすめな曲のタイトルは何かな", generate_fn=fake_generate
        )

        self.assertEqual(title, "おすすめの曲")
        self.assertEqual(calls[0]["model"], TITLE_SUMMARY_MODEL)
        self.assertEqual(calls[0]["prompt"], "この資料からおすすめな曲のタイトルは何かな")

    def test_strips_surrounding_quotes_and_whitespace(self):
        from voice_gateway import summarize_session_title

        title = summarize_session_title("テスト", generate_fn=lambda **_: "「おすすめの曲」\n")
        self.assertEqual(title, "おすすめの曲")

    def test_truncates_overly_long_output(self):
        from voice_gateway import TITLE_SUMMARY_MAX_CHARS, summarize_session_title

        title = summarize_session_title("テスト", generate_fn=lambda **_: "あ" * 100)
        self.assertEqual(title, "あ" * TITLE_SUMMARY_MAX_CHARS)

    def test_returns_none_on_empty_output(self):
        from voice_gateway import summarize_session_title

        self.assertIsNone(summarize_session_title("テスト", generate_fn=lambda **_: "   "))

    def test_returns_none_when_generate_fn_raises(self):
        # Ollama停止中等。呼び出し側はNoneを見て暫定タイトル(先頭30字)を残す。
        from voice_gateway import summarize_session_title

        def boom(**_):
            raise RuntimeError("ollama unreachable")

        self.assertIsNone(summarize_session_title("テスト", generate_fn=boom))


class TestSessionTitleAutoSummarization(unittest.TestCase):
    """14日目③: 最初のやり取りが確定した後、バックグラウンドでLLM要約タイトルへ
    差し替わること(append_turn()が付ける暫定タイトル=先頭30字を上書きする)。
    """

    def setUp(self):
        import tempfile

        from session_store import SessionStore

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = SessionStore(root=Path(self._tmpdir.name))

    def _wait_for_title(self, session_id, expected, timeout=2.0):
        import time as _time

        deadline = _time.monotonic() + timeout
        last = None
        while _time.monotonic() < deadline:
            last = self.store.load_session(session_id).title
            if last == expected:
                return last
            _time.sleep(0.02)
        return last

    def test_first_exchange_title_is_replaced_by_summary(self):
        from fastapi.testclient import TestClient

        session = self.store.create_session()
        app = create_app(
            pipe_factory=lambda: FakePipe("承知しました"),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error
            ),
            synthesize=lambda text: b"",
            session_store=self.store,
            title_generator=lambda user_text: "おすすめの曲",
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_session", "session_id": session.session_id})
            ws.send_json(
                {"type": "text_input", "text": "この資料からおすすめな曲のタイトルは何かな"}
            )
            while True:
                msg = ws.receive_json()
                if msg == {"type": "state", "value": "idle"}:
                    break

        title = self._wait_for_title(session.session_id, "おすすめの曲")
        self.assertEqual(title, "おすすめの曲")

    def test_second_exchange_does_not_trigger_summarization(self):
        from fastapi.testclient import TestClient

        session = self.store.create_session()
        self.store.append_turn(session.session_id, role="user", text="最初の発話", route="FAST")
        calls = []

        def title_generator(user_text):
            calls.append(user_text)
            return "呼ばれたら失敗"

        app = create_app(
            pipe_factory=lambda: FakePipe("はい"),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error
            ),
            synthesize=lambda text: b"",
            session_store=self.store,
            title_generator=title_generator,
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_session", "session_id": session.session_id})
            ws.send_json({"type": "text_input", "text": "2回目の発話"})
            while True:
                msg = ws.receive_json()
                if msg == {"type": "state", "value": "idle"}:
                    break

        # 少し待っても呼ばれないこと(バックグラウンド処理が無いことの確認)
        import time as _time

        _time.sleep(0.2)
        self.assertEqual(calls, [])
        self.assertEqual(self.store.load_session(session.session_id).title, "最初の発話")

    def test_first_exchange_pushes_title_update_over_websocket(self):
        # 14日目④: サイドバーの次回更新を待たず、要約が終わった直後にWS経由で
        # クライアントへ通知する(応答完了直後にタイトルが変わってほしい、という要望対応)。
        from fastapi.testclient import TestClient

        session = self.store.create_session()
        app = create_app(
            pipe_factory=lambda: FakePipe("承知しました"),
            stt_engine_factory=lambda on_partial, on_final, on_stt_error: FakeSttEngine(
                on_partial, on_final, on_stt_error
            ),
            synthesize=lambda text: b"",
            session_store=self.store,
            title_generator=lambda user_text: "おすすめの曲",
        )
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_session", "session_id": session.session_id})
            ws.send_json(
                {"type": "text_input", "text": "この資料からおすすめな曲のタイトルは何かな"}
            )
            seen_idle = False
            title_update = None
            while title_update is None:
                msg = ws.receive_json()
                if msg == {"type": "state", "value": "idle"}:
                    seen_idle = True
                    continue
                if msg.get("type") == "session_title_updated":
                    title_update = msg

        self.assertTrue(seen_idle)
        self.assertEqual(
            title_update,
            {
                "type": "session_title_updated",
                "session_id": session.session_id,
                "title": "おすすめの曲",
            },
        )


def io_bytes(data: bytes):
    import io

    return io.BytesIO(data)


if __name__ == "__main__":
    unittest.main()
