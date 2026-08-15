"""
tests/test_sentence_splitter.py
---------------------------------
9日目ノート⑤:`sentence_splitter.SentenceSplitter` のユニットテスト。

④の`generate_stream()`が返すトークン列を「1文」に切り出してTTSへ渡す接着剤であり、
**8日目の「読み上げ開始まで約1分」問題を実際に解消する部品**。そのため次の4点を検証する:

  1. トークンが文の途中で切れて届いても、正しく1文にまとまること
  2. 句点が来ないまま長文が続いても強制分割されること(最初の音声が遅れないように)
  3. ストリーム終了時にバッファの端数が必ず吐き出されること(最後の文の読み飛ばし防止)
  4. 読み上げ用のテキスト正規化(Markdown除去・コードブロック除外・
     `[route: FAST]`デバッグ接頭辞の除去)が効くこと
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from sentence_splitter import SentenceSplitter, split_stream  # noqa: E402


def make_splitter(**kwargs) -> SentenceSplitter:
    """正規化・分割の検証に集中するため、既定では最小文字数の結合を無効にする。"""
    kwargs.setdefault("min_chars", 0)
    return SentenceSplitter(**kwargs)


def feed_all(splitter: SentenceSplitter, tokens: list[str]) -> list[str]:
    out: list[str] = []
    for token in tokens:
        out.extend(splitter.feed(token))
    return out


class TestSentenceBoundaries(unittest.TestCase):
    def test_tokens_split_mid_sentence_are_joined_into_one_sentence(self):
        sp = make_splitter()
        out = feed_all(sp, ["こん", "にちは", "、元気", "ですか?"])

        self.assertEqual(out, ["こんにちは、元気ですか?"])

    def test_nothing_is_emitted_before_a_terminator_arrives(self):
        sp = make_splitter()
        out = feed_all(sp, ["こん", "にちは"])

        self.assertEqual(out, [])

    def test_multiple_sentences_in_one_token_are_all_emitted(self):
        sp = make_splitter()
        out = sp.feed("おはようございます。今日は晴れです。よい一日を。")

        self.assertEqual(out, ["おはようございます。", "今日は晴れです。", "よい一日を。"])

    def test_exclamation_and_question_marks_terminate_sentences(self):
        sp = make_splitter()
        out = sp.feed("すごい!本当ですか?はい!そうです?")

        self.assertEqual(out, ["すごい!", "本当ですか?", "はい!", "そうです?"])

    def test_newline_terminates_a_sentence_and_is_not_read_aloud(self):
        sp = make_splitter()
        out = sp.feed("1つ目の項目\n2つ目の項目\n")

        self.assertEqual(out, ["1つ目の項目", "2つ目の項目"])

    def test_comma_alone_does_not_terminate_a_sentence(self):
        """「、」で切ると細切れになって不自然なため、区切り文字に含めない(⑤の設計判断)。"""
        sp = make_splitter()
        out = sp.feed("まず準備をして、次に実行します。")

        self.assertEqual(out, ["まず準備をして、次に実行します。"])


class TestFlush(unittest.TestCase):
    def test_flush_emits_trailing_fragment_without_terminator(self):
        sp = make_splitter()
        self.assertEqual(sp.feed("句点で終わらない最後の文"), [])

        self.assertEqual(sp.flush(), ["句点で終わらない最後の文"])

    def test_flush_emits_fragment_shorter_than_min_chars(self):
        """最小文字数を下回っていても、端数は必ず吐き出す(読み飛ばし事故の防止)。"""
        sp = SentenceSplitter(min_chars=10)
        self.assertEqual(sp.feed("短い"), [])

        self.assertEqual(sp.flush(), ["短い"])

    def test_flush_returns_empty_list_when_buffer_is_empty(self):
        sp = make_splitter()
        sp.feed("完結した文です。")

        self.assertEqual(sp.flush(), [])

    def test_flush_is_idempotent(self):
        sp = make_splitter()
        sp.feed("端数")
        sp.flush()

        self.assertEqual(sp.flush(), [])


class TestForceSplitOfLongSentences(unittest.TestCase):
    def test_long_sentence_is_split_at_soft_separator_before_max_chars(self):
        sp = make_splitter(max_chars=20)
        text = "あいうえおかきくけこさし、すせそたちつてとなにぬねのはひふへほ"

        out = sp.feed(text)

        self.assertEqual(out, ["あいうえおかきくけこさし、"])
        self.assertEqual(sp.flush(), ["すせそたちつてとなにぬねのはひふへほ"])

    def test_long_sentence_without_soft_separator_is_split_at_max_chars(self):
        sp = make_splitter(max_chars=10)

        out = sp.feed("あ" * 25)

        self.assertEqual(out, ["あ" * 10, "あ" * 10])
        self.assertEqual(sp.flush(), ["あ" * 5])

    def test_soft_separator_before_min_chars_is_not_used_as_split_point(self):
        """先頭すぐの「、」で切ると「あい、」のような細切れになるため使わない。"""
        sp = SentenceSplitter(max_chars=20, min_chars=10)

        out = sp.feed("あい、" + "う" * 30)

        self.assertEqual(out, ["あい、" + "う" * 17])

    def test_sentence_shorter_than_max_chars_is_not_force_split(self):
        sp = make_splitter(max_chars=120)

        out = sp.feed("あ" * 100 + "。")

        self.assertEqual(out, ["あ" * 100 + "。"])


class TestMinCharsMerging(unittest.TestCase):
    def test_short_sentence_is_merged_with_the_next_one(self):
        sp = SentenceSplitter(min_chars=10)

        out = sp.feed("はい。承知しました。")

        self.assertEqual(out, ["はい。承知しました。"])

    def test_merging_stops_once_min_chars_is_reached(self):
        sp = SentenceSplitter(min_chars=10)

        # 「はい。」(3文字)+「承知しました。」(7文字)=10文字でmin_charsに到達し1文目が確定。
        # 続く「それでは次に進みましょう。」(13文字)は単独でmin_charsを満たすので別の文になる。
        out = sp.feed("はい。承知しました。それでは次に進みましょう。")

        self.assertEqual(out, ["はい。承知しました。", "それでは次に進みましょう。"])
        self.assertEqual(sp.flush(), [])

    def test_merged_short_sentences_get_a_pause_separator(self):
        """箇条書きのような句読点のない短文同士を結合すると、区切りが無くなって
        「予定の管理ノートの検索」のように一息で読まれてしまう。読点を補うこと。"""
        sp = SentenceSplitter(min_chars=10)

        out = sp.feed("予定の管理\nノートの検索\n")

        self.assertEqual(out, ["予定の管理、ノートの検索"])

    def test_merging_does_not_duplicate_existing_punctuation(self):
        sp = SentenceSplitter(min_chars=10)

        out = sp.feed("はい。承知しました。")

        self.assertEqual(out, ["はい。承知しました。"])

    def test_short_sentence_at_the_end_is_kept_until_flush(self):
        sp = SentenceSplitter(min_chars=10)

        out = sp.feed("承知しました。はい。")

        self.assertEqual(out, ["承知しました。はい。"])


class TestTextNormalizationForSpeech(unittest.TestCase):
    def test_route_debug_prefix_is_removed(self):
        sp = make_splitter()

        out = sp.feed("[route: FAST]\n今日は晴れです。")

        self.assertEqual(out, ["今日は晴れです。"])

    def test_route_debug_prefix_split_across_tokens_is_removed(self):
        sp = make_splitter()

        out = feed_all(sp, ["[rou", "te: ", "DEEP]", "\n計画を立てます。"])

        self.assertEqual(out, ["計画を立てます。"])

    def test_bracketed_text_that_is_not_a_route_prefix_is_kept(self):
        sp = make_splitter()

        out = sp.feed("[重要]これは残すべき文です。")

        self.assertEqual(out, ["[重要]これは残すべき文です。"])

    def test_bold_markers_are_removed(self):
        sp = make_splitter()

        out = sp.feed("**重要**な点です。")

        self.assertEqual(out, ["重要な点です。"])

    def test_bold_marker_spanning_a_sentence_boundary_is_removed(self):
        """強調が文をまたぐと、文に切った時点で`**`が対になっていない断片が残る。
        そのままTTSへ渡すと「こめこめ」等と読み上げられるため必ず落とすこと。"""
        sp = make_splitter()

        out = sp.feed("**はじめまして。**私はC.L.A.I.R.E.です。")

        self.assertEqual(out, ["はじめまして。", "私はC.L.A.I.R.E.です。"])

    def test_heading_markers_are_removed(self):
        sp = make_splitter()

        out = sp.feed("## 見出しです\n本文です。")

        self.assertEqual(out, ["見出しです", "本文です。"])

    def test_link_keeps_label_and_drops_url(self):
        sp = make_splitter()

        out = sp.feed("詳しくは[Obsidian](https://obsidian.md)を見てください。")

        self.assertEqual(out, ["詳しくはObsidianを見てください。"])

    def test_inline_code_backticks_are_removed(self):
        """19日目 修正: 括弧内除去を導入したことで、コード片内の`()`もまとめて
        除去されるようになった(既知の制約。docstring参照)。バッククォート自体が
        除去されることの確認に主眼を置き、中身は括弧を含まない識別子にする。"""
        sp = make_splitter()

        out = sp.feed("`hello`を使います。")

        self.assertEqual(out, ["helloを使います。"])

    def test_parenthesized_text_is_not_read_aloud(self):
        """19日目 修正: 「スパイダーマン：ブランド・ニュー・デイ(Spider-Man: Brand New
        Day)」のように、原語表記や補足を括弧で添えている場合、そのまま読み上げると
        同じ内容を2回言うことになりくどいため、括弧内の文字は読み上げから除く。"""
        sp = make_splitter()

        out = sp.feed(
            "スパイダーマン:ブランド・ニュー・デイ(Spider-Man: Brand New Day)が公開されました。"
        )

        self.assertEqual(out, ["スパイダーマン:ブランド・ニュー・デイが公開されました。"])

    def test_fullwidth_parenthesized_text_is_not_read_aloud(self):
        sp = make_splitter()

        out = sp.feed("会場は東京ドーム(とうきょうどーむ)です。")

        self.assertEqual(out, ["会場は東京ドームです。"])

        out2 = sp.feed("会場は東京ドーム(とうきょうどーむ)です。".replace("(", "（").replace(")", "）"))

        self.assertEqual(out2, ["会場は東京ドームです。"])

    def test_link_url_in_parens_is_still_dropped_correctly(self):
        """リンク記法の`(url)`は、括弧除去より先にlink処理で消えるため二重に影響しない。"""
        sp = make_splitter()

        out = sp.feed("詳しくは[Obsidian](https://obsidian.md)を見てください。")

        self.assertEqual(out, ["詳しくはObsidianを見てください。"])

    def test_list_bullets_are_removed(self):
        sp = make_splitter()

        out = sp.feed("- 1つ目です\n- 2つ目です\n")

        self.assertEqual(out, ["1つ目です", "2つ目です"])

    def test_blockquote_marker_is_removed(self):
        sp = make_splitter()

        out = sp.feed("> 引用された文です。")

        self.assertEqual(out, ["引用された文です。"])

    def test_underscores_inside_identifiers_are_preserved(self):
        """`generate_stream`のような識別子まで壊さないこと(過剰な正規化の防止)。"""
        sp = make_splitter()

        out = sp.feed("generate_streamを使います。")

        self.assertEqual(out, ["generate_streamを使います。"])

    def test_symbol_only_chunk_produces_no_sentence(self):
        sp = make_splitter()

        out = sp.feed("---\n")

        self.assertEqual(out, [])


class TestCodeBlockExclusion(unittest.TestCase):
    def test_fenced_code_block_is_not_read_aloud(self):
        sp = make_splitter()

        out = sp.feed("説明します。\n```python\nprint(1)\n```\n以上です。")

        self.assertEqual(out, ["説明します。", "以上です。"])

    def test_code_fence_split_across_tokens_is_still_excluded(self):
        sp = make_splitter()

        out = feed_all(sp, ["手順です。\n``", "`python\nx = 1\n``", "`\n完了しました。"])

        self.assertEqual(out, ["手順です。", "完了しました。"])
        self.assertNotIn("`", "".join(out))

    def test_unclosed_code_block_is_dropped_at_flush(self):
        sp = make_splitter()

        out = sp.feed("途中で切れました。\n```python\nprint(")

        self.assertEqual(out, ["途中で切れました。"])
        self.assertEqual(sp.flush(), [])


class TestSplitStreamHelper(unittest.TestCase):
    def test_split_stream_yields_sentences_including_the_trailing_fragment(self):
        tokens = ["はじめ", "まして。", "私はC.L.A.", "I.R.E.です。", "端数の文"]

        sentences = list(split_stream(tokens, min_chars=0))

        self.assertEqual(
            sentences, ["はじめまして。", "私はC.L.A.I.R.E.です。", "端数の文"]
        )

    def test_split_stream_is_lazy_so_the_first_sentence_arrives_early(self):
        """全トークンを読み切る前に最初の1文が取れること(=最初の音声を早く鳴らせる)。"""
        consumed: list[str] = []

        def token_source():
            for token in ["最初の文です。", "2番目の文です。", "3番目の文です。"]:
                consumed.append(token)
                yield token

        stream = split_stream(token_source(), min_chars=0)
        first = next(stream)

        self.assertEqual(first, "最初の文です。")
        self.assertEqual(consumed, ["最初の文です。"])


if __name__ == "__main__":
    unittest.main()
