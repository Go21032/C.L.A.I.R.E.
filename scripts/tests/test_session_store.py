"""tests/test_session_store.py
------------------------------
14日目②: チャット履歴(画面表示用の逐語ログ)の永続化(session_store.py)。

RAG記憶(rag_memory/memory_store.py)とは別物(役割の違いはsession_store.pyの
モジュールdocstring参照)。ここでは保存先を毎回 tmp_path へ差し替え、実ファイルを
汚さずに検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import session_store  # noqa: E402


def test_create_and_list_roundtrip(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    assert s.title == "新しい会話"
    assert [x.session_id for x in store.list_sessions()] == [s.session_id]


def test_title_is_derived_from_first_user_turn(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="明日の東京の天気を調べて", route="FAST")
    assert store.load_session(s.session_id).title == "明日の東京の天気を調べて"


def test_title_is_truncated_to_30_chars(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="あ" * 100, route="FAST")
    assert store.load_session(s.session_id).title == "あ" * 30


def test_title_is_not_overwritten_by_second_user_turn(tmp_path):
    # 最初のユーザー発話だけがタイトルの元になり、2回目以降では上書きされない
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="最初の発話", route="FAST")
    store.append_turn(s.session_id, role="user", text="2回目の発話", route="FAST")
    assert store.load_session(s.session_id).title == "最初の発話"


def test_rename_overrides_auto_title_and_survives_further_turns(tmp_path):
    # リネーム後に発話しても、自動タイトルで上書きされないこと(これが抜けると事故る)
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="最初の発話", route="FAST")
    store.rename_session(s.session_id, "筋トレメニュー相談")
    store.append_turn(s.session_id, role="user", text="2回目の発話", route="FAST")
    assert store.load_session(s.session_id).title == "筋トレメニュー相談"


def test_empty_first_user_turn_keeps_default_title(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="   ", route="FAST")
    assert store.load_session(s.session_id).title == "新しい会話"


def test_list_is_sorted_by_updated_at_desc(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    older = store.create_session()
    store.append_turn(older.session_id, role="user", text="古い方", route="FAST")
    newer = store.create_session()
    store.append_turn(newer.session_id, role="user", text="新しい方", route="FAST")
    # olderへ追記して更新日時を新しくすると、一覧の先頭へ来る
    store.append_turn(older.session_id, role="user", text="古い方への追記", route="FAST")
    ids = [x.session_id for x in store.list_sessions()]
    assert ids[0] == older.session_id
    assert ids[1] == newer.session_id


def test_append_turn_records_role_text_route_and_ts(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="こんにちは", route="FAST")
    store.append_turn(s.session_id, role="assistant", text="はい、こんにちは", route="FAST")
    turns = store.load_session(s.session_id).turns
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].text == "こんにちは"
    assert turns[1].text == "はい、こんにちは"
    assert all(t.ts for t in turns)


def test_delete_removes_file(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.delete_session(s.session_id)
    assert store.list_sessions() == []
    assert not (tmp_path / f"{s.session_id}.json").exists()


def test_search_matches_title_and_body(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s1 = store.create_session()
    store.append_turn(s1.session_id, role="user", text="筋トレメニューを考えて", route="FAST")
    s2 = store.create_session()
    store.append_turn(s2.session_id, role="user", text="明日の天気は?", route="FAST")

    by_title = store.search_sessions("筋トレ")
    assert [x.session_id for x in by_title] == [s1.session_id]

    by_body_only = store.search_sessions("天気")
    assert [x.session_id for x in by_body_only] == [s2.session_id]

    no_match = store.search_sessions("存在しないキーワード")
    assert no_match == []


def test_load_of_corrupted_json_does_not_break_listing(tmp_path):
    # 1件壊れても一覧全体が落ちないこと(壊れたセッションはスキップする)
    (tmp_path / "sess-broken.json").write_text("{ではないテキスト", encoding="utf-8")
    store = session_store.SessionStore(root=tmp_path)
    assert store.list_sessions() == []


def test_load_session_returns_none_for_unknown_id(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    assert store.load_session("sess-does-not-exist") is None


def test_rename_unknown_id_raises(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    with pytest.raises(KeyError):
        store.rename_session("sess-does-not-exist", "新タイトル")


def test_session_id_format(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    assert s.session_id.startswith("sess-")


def test_set_auto_title_overwrites_the_truncated_placeholder(tmp_path):
    # 14日目③: append_turn()が付けた「先頭30字」の暫定タイトルを、あとから
    # 届くLLM要約タイトルで上書きできること(voice_gateway.pyのバックグラウンド
    # 要約が完了したときに呼ぶ想定)。
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(
        s.session_id, role="user", text="この資料からおすすめな曲のタイトルは何かな", route="FAST"
    )
    store.set_auto_title(s.session_id, "おすすめの曲")
    assert store.load_session(s.session_id).title == "おすすめの曲"


def test_set_auto_title_is_noop_when_title_is_custom(tmp_path):
    # ユーザーが手動リネームした後に、遅れて届いたLLM要約タイトルで
    # 上書きしてしまうと事故になる(ブランチの承認済み設計どおり)。
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="最初の発話", route="FAST")
    store.rename_session(s.session_id, "手動でつけたタイトル")
    store.set_auto_title(s.session_id, "LLMが後から生成したタイトル")
    assert store.load_session(s.session_id).title == "手動でつけたタイトル"


def test_set_auto_title_does_not_bump_updated_at(tmp_path):
    # サイドバーの並び順(updated_at降順)が、バックグラウンドのタイトル更新だけで
    # 突然入れ替わらないようにする。
    store = session_store.SessionStore(root=tmp_path)
    s = store.create_session()
    store.append_turn(s.session_id, role="user", text="最初の発話", route="FAST")
    before = store.load_session(s.session_id).updated_at
    store.set_auto_title(s.session_id, "要約タイトル")
    assert store.load_session(s.session_id).updated_at == before


def test_set_auto_title_unknown_id_raises(tmp_path):
    store = session_store.SessionStore(root=tmp_path)
    with pytest.raises(KeyError):
        store.set_auto_title("sess-does-not-exist", "タイトル")
