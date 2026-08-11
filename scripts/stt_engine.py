"""
stt_engine.py
--------------
9日目ノート(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)
②③で確定したSTT構成を1つの部品にまとめ、⑥の`voice_gateway.py`から
使いやすい形にする:

  - 暫定表示: Vosk small-ja(100msチャンク。③実機比較結果⑥)
  - 確定表示: faster-whisper small + `initial_prompt`(③実機比較結果④)
  - 発話区切り: silero-vad/webrtcvadではなく、Voskの`AcceptWaveform()`の
    戻り値をそのまま流用する(`vad.VoskEndpointVAD`。③残課題の検証結果)
  - 「東北ずん子」の辞書補正: `initial_prompt`だけでは補正しきれなかった
    (③実機比較結果④)ため、確定テキストに対する後処理として実施する

`STTEngine`はVosk/faster-whisperの実体を直接importしない。コンストラクタで
`recognizer`(Vosk互換: AcceptWaveform/PartialResult/Result)と
`transcribe_final`(PCMバイト列→確定テキストの呼び出し可能オブジェクト)を
注入する設計にすることで、ユニットテストではフェイクに差し替えて
モデルのダウンロード・ロードなしにロジックを検証できるようにしてある
(`tests/test_stt_engine.py`)。実運用では`create_default_engine()`が
実物のVosk/faster-whisperを組み立てる。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Protocol

from vad import VadEventType, VoskEndpointVAD

TARGET_SAMPLE_RATE = 16000

# ③実機比較結果⑥で実測した、faster-whisper smallが`initial_prompt`ありでも
# 補正できなかった「東北ずん子」の崩れパターン → 正解への置換表。
# (Voskは暫定表示側でむしろ「東北ずん子」を一発で正しく出せている。
#  補正が必要なのは確定側=Whisperの出力であることに注意)
ZUNKO_CORRECTIONS: dict[str, str] = {
    "東北銃口": "東北ずん子",
    "東北図んこ": "東北ずん子",
    "東北順庫": "東北ずん子",
    "東北順子": "東北ずん子",
    "東北ズンコ": "東北ずん子",
    "東北ずんこ": "東北ずん子",
}

# ③実機比較結果④で固有名詞の認識を劇的に改善したヒント文をそのまま既定値にする。
DEFAULT_INITIAL_PROMPT = (
    "C.L.A.I.R.E.、LanceDB、Ruri、Tailscale、Obsidian、VOICEVOX、東北ずん子、"
    "vault、ベクトルデータベース、埋め込みモデル"
)


def correct_zunko(text: str) -> str:
    """faster-whisperの確定結果に対し、実測済みの「東北ずん子」誤変換パターンを置換する。

    `initial_prompt`だけでは直らないことが9日目③実機比較結果④で確定したための後処理。
    """
    corrected = text
    for wrong, right in ZUNKO_CORRECTIONS.items():
        corrected = corrected.replace(wrong, right)
    return corrected


class Recognizer(Protocol):
    """VoskのKaldiRecognizerが満たすべき最小プロトコル(テストではフェイクで差し替える)。"""

    def AcceptWaveform(self, data: bytes) -> bool: ...
    def PartialResult(self) -> str: ...
    def Result(self) -> str: ...
    def FinalResult(self) -> str: ...


@dataclass
class STTEngine:
    """音声チャンクを受け取り、暫定テキスト(Vosk)と確定テキスト(Whisper)を
    コールバックで返すステートフルな部品。

    引数:
        recognizer: Vosk互換のKaldiRecognizer(またはそのフェイク)。
        transcribe_final: 発話終了時に呼ぶ、確定転写の呼び出し可能オブジェクト。
            `(pcm_bytes: bytes) -> str` の形(faster-whisperの`model.transcribe()`を
            ラップしたものを渡す想定。initial_promptの付与は呼び出し元の責務)。
        vad: 発話区切りの検出器。既定は`vad.VoskEndpointVAD()`。
        correct_final: 確定テキストへ適用する後処理。既定は`correct_zunko`。
        on_partial: 暫定テキストが変化するたびに呼ぶコールバック。
        on_final: 確定テキストが出るたびに呼ぶコールバック(辞書補正済み)。
    """

    recognizer: Recognizer
    transcribe_final: Callable[[bytes], str]
    vad: VoskEndpointVAD = field(default_factory=VoskEndpointVAD)
    correct_final: Callable[[str], str] = correct_zunko
    on_partial: Callable[[str], None] | None = None
    on_final: Callable[[str], None] | None = None

    _pcm_buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _last_partial: str = field(default="", init=False, repr=False)

    def feed_audio(self, chunk: bytes) -> None:
        """16kHz/mono/16bit PCMチャンクを1つ流し込む。

        呼ぶたびに:
          1. Voskへ流し、暫定テキストが変化していれば`on_partial()`を呼ぶ
          2. `vad.VoskEndpointVAD`へ結果を渡し、発話終了イベントが出れば確定処理へ回す
        """
        self._pcm_buffer.extend(chunk)
        accepted = self.recognizer.AcceptWaveform(chunk)

        if accepted:
            payload = json.loads(self.recognizer.Result())
            final_text = payload.get("text", "")
            partial_text = ""
        else:
            payload = json.loads(self.recognizer.PartialResult())
            partial_text = payload.get("partial", "")
            final_text = ""

        if not accepted and partial_text and partial_text != self._last_partial:
            self._last_partial = partial_text
            if self.on_partial:
                self.on_partial(partial_text)

        events = self.vad.observe(
            accepted=accepted, partial_text=partial_text, final_text=final_text
        )
        for event in events:
            if event.type is VadEventType.SPEECH_END:
                self._finalize()

    def flush(self) -> None:
        """ストリーム終了時(WebSocket切断など)に呼ぶ。

        バッファに音声が残っていれば、VADが発話終了を検知していなくても
        強制的に確定転写へ回す(sentence_splitter.flush()と同じ「端数を捨てない」方針)。
        """
        if self._pcm_buffer:
            self._finalize()

    def _finalize(self) -> None:
        pcm = bytes(self._pcm_buffer)
        self._pcm_buffer.clear()
        self._last_partial = ""
        self.vad.reset()
        if not pcm:
            return
        text = self.transcribe_final(pcm)
        text = self.correct_final(text)
        if self.on_final:
            self.on_final(text)


def create_default_engine(
    *,
    vosk_model_dir: str,
    whisper_model: str = "small",
    device: str = "cuda",
    compute_type: str = "int8_float16",
    initial_prompt: str | None = DEFAULT_INITIAL_PROMPT,
    on_partial: Callable[[str], None] | None = None,
    on_final: Callable[[str], None] | None = None,
) -> STTEngine:
    """実物のVosk + faster-whisperでSTTEngineを組み立てる(⑥の実運用向け)。

    ユニットテストの対象外(実モデルのロードを伴うため)。9日目③の実機検証で
    確認済みの構成(vosk-model-small-ja-0.22 / faster-whisper small /
    device=cuda, compute_type=int8_float16)を既定値にしてある。

    Voskモデルは非ASCIIパスに置くとロードに失敗する(③実機比較結果⑥で確認済み)ため、
    `vosk_model_dir`にはvault外のASCIIパス(例: C:\\Users\\<user>\\vosk_models\\...)を渡すこと。
    """
    from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore
    from faster_whisper import WhisperModel  # type: ignore

    SetLogLevel(-1)
    vosk_model = Model(vosk_model_dir)
    recognizer = KaldiRecognizer(vosk_model, TARGET_SAMPLE_RATE)

    whisper = WhisperModel(whisper_model, device=device, compute_type=compute_type)

    def transcribe_final(pcm: bytes) -> str:
        import array

        samples = array.array("h")
        samples.frombytes(pcm)
        waveform = [s / 32768.0 for s in samples]
        segments, _info = whisper.transcribe(
            waveform, language="ja", initial_prompt=initial_prompt
        )
        return "".join(seg.text for seg in segments).strip()

    return STTEngine(
        recognizer=recognizer,
        transcribe_final=transcribe_final,
        on_partial=on_partial,
        on_final=on_final,
    )
