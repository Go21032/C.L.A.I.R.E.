"""
title: C.L.A.I.R.E. (Auto)
author: gakuhari
version: 0.1.0
description: >
    Phi-4-mini(scripts/router.py)による4分類ルーター(FAST/DEEP/CODE/CLARIFY)を経由して、
    質問内容に応じたOllamaモデル(gpt-oss:20b / gemma4:26b / devstral-small-2:24b)へ
    自動的に振り分けるOpen WebUI用Pipe関数。

----------------------------------------------------------------------
4日目ノート(サポートAI作製計画/4日目Phi4ロジック設計.md)②の残タスク
「Open WebUIのPipe機能側の実装方法を確認し、`C.L.A.I.R.E. (Auto)`仮想モデルとして
ルーティング処理を組み込めるか検証(手動モード/自動モードの共存含む)」に対応するファイル。

■ 導入方法(Open WebUIインストール後に行うこと)
    1. Open WebUI管理画面 → Workspace → Functions → 「+」→「Create new function」
    2. このファイルの中身をそのまま貼り付けて保存する
       (Open WebUIはファイル先頭のdocstring内`title:` `description:`等のメタデータを
        自動的に読み取り、Functionの表示名・説明として使う)
    3. 保存すると「C.L.A.I.R.E. (Auto)」という名前の仮想モデルがモデル選択ドロップダウンに
       追加される。これを選ぶと本Pipeが呼ばれ、FAST/DEEP/CODE/CLARIFYへ自動振り分けされる。
    4. 手動モード(gpt-oss:20b等を直接選択)は今まで通りOpen WebUIのモデル選択に残るため、
       このPipeを追加しても既存の直接選択モードとは干渉しない(Open WebUIの仕組み上、
       Pipeはあくまで「選択肢が1つ増える」だけであり、既存モデルの選択導線は変更されない)。
       これで「手動モード/自動モードの共存」の要件を満たす。

■ ⚠️ このファイルの検証状況について(重要・正直に書いておく)
    このPCにはまだOpen WebUI自体がインストールされていない(1日目ノートで
    「Open WebUI等」として構想段階のまま、4日目時点でも導入作業は未着手)。
    そのため、本ファイルを実際にOpen WebUIのFunctionsとして読み込ませ、UI上で
    動作させる確認はまだできていない。
    Open WebUIのFunction(Pipe)APIは公式ドキュメント記載の規約(`Pipe`クラス、
    `Valves`、`pipe(self, body, __user__, __metadata__, ...)`という関数シグネチャ)に
    従って書いているが、Open WebUIのバージョンによって引数名や挙動が変わることがある。
    代わりに以下2点は実行して確認済み:
      1. `tests/test_support_ai_auto_pipe.py`: Ollama呼び出しをフェイクに差し替えた
         ユニットテストで、chat_idごとのセッション保持・CLARIFY分岐・エラー時の
         フォールバックといった「Pipe自体のロジック」が正しいことを確認。
      2. `smoke_test_pipe.py`: 実際のOllama・実際のモデルに対してPipeクラスを
         直接呼び出すスモークテスト(Open WebUIを介さずに、Pipeの中身が実機で
         最後まで動くかを確認)。
    Open WebUIを実際にセットアップした後は、Functions画面に読み込ませて
    「C.L.A.I.R.E. (Auto)」を選び、実際のチャットで動作確認することを次回タスクとして残す。
----------------------------------------------------------------------
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

# router.py / router_rules.py / ollama_client.py は
# サポートAI作製計画/scripts 直下にある。
#
# 注意: Open WebUIの「Functions」機能は、貼り付けたコードをファイルとして保存せず
# exec()で文字列として動的実行するため、__file__は実ファイルの場所を指さない
# (2026-08-05実機確認: `Path(__file__).resolve().parent.parent`ベースの実装だと
#  Open WebUI上で`ModuleNotFoundError: No module named 'router'`になった)。
# そのため、このPC上の絶対パスを直接指定してsys.pathに追加する。
# ローカルでのCLI実行・pytest実行時(__file__が有効な環境)は、そちらの相対位置を
# フォールバックとして使う。
ROUTER_SCRIPTS_DIR = Path(
    r"C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts"
)
if not ROUTER_SCRIPTS_DIR.exists():
    ROUTER_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(ROUTER_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(ROUTER_SCRIPTS_DIR))

import code_executor  # noqa: E402
import router  # noqa: E402
from ollama_client import OllamaError, generate  # noqa: E402

# 6日目ノート(サポートAI作製計画/6日目RAG記憶レイヤーのPipe組み込み.md)④⑤:
# 記憶レイヤー(rag_memory/scripts/memory_store.py)は、上のrouter.py等と違い
# このPC内ではなく外付けHDD(D:)側(D:\sapo_ai\rag_memory\scripts)に置かれている。
# HDDが未接続でもC.L.A.I.R.E.本体は従来どおり応答を返す必要がある(④の完了条件)ため、
# インポート自体をtry/exceptで包み、失敗したら記憶機能を丸ごと無効化(memory_store=None)
# するだけにとどめ、Pipe本体を止めない。
RAG_MEMORY_SCRIPTS_DIR = Path(r"D:\sapo_ai\rag_memory\scripts")
if str(RAG_MEMORY_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_MEMORY_SCRIPTS_DIR))

try:
    import memory_store  # noqa: E402
except Exception as e:  # HDD未接続・依存パッケージ未導入等、あらゆる失敗を許容する
    memory_store = None
    print(
        f"[claire] 記憶レイヤー(memory_store)の読み込みに失敗したため、記憶機能を無効化して起動します: {e}"
    )

# code_executor.WORKSPACE_DIRのエイリアス。テストからこのモジュール変数を
# 直接差し替えられるように、code_executor.WORKSPACE_DIRを直接参照せず
# こちらを経由する(tests/test_support_ai_auto_pipe_code_execution.py参照)。
WORKSPACE_DIR = code_executor.WORKSPACE_DIR

# CODEルートでdevstralを呼ぶ際に付与するシステムプロンプト。
# 4日目ノート⑩「CODEルートに実ファイル作成・実行までさせる」機能に対応。
# ユーザーの依頼が「ファイルを作って実行して」のような実行を伴うものであれば、
# 以下のACTIONブロック形式で出力するよう指示する。単なるコードレビュー・
# 質問応答であれば、このブロックを使わず普段どおりのテキストで回答してよい。
CODE_ACTION_SYSTEM_PROMPT = """あなたはユーザーのコーディング依頼に応えるアシスタントです。

