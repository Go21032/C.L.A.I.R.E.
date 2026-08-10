"""
sentence_splitter.py
----------------------
9日目ノート(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)⑤に対応。

④の`ollama_client.generate_stream()`が返すトークン列と、①で速度を確認した
TTS(`tts_adapter.synthesize()`)を繋ぐ接着剤。**8日目の「読み上げ開始まで約1分」問題を
実際に解消する実体はここにある**。全文の生成完了を待たず、句点が現れた時点で1文を
確定させ、その1文だけを即TTSへ渡す。

    [LLMトークン列] →(句点で切る)→ [1文確定] → [その1文だけ合成] → [再生キューへ]

これにより音声の開始条件が「全文生成 + 全文合成の完了」から
「最初の1文の生成 + その1文の合成」に変わる。

■ 使い方(⑥ voice_gateway.py からの想定)

    from ollama_client import generate_stream
    from sentence_splitter import split_stream

    for sentence in split_stream(generate_stream(model=..., prompt=...)):
        wav = tts_adapter.synthesize(sentence, speaker=107)
        push_to_playback_queue(wav)

  ステートフルに使いたい場合(トークンの供給元が別スレッド等):

    sp = SentenceSplitter()
    for token in tokens:
        for sentence in sp.feed(token):
            ...
    for sentence in sp.flush():   # ← 端数の吐き出しを絶対に忘れないこと
        ...

■ 設計上の判断(9日目⑤の表より)

  - 区切り文字は「。」「!」「?」「!」「?」「改行」。「、」では区切らない
    (細切れだと不自然なため)。
  - 句点が来ないまま`max_chars`を超えたら「、」等で強制分割する。
    ①の実測では85文字までは「合成時間 < 再生時間」が成り立っていた
    (実時間比0.15〜0.20)。86文字以上は未計測のため、既定値は実測範囲から
    大きく離れすぎない120文字にしてある。
  - `min_chars`未満の短文は次の文と結合してから投げる(「はい。」だけを
    TTSに渡すと細切れで不自然になるため)。ただし**ストリーム終端の端数だけは
    長さに関わらず必ず吐き出す**(読み飛ばし事故の防止)。
  - 読み上げ用にテキストを正規化する。特に現在のPipeは`[route: FAST]`という
    デバッグ接頭辞を先頭に付けるため、これを外さないと毎回
    「かっこルートファストかっことじ」と読み上げられる。
  - コードブロック(```で囲まれた部分)は読み上げ対象から除外する。
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

# --- 区切り文字 -----------------------------------------------------------
DEFAULT_TERMINATORS = "。!?!?\n"
# 強制分割時に優先して使う「読点」相当の文字。
DEFAULT_SOFT_SEPARATORS = "、,,;;:"

# --- 長さの既定値 ---------------------------------------------------------
# ①の実測(12〜85文字で実時間比0.15〜0.20)を踏まえた安全側の値。
DEFAULT_MAX_CHARS = 120
DEFAULT_MIN_CHARS = 10

CODE_FENCE = "```"

# Pipeが先頭に付けるデバッグ接頭辞(support_ai_auto_pipe.Valves.show_route_debug_prefix)。
_RE_ROUTE_PREFIX = re.compile(r"^\s*\[route:\s*[A-Za-z]+\]\s*")
_ROUTE_PREFIX_HEAD = "[route:"

# --- 読み上げ用のMarkdown除去 ---------------------------------------------
_RE_HORIZONTAL_RULE = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.MULTILINE)
_RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_RE_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_RE_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_RE_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_RE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_RE_INLINE_CODE = re.compile(r"`([^`]*)`")
_RE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_RE_BOLD_UNDERSCORE = re.compile(r"__([^_]+)__")
# 単独の`_`は`generate_stream`のような識別子を壊すため触らない。`*`のみ扱う。
_RE_ITALIC = re.compile(r"\*([^*\n]+)\*")
# 強調が文をまたぐと(例:「**はじめまして。**私は…」)、1文に切った時点で
# `**`が対になっていない断片が残り、上のペア用の正規表現では落とせない。
# 対になっていない`**`/`__`は読み上げても意味が無いので最後にまとめて落とす。
_RE_STRAY_EMPHASIS = re.compile(r"\*\*|__")
_RE_WHITESPACE = re.compile(r"[ \t\r\n　]+")

# 文を結合する際、直前の文がこれらで終わっていれば読点を補わない。
_PAUSE_CHARS = "。!?!?、,,;;:…‥・)」』】〉"


class SentenceSplitter:
    """トークンを`feed()`し、文が確定したらその文を返すステートフルな部品。

    `feed()`は「今回のトークンで新たに確定した文のリスト」を返す(0件のことも多い)。
    ストリームが終わったら必ず`flush()`を呼び、バッファに残った端数を取り出すこと。
    """

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHARS,
        min_chars: int = DEFAULT_MIN_CHARS,
        terminators: str = DEFAULT_TERMINATORS,
        soft_separators: str = DEFAULT_SOFT_SEPARATORS,
        strip_route_prefix: bool = True,
        skip_code_blocks: bool = True,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars は1以上にしてください")
        if min_chars < 0:
            raise ValueError("min_chars は0以上にしてください")

        self.max_chars = max_chars
        self.min_chars = min_chars
        self.terminators = terminators
        self.soft_separators = soft_separators
        self.skip_code_blocks = skip_code_blocks

        # まだ解釈していない生のトークン片(コードフェンス判定待ちの``等を含む)
        self._raw = ""
        # コードフェンス処理まで済ませた「読み上げ候補テキスト」
        self._text = ""
        # min_chars未満で次の文と結合するために保留している文
        self._carry = ""
        self._in_code_block = False
        self._route_prefix_pending = strip_route_prefix

    # ------------------------------------------------------------------
    # 公開API
    # ------------------------------------------------------------------
    def feed(self, token: str) -> list[str]:
        """トークンを1つ流し込み、今回確定した文のリストを返す。"""
        if not token:
            return []
        self._raw += token
        self._consume_raw(final=False)
        return self._extract(final=False)

    def flush(self) -> list[str]:
        """ストリーム終了時に呼ぶ。バッファの端数を長さに関わらず吐き出す。

        これを呼び忘れると、最後の文が句点で終わらない場合に読み飛ばされる。
        """
        self._consume_raw(final=True)
        out = self._extract(final=True)

        rest = self._normalize(self._text)
        self._text = ""
        tail = self._merge(self._carry, rest).strip()
        self._carry = ""
        if tail:
            out.append(tail)
        return out

    # ------------------------------------------------------------------
    # 段階1: 生トークン → コードブロックを除いた読み上げ候補テキスト
    # ------------------------------------------------------------------
    def _consume_raw(self, final: bool) -> None:
        if not self.skip_code_blocks:
            self._text += self._raw
            self._raw = ""
            return

        buf = self._raw
        while buf:
            idx = buf.find(CODE_FENCE)
            if idx == -1:
                # 末尾の「`」「``」は次のトークンでフェンスになる可能性があるため保留する
                # (ここで読み上げに流すと「バッククォート」が音声に混じる)。
                hold = 0
                if not final:
                    for n in (2, 1):
                        if buf.endswith("`" * n):
                            hold = n
                            break
                safe = buf[: len(buf) - hold] if hold else buf
                if not self._in_code_block:
                    self._text += safe
                buf = buf[len(safe) :]
                break

            if not self._in_code_block:
                self._text += buf[:idx]
            buf = buf[idx + len(CODE_FENCE) :]
            self._in_code_block = not self._in_code_block

        self._raw = buf

    # ------------------------------------------------------------------
    # 段階2: 読み上げ候補テキスト → 確定した文
    # ------------------------------------------------------------------
    def _extract(self, final: bool) -> list[str]:
        if self._route_prefix_pending and not self._strip_route_prefix(final):
            return []

        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            chunk = self._text[:cut]
            self._text = self._text[cut:]

            normalized = self._normalize(chunk)
            if not normalized:
                continue

            candidate = self._merge(self._carry, normalized)
            if len(candidate) < self.min_chars:
                # 短すぎる文はTTSに投げると細切れで不自然なので次の文と結合する
                self._carry = candidate
                continue
            self._carry = ""
            out.append(candidate)
        return out

    def _strip_route_prefix(self, final: bool) -> bool:
        """先頭の`[route: FAST]`を取り除く。判断が付かない間はFalseを返して待つ。"""
        match = _RE_ROUTE_PREFIX.match(self._text)
        if match:
            self._text = self._text[match.end() :]
            self._route_prefix_pending = False
            return True

        head = self._text.lstrip()
        if not final and self._could_still_become_route_prefix(head):
            return False

        self._route_prefix_pending = False
        return True

    @staticmethod
    def _could_still_become_route_prefix(head: str) -> bool:
        if not head:
            return True
        if len(head) < len(_ROUTE_PREFIX_HEAD):
            return _ROUTE_PREFIX_HEAD.startswith(head)
        # "[route:"までは一致しているが閉じ括弧がまだ届いていない状態
        return head.startswith(_ROUTE_PREFIX_HEAD) and "]" not in head

    def _find_cut(self) -> int | None:
        """`self._text`の先頭から何文字目までを1文として切り出すかを返す。"""
        text = self._text
        if not text:
            return None

        positions = [text.find(t) for t in self.terminators]
        hits = [p for p in positions if p != -1]
        if hits:
            return min(hits) + 1

        if len(text) < self.max_chars:
            return None

        # 句点が来ないまま長くなった場合の強制分割。
        # 読点(、)があればそこで切る方が音声として自然なので優先する。
        window = text[: self.max_chars]
        for i in range(len(window) - 1, self.min_chars - 1, -1):
            if window[i] in self.soft_separators:
                return i + 1
        return self.max_chars

    # ------------------------------------------------------------------
    # 読み上げ用テキスト正規化
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""
        text = _RE_HORIZONTAL_RULE.sub("", text)
        text = _RE_HEADING.sub("", text)
        text = _RE_BLOCKQUOTE.sub("", text)
        text = _RE_BULLET.sub("", text)
        text = _RE_IMAGE.sub(r"\1", text)
        text = _RE_LINK.sub(r"\1", text)
        text = _RE_INLINE_CODE.sub(r"\1", text)
        text = _RE_BOLD.sub(r"\1", text)
        text = _RE_BOLD_UNDERSCORE.sub(r"\1", text)
        text = _RE_ITALIC.sub(r"\1", text)
        text = _RE_STRAY_EMPHASIS.sub("", text)
        text = _RE_WHITESPACE.sub(" ", text)
        return text.strip()

    @staticmethod
    def _merge(carry: str, following: str) -> str:
        """min_chars未満で保留していた文に次の文を繋ぐ。

        箇条書きのように句読点を持たない短文同士をそのまま連結すると
        「予定の管理ノートの検索」と一息で読まれてしまうため、読点を補って
        TTSに間を作らせる。
        """
        if not carry:
            return following
        if carry[-1] in _PAUSE_CHARS:
            return carry + following
        return carry + "、" + following


def split_stream(tokens: Iterable[str], **kwargs) -> Iterator[str]:
    """トークン列を受け取り、確定した文を確定した順にyieldする。

    遅延評価であることが重要:最初の1文が確定した時点でyieldされるため、
    呼び出し側は残りのトークンを待たずにTTS合成へ回せる(=最初の音声が早く鳴る)。
    キーワード引数はそのまま`SentenceSplitter`へ渡す。
    """
    splitter = SentenceSplitter(**kwargs)
    for token in tokens:
        for sentence in splitter.feed(token):
            yield sentence
    for sentence in splitter.flush():
        yield sentence
