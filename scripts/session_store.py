"""session_store.py — 14日目②: チャット履歴(画面表示用の逐語ログ)の永続化。

RAG記憶(rag_memory/memory_store.py)とは役割が違う点に注意:
memory_storeは「意味検索のためのベクトル付きチャンク」であり、「何ターン目に
何を話したか」を順番どおり復元する用途には向かない(検索でヒットした断片しか
返らない)。こちらは「画面へそのまま流し込むための逐語ログ」。
voice_gateway.py側でchat_id == session_id に揃えることで、片方から他方を
必ず引けるようにしてある(このモジュール自体はmemory_storeに一切触れない)。

保存先: {root}/{session_id}.json(1セッション1ファイル)。中身を直接開いて読める・
壊れても1セッションで済む・新しい依存を足さない、という理由でJSONファイルを選んだ
(規模は個人利用の数百件程度を想定。SQLiteは検索が速いがこの規模では過剰)。
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(__file__).parent / "data" / "sessions"
DEFAULT_TITLE = "新しい会話"
TITLE_MAX_CHARS = 30


def _now_iso() -> str:
    # マイクロ秒精度: 同一テスト実行内で連続して呼ばれても更新順序が一意に決まるようにする
    # (list_sessions()のupdated_at降順ソートが秒精度だと同着になりうるため)。
    return datetime.now().isoformat(timespec="microseconds")


def _new_session_id() -> str:
    # sess-{YYYYMMDD-HHMMSS}-{4桁乱数}: 時系列でソートでき、ファイル名としてそのまま使える
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"sess-{ts}-{random.randint(0, 9999):04d}"


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str
    route: str = ""
    ts: str = ""


@dataclass
class Session:
    session_id: str
    title: str = DEFAULT_TITLE
    created_at: str = ""
    updated_at: str = ""
    title_is_custom: bool = False  # リネーム済みなら自動タイトルで上書きしない
    turns: list[Turn] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        turns = [Turn(**t) for t in d.get("turns", [])]
        return cls(
            session_id=d["session_id"],
            title=d.get("title", DEFAULT_TITLE),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            title_is_custom=d.get("title_is_custom", False),
            turns=turns,
        )


class SessionStore:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._root / f"{session_id}.json"

    def _save(self, session: Session) -> None:
        self._path(session.session_id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def create_session(self) -> Session:
        now = _now_iso()
        session = Session(session_id=_new_session_id(), created_at=now, updated_at=now)
        self._save(session)
        return session

    def load_session(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return Session.from_dict(data)

    def list_sessions(self) -> list[Session]:
        sessions: list[Session] = []
        for path in self._root.glob("*.json"):
            session = self.load_session(path.stem)
            if session is not None:  # 破損したセッションはスキップ(一覧全体を落とさない)
                sessions.append(session)
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def append_turn(self, session_id: str, *, role: str, text: str, route: str = "") -> None:
        session = self.load_session(session_id)
        if session is None:
            raise KeyError(f"未登録のセッションです: {session_id}")
        turn = Turn(role=role, text=text, route=route, ts=_now_iso())
        session.turns.append(turn)
        session.updated_at = turn.ts
        # 最初のユーザー発話の先頭30字を自動タイトルとして採用する。リネーム済み
        # (title_is_custom)なら上書きしない。空文字(空白のみ)ならデフォルトのまま。
        is_first_user_turn = role == "user" and not session.title_is_custom and session.title == DEFAULT_TITLE
        if is_first_user_turn and text.strip():
            session.title = text.strip()[:TITLE_MAX_CHARS]
        self._save(session)

    def set_auto_title(self, session_id: str, title: str) -> None:
        """14日目③: バックグラウンドで生成したLLM要約タイトルを反映する。

        append_turn()が付ける「先頭30字」の暫定タイトルを、後から届く要約タイトルで
        置き換えるための入口。rename_session()と違い、
        - title_is_customなら何もしない(ユーザーの手動リネームを上書きしない)
        - updated_atは更新しない(サイドバーの並び順がバックグラウンド更新だけで
          入れ替わらないようにする)
        """
        session = self.load_session(session_id)
        if session is None:
            raise KeyError(f"未登録のセッションです: {session_id}")
        if session.title_is_custom:
            return
        session.title = title
        self._save(session)

    def rename_session(self, session_id: str, new_title: str) -> None:
        session = self.load_session(session_id)
        if session is None:
            raise KeyError(f"未登録のセッションです: {session_id}")
        session.title = new_title
        session.title_is_custom = True
        session.updated_at = _now_iso()
        self._save(session)

    def delete_session(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()

    def search_sessions(self, query: str) -> list[Session]:
        if not query:
            return self.list_sessions()
        q = query.lower()

        def matches(session: Session) -> bool:
            if q in session.title.lower():
                return True
            return any(q in t.text.lower() for t in session.turns)

        return [s for s in self.list_sessions() if matches(s)]
