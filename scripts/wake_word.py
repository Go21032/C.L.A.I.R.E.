"""wake_word.py — 13日目②:ウェイクワード「クレア/ねえクレア」の検出。

10日目②で作り⑦で削除したものの復活だが、役割が違う点に注意:
当時は「検出しなければ発話をAIへ送らず、書き起こしも捨てる」ゲートだったため
"消えて分からない"体験を生んだ。今回は書き起こしの常時プレビューは常に行い、
**自動送信してよいかどうか**の判断にだけ使う(voice_gateway.py/index.html側)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# マッチングは「ひらがな→カタカナに寄せた文字列」に対して行うため、パターンは
# カタカナ表記で書く(ひらがな表記はマッチング前に自動でカタカナへ変換されるため不要)。
# Voskの暫定結果で実際に出た誤認識パターンは実機運用しながらここへ追加していく
# (stt_engine.pyのZUNKO_CORRECTIONSと同じ育て方)。
#
# 16日目 修正1: 前置き語に「イヤー(いやー)」「ヤー(やー)」を追加し、語尾に
# 「キドウ(きどう=起動)」を追加した。あわせて前置き語⇔本体⇔語尾の間に読点/カンマ/
# 空白が挟まっても検出できるよう`[、,\s]*`を挟む(「へい、クレア」「クレア、きどう」に
# 対応するため。以前は直接連結のみ想定していた)。
#
# 18日目 修正: UIに表示/入力される「C.L.A.I.R.E.」のように1文字ずつピリオドで区切った
# スペルアウト表記が`claire`と連続一致せず検出できないため、入力欄・送信テキストに
# ウェイクワードがそのまま混入するバグがあった(`_normalize_for_match`は文字数を変えない
# 変換に限定しており、ピリオド除去はしていない)。各文字の直後に任意で`.`が挟まってよい
# ことを正規表現側で許容して対応する。
WAKE_PATTERNS = [
    r"(ネエ|ネェ|ネ|ヘイ|イヤー|ヤー|hey)?[、,\s]*"
    r"(クレア|クレヤ|クレアー|暮レ亜|暮レア|c\.?l\.?a\.?i\.?r\.?e\.?|c\.?l\.?a\.?i\.?r\.?)"
    r"[、,\s]*(サン|チャン|キドウ)?",
]
_COMPILED = [re.compile(p) for p in WAKE_PATTERNS]

# text_after切り出し時に先頭から取り除く区切り文字(句読点・空白の類)
_LEADING_SEPARATORS = " 　、。,.!?！?・「」"


@dataclass(frozen=True)
class WakeDetection:
    matched: str  # 実際にマッチした部分(正規化後)
    text_after: str  # ウェイクワードより後ろの本文(元テキストから切り出す)


def _normalize_for_match(text: str) -> str:
    """マッチング用に正規化する。NFKC/小文字化/ひらがな→カタカナはいずれも
    1文字1文字を置き換えるだけの変換(文字数が変わらない)なので、変換後の
    インデックスは元テキストのインデックスとそのまま対応させられる。
    """
    t = unicodedata.normalize("NFKC", text).lower()
    t = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in t)
    return t


def normalize(text: str) -> str:
    """全角/半角・大小文字・ひらがな/カタカナの表記ゆれを吸収した文字列を返す。

    (後方互換のため単体でも公開している。マッチング自体は`detect_wake_word`内で行う)
    """
    return _normalize_for_match(text)


def detect_wake_word(text: str) -> WakeDetection | None:
    """テキストの先頭付近にウェイクワードが含まれるか判定する。

    見つかればマッチ部分(正規化後)と、その後に続く本文を`WakeDetection`で返す。
    `text_after`は元テキストからマッチ末尾以降を切り出したもの(先頭の区切り文字は除く)。
    見つからなければ`None`を返す。
    """
    norm = _normalize_for_match(text)
    for pattern in _COMPILED:
        m = pattern.search(norm)
        if m and m.group(0):
            after = text[m.end():].lstrip(_LEADING_SEPARATORS)
            return WakeDetection(matched=m.group(0), text_after=after)
    return None