ユーザーの依頼が「実際にファイルを作成してほしい」「実行して結果を見せてほしい」という
意図を含む場合は、説明文に続けて、以下の形式のACTIONブロックを回答に含めてください。

<ACTION path="ファイル名.py" run="true">
```python
(ここに実際に書き込むPythonコードの中身)
```
</ACTION>

- pathは専用の作業フォルダの直下に保存されるため、ディレクトリ名を含めず
  拡張子.pyのファイル名のみを指定すること(例: "hello.py"。"scripts/hello.py"のように
  ユーザーが言及したフォルダ名を含めたり、".."で親ディレクトリに出ようとしたりしないこと)。
- 作成するだけで実行までは不要な場合はrun="false"にすること。
- ユーザーの依頼がコードレビュー・質問応答・説明など、ファイル作成を伴わないものであれば、
  ACTIONブロックは使わず、通常の説明文だけで回答すること。
- ACTIONブロックは1回answerにつき1つだけ出力すること。
"""


class Pipe:
    class Valves(BaseModel):
        show_route_debug_prefix: bool = Field(
            default=True,
            description="応答の先頭に[route: FAST/DEEP/CODE/CLARIFY]を付けて、"
            "どのモデルに振り分けられたかをデバッグしやすくする",
        )
        code_execution_mode: str = Field(
            default="confirm",
            description="CODEルートでdevstralがファイル作成・実行を提案した場合の扱い。"
            '"off"=提案のみ(実行しない) / "confirm"=毎回ユーザーに実行して良いか確認する'
            "(既定・Claude Codeの確認モード相当) / "
            '"autonomous"=確認なしで即座に書き込み・実行する。',
        )
        memory_enabled: bool = Field(
            default=True,
            description="長期記憶(LanceDB)の検索・書き戻しを行うかどうか。"
            "OFFにすると6日目より前と同じ挙動に戻る(不具合時の切り分け用)。",
        )
        memory_top_k: int = Field(
            default=3,
            description="記憶を検索する際に取得する件数(DEEP/CODEルートのみ)。",
        )

    def __init__(self) -> None:
        self.type = "pipe"
        self.id = "claire"
        self.name = "C.L.A.I.R.E. (Auto)"
        self.valves = self.Valves()

        # チャット(会話)ごとにRouterSessionを保持する辞書。
        # 4日目ノート「⑤ 会話継続時のモデルスワップ実機検証」で確認した通り、
        # 「メッセージが来るたびにrouter.pyをサブプロセスで叩く」実装だと
        # RouterSessionが毎回作り直されてセッション保持ロジックが機能しない。
        # Open WebUIはPipeインスタンスをプロセス起動中ずっと使い回すため、
        # __init__でセッション辞書を持たせておけば、Pipeインスタンスの生存期間中
        # (Open WebUIサーバーが起動している間)は会話ごとのroute保持が機能する。
        self._sessions: dict[str, router.RouterSession] = {}

        # ⑩: confirmモードで「実行してよいか」を確認中のアクションを
        # チャットごとに保持する辞書。次のユーザー発言が肯定的な返事であれば実行し、
        # そうでなければ(話題が変わった等)破棄する。
        self._pending_actions: dict[str, code_executor.CodeAction] = {}

    def _get_session(self, chat_id: str) -> router.RouterSession:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = router.RouterSession()
        return self._sessions[chat_id]

    @staticmethod
    def _extract_chat_id(body: dict, metadata: dict | None) -> str:
        """会話を識別するIDを取り出す。

        Open WebUIのバージョンにより渡され方が微妙に異なる場合があるため、
        優先順位をつけて複数の候補から取得する。どれも取れない場合は
        "default"にフォールバックする(その場合、全チャットが同一セッション扱いに
        なってしまうので、実際にOpen WebUIへ導入する際にログで要確認)。
        """
        if metadata:
            chat_id = metadata.get("chat_id")
            if chat_id:
                return str(chat_id)
        chat_id = body.get("chat_id")
        if chat_id:
            return str(chat_id)
        return "default"

    @staticmethod
    def _extract_last_user_text(body: dict) -> str:
        messages = body.get("messages", [])
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content", "")
                # Open WebUIはcontentが文字列以外(画像添付時のlist形式等)になる
                # こともあるため、文字列以外は空文字扱いにして後段のNoneチェックに任せる。
                return content if isinstance(content, str) else ""
        return ""

    @staticmethod
    def _is_confirmation(text: str) -> bool:
        """ユーザーの発言が「実行してよい」という肯定的な返事かどうかを判定する。

        簡易的な部分一致方式。誤検出を避けるため、単独の"y"のような
        1文字だけの語や、一般的な単語に紛れやすい語("go"等)は含めない。
        """
        normalized = text.strip().lower()
        affirmative_words = [
            "はい",
            "yes",
            "ok",
            "おっけ",
            "お願いします",
            "実行して",
            "実行",
            "やってください",
            "やって",
        ]
        return any(word in normalized for word in affirmative_words)

    def _run_action(self, action: "code_executor.CodeAction") -> str:
        """CodeActionを実際にworkspace配下へ書き込み、必要なら実行して結果メッセージを返す。"""
        try:
            written_path = code_executor.write_action_file(action, WORKSPACE_DIR)
        except code_executor.UnsafePathError as e:
            return f"[error] ファイルの書き込みに失敗しました: {e}"

        if not action.run:
            return f"[C.L.A.I.R.E.] `{written_path}` を作成しました。"

        result = code_executor.execute_python_file(written_path)
        if result.timed_out:
            return f"[C.L.A.I.R.E.] `{written_path}` を作成しましたが、実行がタイムアウトしました。"

        lines = [
            f"[C.L.A.I.R.E.] `{written_path}` を作成し、実行しました(終了コード: {result.returncode})。"
        ]
        if result.stdout:
            lines.append(f"標準出力:\n{result.stdout}")
        if result.stderr:
            lines.append(f"標準エラー:\n{result.stderr}")
        return "\n".join(lines)

    def _handle_code_reply(self, chat_id: str, reply: str) -> str:
        """CODEルートのdevstral応答からACTIONブロックを検出し、モードに応じて処理する。"""
        action = code_executor.parse_action(reply)
        if action is None:
            return reply

        mode = self.valves.code_execution_mode
        if mode == "off":
            return reply

        if mode == "autonomous":
            return f"{reply}\n\n{self._run_action(action)}"

        # confirmモード(既定): 即実行せず、次のユーザー発言で確認する
        self._pending_actions[chat_id] = action
        confirm_question = (
            f"[C.L.A.I.R.E.] 上記の内容で `{action.path}` を作成"
            + ("し、実行" if action.run else "")
            + "してよろしいですか?(『はい』『実行して』などと返信してください)"
        )
        return f"{reply}\n\n{confirm_question}"

    # 6日目⑤: route別の検索(retrieve)・書き戻し(append)要否。
    # Phi-4-miniに判定させず、routeからの決定的なルールで済ませる方針(⑤参照)。
    #
    # 改善策(2026-08-06実機検証で発覚): 当初FASTはretrieve対象外だったが、
    # 「私の休みは何曜日ですか?」のような個人情報の想起質問はFASTに分類されるため、
    # FASTを除外したままだと⑧の本丸(「別チャットから前回の内容を参照できる」)が
    # 原理的に成立しないケースが実機で再現した。速度最優先という当初の狙いは
    # memory_top_kの絞り込み(既定3件)で担保しつつ、FASTもretrieveの対象に含める。
    RETRIEVE_ROUTES = {"FAST", "DEEP", "CODE"}
    APPEND_ROUTES = {"FAST", "DEEP", "CODE"}

    def _recall(self, route: str, user_text: str) -> str:
        """route別に過去の記憶を検索し、system用の文脈文字列を返す。失敗しても空文字を返す。"""
        if memory_store is None or not self.valves.memory_enabled or route not in self.RETRIEVE_ROUTES:
            return ""
        try:
            hits = memory_store.retrieve(
                user_text,
                limit=self.valves.memory_top_k,
                route="CODE" if route == "CODE" else None,
            )
            return memory_store.format_context(hits)
        except Exception as e:  # 記憶レイヤーの障害で本体を止めない(④完了条件)
            print(f"[claire] 記憶の検索に失敗(処理は継続): {e}")
            return ""

    def _remember(self, chat_id: str, route: str, user_text: str, reply: str) -> None:
        """ユーザー発言とアシスタント応答を記憶DBへ書き戻す。失敗しても本体は止めない。"""
        if memory_store is None or not self.valves.memory_enabled or route not in self.APPEND_ROUTES:
            return
        try:
            memory_store.append_turn(chat_id, "user", route, user_text)
            memory_store.append_turn(chat_id, "assistant", route, reply)
        except Exception as e:
            print(f"[claire] 記憶の書き戻しに失敗(処理は継続): {e}")

    def pipe(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        **_: Any,
    ) -> str | Iterator[str]:
        user_text = self._extract_last_user_text(body)
        if not user_text.strip():
            return "(質問内容が空でした。もう一度入力してください)"

        # 6日目⑧-2「マツコ問題」の根本原因への対応:
        # Open WebUIの「タスクモデル」が「現在のモデル」(=このPipe自身)になっていると、
        # タイトル生成・タグ生成・フォローアップ生成といったOpen WebUI内部のユーティリティ
        # 呼び出しまで、本物のユーザー発言と**同じchat_id**でここに流れてくる
        # (open_webui/routers/tasks.pyが__metadata__["task"]にタスク種別を、
        #  __metadata__["chat_id"]に本物の会話と同一のchat_idをセットして呼び出す)。
        # これをFAST/DEEP/CODE/CLARIFYの分類・RouterSessionに通してしまうと、
        # タスク文(JSON形式が違うためparse_route_responseが必ずCLARIFYにフォールバックする)
        # がそのchat_idのlast_routeとして記録され、直後の本物の発言の分類まで
        # 「CLARIFYが継続」として汚染される実害が実機で確認された。
        # そのため、タスク呼び出しは分類・セッション・記憶レイヤーを一切通さず、
        # 軽量モデルに直接投げて素直に返す(Open WebUI側は普通のテキスト応答を期待している)。
        if __metadata__ and __metadata__.get("task"):
            try:
                return generate(model=router.ROUTER_MODEL, prompt=user_text)
            except OllamaError as e:
                return f"[error] タスク処理に失敗しました: {e}"

        chat_id = self._extract_chat_id(body, __metadata__)

        # ⑩: confirmモードで確認待ちのアクションがあれば、今回の発言が
        # 肯定的な返事かどうかをまず確認する。肯定的でなければ(話題が変わった等)
        # 保留アクションを破棄して通常の分類フローに進む。
        pending_action = self._pending_actions.pop(chat_id, None)
        if pending_action is not None and self._is_confirmation(user_text):
            return self._run_action(pending_action)

        session = self._get_session(chat_id)

        error_prefix = ""
        try:
            route = session.get_route(chat_id, user_text, router.call_phi4)
        except OllamaError as e:
            route = router.DEFAULT_FALLBACK_ROUTE
            error_prefix = (
                f"[error] Phi-4-miniによる分類に失敗しました: {e}\n"
                f"[info] フォールバックとして{route}を採用します\n"
            )

        debug_prefix = f"[route: {route}]\n" if self.valves.show_route_debug_prefix else ""

        if route == "CLARIFY":
            clarify_prompt = (
                "以下の質問は曖昧で、どのカテゴリに分類すべきか判断できませんでした。"
                "何について知りたいのか具体的に聞き返してください。\n\n"
                f"質問: {user_text}"
            )
            try:
                reply = generate(model=router.ROUTER_MODEL, prompt=clarify_prompt)
            except OllamaError as e:
                reply = f"(聞き返し文の生成にも失敗しました: {e})"
            return f"{error_prefix}{debug_prefix}{reply}"

        # 6日目④⑤: route別に過去の記憶を検索し、system用の文脈として差し込む。
        memory_context = self._recall(route, user_text)

        try:
            router.ensure_model_ready(route)
            target_model = router.ROUTE_MODEL_MAP[route]
            if route == "CODE":
                # CODEルートは既にCODE_ACTION_SYSTEM_PROMPTをsystem=で渡しているため、
                # 記憶の文脈は上書きせず連結する(⑤の注意点。上書きするとACTIONブロック
                # 機能が壊れる)。
                system_prompt = CODE_ACTION_SYSTEM_PROMPT
                if memory_context:
                    system_prompt = f"{CODE_ACTION_SYSTEM_PROMPT}\n\n{memory_context}"
                reply = generate(model=target_model, prompt=user_text, system=system_prompt)
            else:
                reply = generate(model=target_model, prompt=user_text, system=memory_context or None)
        except OllamaError as e:
            target_model = router.ROUTE_MODEL_MAP[route]
            return f"{error_prefix}{debug_prefix}[error] {target_model} の呼び出しに失敗しました: {e}"

        # 6日目④⑤: 検索結果を反映する前の、モデルの生応答を記憶DBへ書き戻す
        # (ACTION実行結果メッセージ等の後付け文言は含めない)。
        self._remember(chat_id, route, user_text, reply)

        if route == "CODE":
            reply = self._handle_code_reply(chat_id, reply)

        return f"{error_prefix}{debug_prefix}{reply}"
