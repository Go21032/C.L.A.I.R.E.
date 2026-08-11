"""
vad.py
--------
9日目ノート(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)
③残課題・⑥の部品。「VoskのResult()タイミングを発話区切りに流用できるか」の実装。

silero-vad/webrtcvadは音声波形そのものを解析して無音/発話を判定するライブラリだが、
③実機比較結果⑥で、Voskは`AcceptWaveform()`が`True`を返すタイミング(=Kaldi内部の
エンドポインタが「ここで区切ってよい」と判断したタイミング)を既に持っていることが
分かっている。これをそのまま発話区切りに流用できれば、silero-vad/webrtcvadの
導入(追加の依存・追加のCPU負荷・別途の閾値チューニング)が丸ごと不要になる。

`VoskEndpointVAD`はその検証結果を実装したもの。**音声そのものは一切扱わない**。
stt_engine.py がVoskの`KaldiRecognizer`を実際に呼んだ結果
(`accepted: bool` / 暫定テキスト / 確定テキスト)をこのクラスへ渡し、
「発話開始 (SPEECH_START)」「発話終了 (SPEECH_END)」のイベントに変換するだけの
薄い層にすることで、音声処理を一切importせずに単体テストできるようにしてある。

silero-vad/webrtcvadへの切り替えが必要になった場合は、同じ`observe()`インターフェース
(accepted/partial_text/final_textの代わりに、無音判定の結果を渡す形)を持つ別実装を
用意すれば`stt_engine.py`側は変更不要 — という差し替え可能性も設計上残してある。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VadState(Enum):
    IDLE = "idle"
    SPEAKING = "speaking"


class VadEventType(Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@dataclass(frozen=True)
class VadEvent:
    type: VadEventType
    # SPEECH_ENDの場合のみ、Voskがそこまでに確定させたテキストを載せる。
    # あくまで暫定表示用の参考値であり、確定はstt_engine.pyがWhisperで取り直す。
    text: str = ""


class VoskEndpointVAD:
    """VoskのAcceptWaveform()の戻り値を発話区切りとして解釈するステートマシン。

    引数:
        min_utterance_chars: SPEECH_ENDとして扱う最小文字数。これ未満の確定テキスト
            (無音チャンクがたまたま区切られただけ、等)は発話とみなさず握りつぶす。
    """

    def __init__(self, min_utterance_chars: int = 1):
        self._state = VadState.IDLE
        self._min_utterance_chars = min_utterance_chars

    @property
    def state(self) -> VadState:
        return self._state

    def observe(
        self, *, accepted: bool, partial_text: str = "", final_text: str = ""
    ) -> list[VadEvent]:
        """stt_engine.pyが1チャンク分のVosk呼び出し結果を渡すたびに呼ぶ。

        - accepted=False: まだエンドポイントに達していない(発話継続中の暫定結果)。
          IDLE中に非空のpartial_textが来たら「発話開始」とみなす。
        - accepted=True: Voskの内部エンドポインタが区切りを検出した。
          SPEAKING中であれば「発話終了」。IDLE中(=発話が始まる前からの無音区切り)は無視する。
        """
        events: list[VadEvent] = []

        if not accepted:
            if self._state is VadState.IDLE and partial_text.strip():
                self._state = VadState.SPEAKING
                events.append(VadEvent(VadEventType.SPEECH_START))
            return events

        if self._state is VadState.SPEAKING:
            text = final_text.strip()
            if len(text) >= self._min_utterance_chars:
                events.append(VadEvent(VadEventType.SPEECH_END, text=text))
            self._state = VadState.IDLE

        return events

    def reset(self) -> None:
        """ストリーム打ち切り(WebSocket切断など)後、次の発話に備えて状態を初期化する。"""
        self._state = VadState.IDLE
