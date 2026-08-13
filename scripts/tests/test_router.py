import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import DEFAULT_FALLBACK_ROUTE, RouterSession, classify_route, parse_route_response


class TestParseRouteResponse(unittest.TestCase):
    def test_valid_json(self):
        self.assertEqual(parse_route_response('{"route": "DEEP"}'), "DEEP")

    def test_json_with_surrounding_whitespace(self):
        self.assertEqual(parse_route_response('\n {"route": "FAST"} \n'), "FAST")

    def test_broken_json_recovered_by_regex(self):
        # 閉じ括弧が欠けているなど、json.loadsには失敗するが"route"キーは読み取れるケース
        self.assertEqual(parse_route_response('{"route": "CODE"'), "CODE")

    def test_invalid_route_value_falls_back(self):
        self.assertEqual(parse_route_response('{"route": "UNKNOWN"}'), DEFAULT_FALLBACK_ROUTE)

    def test_garbage_text_falls_back(self):
        self.assertEqual(parse_route_response('すみません、わかりません'), DEFAULT_FALLBACK_ROUTE)


class TestClassifyRoute(unittest.TestCase):
    def test_rule_based_short_circuits_llm(self):
        calls = []

        def fake_call(system: str, text: str) -> str:
            calls.append(text)
            return '{"route": "FAST"}'

        route = classify_route("このコードを実装してください", fake_call)
        self.assertEqual(route, "CODE")
        self.assertEqual(calls, [])  # ルールベースで確定したのでLLMは呼ばれない

    def test_llm_used_when_no_rule_match(self):
        def fake_call(system: str, text: str) -> str:
            return '{"route": "DEEP"}'

        route = classify_route("来月の家族旅行のスケジュールを組んで", fake_call)
        self.assertEqual(route, "DEEP")


class TestRouterSession(unittest.TestCase):
    def test_first_turn_classifies_via_llm(self):
        session = RouterSession()

        def fake_call(system: str, text: str) -> str:
            return '{"route": "DEEP"}'

        route = session.get_route("s1", "計画を立てて", fake_call)
        self.assertEqual(route, "DEEP")

    def test_second_turn_still_calls_llm_with_last_route_context(self):
        # 2026-08-05修正: 「2ターン目はLLMを呼ばない」旧仕様は、話題が変わった場合に
        # 誤ったrouteに固定され続けるバグの原因だったため廃止。
        # 現在は毎ターンLLMを呼び、直前のrouteをプロンプト文脈として渡す。
        session = RouterSession()
        calls = []

        def fake_call(system: str, text: str) -> str:
            calls.append(text)
            return '{"route": "DEEP"}'

        session.get_route("s1", "計画を立てて", fake_call)
        route2 = session.get_route("s1", "続けて詳しく教えて", fake_call)
        self.assertEqual(route2, "DEEP")
        self.assertEqual(len(calls), 2)  # 2ターン目もLLMを呼ぶ
        self.assertIn("DEEP", calls[1])  # 直前のrouteが文脈として渡されている

    def test_code_trigger_overrides_session_route(self):
        # 優先度ルール(CODE > 保持中のroute)の確認
        session = RouterSession()

        def fake_call(system: str, text: str) -> str:
            return '{"route": "DEEP"}'

        session.get_route("s1", "計画を立てて", fake_call)
        route2 = session.get_route("s1", "このコードをデバッグして", fake_call)
        self.assertEqual(route2, "CODE")

    def test_different_sessions_are_independent(self):
        session = RouterSession()

        def fake_call(system: str, text: str) -> str:
            return '{"route": "FAST"}'

        session.get_route("s1", "計算して", fake_call)
        route_s2 = session.get_route("s2", "計算して", fake_call)
        self.assertEqual(route_s2, "FAST")

    def test_topic_change_within_same_session_is_reclassified(self):
        # 実機バグ再現: 同じチャット内で話題が完全に変わった場合、
        # 直近routeに固定され続けず、新しい話題の内容に応じて正しく再分類されるべき。
        session = RouterSession()

        def fake_call(system: str, text: str) -> str:
            if "旅行" in text:
                return '{"route": "DEEP"}'
            if "天気" in text:
                return '{"route": "FAST"}'
            return '{"route": "CLARIFY"}'

        route1 = session.get_route("s1", "来月の家族旅行のスケジュールを組んで", fake_call)
        self.assertEqual(route1, "DEEP")

        route2 = session.get_route("s1", "今日の東京の天気を教えて", fake_call)
        self.assertEqual(route2, "FAST")  # 話題が変わったので再分類されるべき(旧実装ではDEEPのまま固定されてしまう)

    def test_force_route_skips_rule_and_llm(self):
        # 11日目④-1: 画像添付時にDEEPへ強制ルーティングする際、ルーター自体には
        # 画像を読ませない(match_rule_basedもcall_modelも一切呼ばれない)ことの確認。
        session = RouterSession()
        calls = []

        def fake_call(system: str, text: str) -> str:
            calls.append(text)
            return '{"route": "FAST"}'

        route = session.get_route(
            "s1", "このコードをデバッグして", fake_call, force_route="DEEP"
        )
        self.assertEqual(route, "DEEP")  # CODE_TRIGGERSに一致する文言でも上書きされない
        self.assertEqual(calls, [])

    def test_force_route_is_recorded_as_last_route_for_next_turn(self):
        session = RouterSession()

        def fake_call(system: str, text: str) -> str:
            return '{"route": "%s"}' % "FAST"

        session.get_route("s1", "この画像は何?", fake_call, force_route="DEEP")
        calls = []

        def recording_call(system: str, text: str) -> str:
            calls.append(text)
            return '{"route": "FAST"}'

        session.get_route("s1", "続けて教えて", recording_call)
        self.assertIn("DEEP", calls[0])  # 直前routeとして文脈に渡っている

    def test_reset_clears_session_state(self):
        session = RouterSession()
        calls = []

        def fake_call(system: str, text: str) -> str:
            calls.append(text)
            return '{"route": "DEEP"}'

        session.get_route("s1", "計画を立てて", fake_call)
        session.reset("s1")
        session.get_route("s1", "計画を立てて", fake_call)
        self.assertEqual(len(calls), 2)  # resetしたので2回ともLLMが呼ばれる


if __name__ == "__main__":
    unittest.main()
