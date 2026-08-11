"""
tests/fakes.py
----------------
6日目ノート(サポートAI作製計画/6日目RAG記憶レイヤーのPipe組み込み.md)で
support_ai_auto_pipe.pyにmemory_store(LanceDB連携)を組み込んだ際、
既存のpytestスイートがmemory_storeを一切モックしていなかったため、
テスト実行のたびに本番の記憶DB(D:\\sapo_ai\\rag_memory\\db)へ
実際のOllama Embeddingでテストデータが書き込まれてしまう事故が起きた。

この事故を防ぐため、`support_ai_auto_pipe.memory_store` を差し替える
フェイク実装をここに集約する。実際のOllama呼び出し・LanceDB接続は一切行わない。
"""

from __future__ import annotations


class NoopMemoryStore:
    """記憶レイヤーが「何もヒットしない・何も書き込まない」だけの最小フェイク。
    「Pipe自体のルーティングロジック」だけを検証したいテストではこれを使う。"""

    def retrieve(self, query, limit=3, route=None):
        return []

    def format_context(self, hits, max_distance=0.45):
        return ""

    def append_turn(self, chat_id, role, route, text, topic=""):
        return 0


class RecordingMemoryStore(NoopMemoryStore):
    """呼び出しを記録するフェイク。「retrieveがどのrouteで呼ばれたか」
    「append_turnが何回・どんな内容で呼ばれたか」を検証するテストで使う。"""

    def __init__(self, hits=None):
        self.retrieve_calls: list[dict] = []
        self.append_calls: list[dict] = []
        self._hits = hits or []

    def retrieve(self, query, limit=3, route=None):
        self.retrieve_calls.append({"query": query, "limit": limit, "route": route})
        return self._hits

    def format_context(self, hits, max_distance=0.45):
        if not hits:
            return ""
        lines = [f"- ({h['date']}) {h['content']}" for h in hits]
        return "以下は過去の会話からの参考情報です。関連する場合のみ利用してください。\n" + "\n".join(
            lines
        )

    def append_turn(self, chat_id, role, route, text, topic=""):
        self.append_calls.append(
            {"chat_id": chat_id, "role": role, "route": route, "text": text}
        )
        return 1


class FailingMemoryStore(NoopMemoryStore):
    """記憶レイヤーの障害(DB接続断・Ollama未起動など)をシミュレートするフェイク。
    retrieve/append_turnが例外を送出しても、Pipe本体の応答が止まらないこと
    (6日目④の完了条件)を確認するテストで使う。"""

    def retrieve(self, query, limit=3, route=None):
        raise RuntimeError("simulated memory DB connection failure")

    def append_turn(self, chat_id, role, route, text, topic=""):
        raise RuntimeError("simulated memory DB write failure")


class ScriptedVoskRecognizer:
    """9日目⑥ stt_engine.STTEngine のテスト用フェイク。

    実際のVosk(`vosk.KaldiRecognizer`)をインストール・モデルロードせずに、
    `feed_audio()`を呼ぶたびに「あらかじめ台本(script)で決めた戻り値」を
    順番に返すだけの最小実装。台本の1要素は
    (accepted: bool, partial_text: str, final_text: str) のタプルで、
    STTEngine.feed_audio()が呼ぶ AcceptWaveform/PartialResult/Result と
    同じ形になるよう、内部でVosk互換のJSON文字列に変換して返す。
    """

    def __init__(self, script):
        import json

        self._json = json
        self._script = list(script)
        self._step = 0
        self._last_partial_json = "{}"
        self._last_result_json = "{}"
        self.accept_waveform_calls: list[bytes] = []

    def AcceptWaveform(self, data: bytes) -> bool:
        self.accept_waveform_calls.append(data)
        accepted, partial_text, final_text = self._script[self._step]
        self._step += 1
        if accepted:
            self._last_result_json = self._json.dumps({"text": final_text})
        else:
            self._last_partial_json = self._json.dumps({"partial": partial_text})
        return accepted

    def PartialResult(self) -> str:
        return self._last_partial_json

    def Result(self) -> str:
        return self._last_result_json

    def FinalResult(self) -> str:
        return self._last_result_json
