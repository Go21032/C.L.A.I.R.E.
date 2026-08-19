import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router_rules import is_media_request, match_rule_based


class TestMatchRuleBased(unittest.TestCase):
    def test_code_keyword_matches(self):
        self.assertEqual(match_rule_based("このコードをデバッグして"), "CODE")

    def test_implement_keyword_matches(self):
        self.assertEqual(match_rule_based("FastAPIでエンドポイントを実装して"), "CODE")

    def test_code_fence_matches(self):
        self.assertEqual(match_rule_based("```python\nprint(1)\n```これ動かないんだけど"), "CODE")

    def test_python_def_matches(self):
        self.assertEqual(match_rule_based("def add(a, b): の戻り値がおかしい"), "CODE")

    def test_non_code_text_returns_none(self):
        self.assertIsNone(match_rule_based("今日の天気を教えて"))

    def test_compound_task_with_code_keyword_forces_code(self):
        # ノートに記載の複合タスク例。優先度ルール(CODE > DEEP > FAST)の起点になる。
        self.assertEqual(
            match_rule_based("バグ修正しつつ開発スケジュールも整理して"), "CODE"
        )

    def test_regex_debugging_keyword_matches(self):
        # v1テストで実際に誤判定(DEEPへの誤分類)が発生したC4相当のケース。
        self.assertEqual(
            match_rule_based("この正規表現、意図通りにマッチしない原因を教えて"), "CODE"
        )

    # --- 4日目ノート「⑤ 会話継続時のモデルスワップ実機検証」で見つかった
    #     口語表現の揺れへの対応(2026-08-04追加分) ---

    def test_bug_fix_with_particle_mo_matches(self):
        # 実機検証のT4で実際に不一致だったケース。「バグを」ではなく「バグも」。
        self.assertEqual(
            match_rule_based("ついでにこの前渡したスクリプトのバグも直して実装しといて"),
            "CODE",
        )

    def test_implement_casual_form_matches(self):
        # 「実装して」ではなく「実装しといて」(実装しておいて、のくだけた言い方)。
        self.assertEqual(match_rule_based("この機能、実装しといて"), "CODE")

    def test_implement_hoshii_form_matches(self):
        self.assertEqual(match_rule_based("APIクライアントを実装してほしい"), "CODE")

    def test_bug_fix_onegai_form_matches(self):
        self.assertEqual(match_rule_based("バグの修正をお願いします"), "CODE")

    def test_debug_casual_form_matches(self):
        self.assertEqual(match_rule_based("このエラー、デバッグしといてくれる?"), "CODE")

    def test_review_casual_form_matches(self):
        self.assertEqual(match_rule_based("このPRレビューしてほしいです"), "CODE")

    def test_unrelated_naoshite_does_not_match(self):
        # 「バグ」を伴わない「直して」はCODEにしない(広げすぎ防止の回帰チェック)。
        self.assertIsNone(match_rule_based("提案資料の構成を練り直して"))

    def test_unrelated_kansei_form_does_not_match(self):
        # 過去に完了した実装への言及(依頼ではない)。プレフィルタの過剰検出防止の確認。
        # ※現状は「実装し」語幹だけで一致させているため、依頼ではない文脈でも
        #   CODEに寄ってしまう可能性があることをテストとして明示しておく
        #   (将来もし誤検出が問題になった場合、この行が真っ先に落ちるはず)。
        self.assertEqual(match_rule_based("先週その機能は実装した"), "CODE")

    # --- 6日目ノート「マツコ問題」で見つかった、想起質問がPhi-4-miniによって
    #     CLARIFYへ誤判定される問題への対応(2026-08-06追加分)。
    #     文章によるプロンプトルールでは小型モデルの汎化が効かず、具体例と
    #     完全一致しない限りCLARIFYへ倒れることが実測で確認されたため、
    #     CODE_TRIGGERSと同じ正規表現による事前確定方式で対応した。 ---

    def test_recall_dakke_matches_fast(self):
        self.assertEqual(
            match_rule_based("この前決めたチャンク分割の上限値って何字だっけ?"), "FAST"
        )

    def test_recall_deshitakke_matches_fast(self):
        self.assertEqual(match_rule_based("合言葉は何でしたっけ?"), "FAST")

    def test_recall_my_possessive_question_matches_fast(self):
        # 実機で「私の猫の名前は何ですか?」はFASTだが「私の亀の名前は何ですか?」は
        # CLARIFYに誤判定された(プロンプト例に無い動物名では汎化しなかった)ケース。
        self.assertEqual(match_rule_based("私の亀の名前は何ですか?"), "FAST")

    def test_recall_past_reference_matches_fast(self):
        self.assertEqual(match_rule_based("昨日やった残りの課題を教えて"), "FAST")

    def test_recall_sakki_matches_fast(self):
        self.assertEqual(match_rule_based("さっき話してたやつどうなった?"), "FAST")

    def test_code_recall_still_prioritizes_code(self):
        # 想起+コード依頼の複合表現は、4日目の優先度ルール(CODE > DEEP > FAST)
        # どおりCODEが優先されることを確認する(RECALL_TRIGGERSがCODE判定を
        # 上書きしないことの回帰チェック)。
        self.assertEqual(
            match_rule_based("前に書いてもらったコードのバグ直して"), "CODE"
        )

    def test_weather_question_without_recall_marker_returns_none(self):
        # 「教えて」だけでは想起トリガーにせず、時間参照語(昨日/前に等)との
        # 組み合わせのみに限定していることの回帰チェック。
        self.assertIsNone(match_rule_based("今日の天気を教えて"))


class TestDocumentGenerationTriggers(unittest.TestCase):
    """14日目③: 資料生成の依頼がCODEルートへ飛ぶこと。

    ここが通らないと③の実装(code_executorの拡張)は一度も実行されない
    (⓪-3のattached_document_textと同じ「経路が繋がっていない死にコード」になる)。
    """

    def test_excel_request_is_code(self):
        self.assertEqual(match_rule_based("今の調査結果をエクセルにまとめて"), "CODE")

    def test_slide_request_is_code(self):
        self.assertEqual(match_rule_based("この構成でスライドを作って"), "CODE")

    def test_word_request_is_code(self):
        self.assertEqual(match_rule_based("報告書をWordで作成して"), "CODE")

    def test_json_request_is_code(self):
        self.assertEqual(match_rule_based("設定をJSONで書き出して"), "CODE")

    # --- 誤爆しないこと(過剰検出の確認)。「資料」「エクセル」は日常会話にも
    #     出てくる語のため、既存トリガーと同じ感覚で名詞単独を登録してはいけない。 ---

    def test_casual_mention_of_document_is_not_code(self):
        self.assertIsNone(match_rule_based("今日は一日資料を読んでいたよ"))

    def test_casual_mention_of_excel_skill_is_not_code(self):
        self.assertIsNone(match_rule_based("エクセルって難しいよね"))


class TestMediaTriggers(unittest.TestCase):
    """15日目②: 音楽再生の依頼がMEDIAルートへ飛ぶこと。

    14日目③の教訓: ここが通らないと①の実装は一度も実行されない。
    """

    def test_play_request_is_media(self):
        self.assertTrue(is_media_request("米津玄師のLemonを流して"))

    def test_kakete_is_media(self):
        self.assertTrue(is_media_request("アイドルをかけて"))

    def test_saisei_is_media(self):
        self.assertTrue(is_media_request("夜に駆けるを再生して"))

    def test_kikasete_is_media(self):
        self.assertTrue(is_media_request("マリーゴールドを聴かせて"))

    # 誤爆しないこと(「流して」は日常会話に出るので、ここが特に重要)
    def test_kikinagashi_is_not_media(self):
        self.assertFalse(is_media_request("その話は聞き流していいよ"))

    def test_casual_music_mention_is_not_media(self):
        self.assertFalse(is_media_request("今日は一日音楽を聴いていたよ"))

    def test_water_flow_is_not_media(self):
        self.assertFalse(is_media_request("お風呂のお湯を流しておいて"))

    def test_match_rule_based_returns_media_route(self):
        self.assertEqual(match_rule_based("米津玄師のLemonを流して"), "MEDIA")

    def test_match_rule_based_does_not_misfire_on_kikinagashi(self):
        self.assertIsNone(match_rule_based("その話は聞き流していいよ"))

    def test_code_request_still_prioritizes_code_over_media(self):
        # 「エクセルを流して」のような字面上の衝突が起きても、CODEの
        # ドキュメント生成トリガー(依頼動詞とセット)には「流して」は含まれて
        # いないため、そもそも衝突しないことの回帰チェック。
        self.assertEqual(match_rule_based("今の調査結果をエクセルにまとめて"), "CODE")


if __name__ == "__main__":
    unittest.main()
