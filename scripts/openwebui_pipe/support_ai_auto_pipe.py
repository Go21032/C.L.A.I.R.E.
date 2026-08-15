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
import web_search  # noqa: E402 - 14日目: 13日目④で部品実装のみ区切っていたWeb検索を結線する
from ollama_client import OllamaError, generate, generate_stream  # noqa: E402

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

# 13日目「直近添付ファイルを自動優先」対応: rag_memory/doc_ingest.pyが
# `f"doc:{filename}"`の形でLanceDBのsource列へ登録する接頭辞と同じ値。
# doc_ingest.py自体をimportすると依存が増えるため、値だけをここでも定義しておく
# (doc_ingest.DOC_SOURCE_PREFIXと常に一致させること)。
DOC_SOURCE_PREFIX = "doc:"

# 14日目「添付ファイルをそのまま1ターンだけプロンプトへ埋め込む」対応。
# voice_gateway.DOC_INLINE_MAX_CHARSと同じ考え方の閾値だが、FastAPI層
# (voice_gateway.py)とPipe層(このファイル)は互いに直接importし合っていない
# 別モジュールのため、値をここでも独立に持つ。本来はクライアント
# (static/index.html)がアップロード時点でこの閾値以下と判定した場合にしか
# attached_document_textを送ってこないはずだが、クライアント側の改変・バグで
# 想定より大きなテキストが届いてもプロンプト予算(コンテキスト長・VRAM)を
# 壊さないための最終防衛ラインとして、ここでも同じ値でハードトランケートする
# (voice_gateway.DOC_INLINE_MAX_CHARSと値を合わせること)。
ATTACHED_DOCUMENT_TEXT_MAX_CHARS = 20000

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

# 12日目追記→13日目改訂: 画像添付(force_route=DEEP)時に、手書きワークアウト表などの
# 画像を「表にして」と頼まれた際の出力形式。当初はこのアプリの画面(#logはtextContent+
# white-space:pre-wrapで生テキストをそのまま表示するだけでMarkdownを描画しない)向けに
# TSV(タブ区切り)で出させていたが、実際にはExcel/スプレッドシートよりもObsidianの
# ノートへコピペして使いたいという要望が実機で判明した。ObsidianはMarkdownを描画する
# ため、TSVのままだと罫線の無いただの文章になってしまう。そのため、Markdownのパイプ表
# (| a | b |と区切り行 |---|---|)で出力させる方針に変更した。このアプリ自身の画面上では
# パイプ文字がそのまま見えてやや不格好になるが、Obsidianへ貼り付けたときに正しい表として
# 描画されることを優先する(ユーザーの明示的な判断)。画像添付ターンにのみ付与する
# (通常会話にまで常時付与すると、①-3でようやく短縮した応答時間に無駄なプロンプト処理
# コストが乗るため)。
TABLE_FORMAT_SYSTEM_PROMPT = """表形式のデータ(ワークアウト記録・一覧・比較表など)を
作成する場合は、タブ区切りテキストや```で囲んだコードブロックは使わず、Markdownの
パイプ表として出力してください。1行目に日本語の列見出しを入れた
「| 見出しA | 見出しB |」の形式の行を書き、その直下に区切り行
「|---|---|」(列数に合わせてハイフンの列を並べる)を必ず入れ、以降の行に
「| 値1 | 値2 |」の形式でレコードを1行ずつ並べてください。
これはObsidianのノートへそのままコピペして貼り付けたときに、罫線付きの表として
正しく描画されるようにするためです。表以外の説明文は普段どおり自然な日本語の
文章で書いてください。
"""


def _combine_system_prompts(*prompts: str | None) -> str | None:
    """空文字/Noneを除いて改行2つで連結する。全部空ならNoneを返す(呼び出し元の
    generate/generate_streamは system=None を「文脈なし」として扱うため)。"""
    parts = [p for p in prompts if p]
    return "\n\n".join(parts) if parts else None


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
        streaming_mode: str = Field(
            default="auto",
            description="FAST/DEEPルートの応答をトークン単位で逐次返すかどうか(9日目④)。"
            '"auto"=呼び出し側のリクエスト(body["stream"])に従う(既定・Open WebUIの流儀) / '
            '"always"=body["stream"]が無くても常にストリーミングする'
            "(Open WebUIのバージョンによってはstreamフラグが渡って来ないため、その場合の逃げ道) / "
            '"off"=8日目までと同じ全文一括応答に戻す(不具合時の切り分け用)。'
            "CODE・CLARIFY・タスク呼び出しは設定に関わらず常に全文応答のまま。",
        )
        web_search_enabled: bool = Field(
            default=True,
            description="Web検索(SearXNG経由、web_search.py)を利用するかどうか。"
            "OFFにするとクライアントのWeb検索トグルがONでも検索を一切行わない"
            "(memory_enabledと同じ、不具合時の切り分け用のキルスイッチ)。",
        )
        web_search_limit: int = Field(
            default=5,
            description="Web検索1回あたりに取得する結果件数の上限。",
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
    def _extract_last_user_images(body: dict) -> list[str]:
        """最後のuserメッセージに添付された画像(base64文字列のリスト)を取り出す。

        11日目④-1: OpenWebUIの「contentがlist形式になる」画像添付convention
        (`content: [{"type": "text", ...}, {"type": "image_url", ...}]`)は
        本Pipeでは元々解釈していない(_extract_last_user_textが空文字扱いにするだけ)。
        本実装ではそれとは別に、voice_gateway.py(自前UI)が組み立てる独自convention
        ―― 最後のuserメッセージ辞書に`"images"`キー(base64文字列のlist、
        data URL prefix無し)を直接持たせる形 ―― を読む。将来OpenWebUI側の
        画像添付にも対応する場合は、ここへ変換ロジックを足す形で拡張する想定。
        """
        messages = body.get("messages", [])
        for message in reversed(messages):
            if message.get("role") == "user":
                images = message.get("images")
                if isinstance(images, list):
                    return [img for img in images if isinstance(img, str) and img]
                return []
        return []

    @staticmethod
    def _extract_last_user_attached_document(body: dict) -> str | None:
        """最後のuserメッセージに添付された文書のファイル名を取り出す。

        13日目「直近添付ファイルを自動優先」対応: 📎で文書をアップロードした直後の
        次のテキスト送信では、voice_gateway.py(自前UI)がstatic/index.html側で
        覚えておいた直近アップロードファイル名を、`_extract_last_user_images()`が読む
        `"images"`キーと同じ独自convention ―― 最後のuserメッセージ辞書の
        `"attached_document"`キー ―― で載せて渡してくる。無ければNone(=従来どおり
        通常の全文脈検索のみを行う)。
        """
        messages = body.get("messages", [])
        for message in reversed(messages):
            if message.get("role") == "user":
                attached_document = message.get("attached_document")
                return attached_document if isinstance(attached_document, str) and attached_document else None
        return None

    @staticmethod
    def _extract_last_user_attached_document_text(body: dict) -> str | None:
        """最後のuserメッセージに添付された文書の抽出済み全文を取り出す。

        14日目「添付ファイルをそのまま1ターンだけプロンプトへ埋め込む」対応:
        📎でアップロードした文書の抽出テキストがvoice_gateway.DOC_INLINE_MAX_CHARS
        以下だった場合、static/index.html側が/documentsのレスポンスから覚えておき、
        `_extract_last_user_attached_document()`が読む`"attached_document"`キーと
        同じ独自conventionで、最後のuserメッセージ辞書の`"attached_document_text"`
        キーに載せて渡してくる。無ければNone(=従来どおりRAG検索側の経路のみを使う)。
        """
        messages = body.get("messages", [])
        for message in reversed(messages):
            if message.get("role") == "user":
                text = message.get("attached_document_text")
                return text if isinstance(text, str) and text else None
        return None

    @staticmethod
    def _extract_last_user_web_search(body: dict) -> bool:
        """最後のuserメッセージのWeb検索要求フラグを取り出す。

        14日目「13日目④の未結線を解消」対応: voice_gateway.py(自前UI)が
        `_extract_last_user_images()`等と同じ独自convention ―― 最後のuserメッセージ
        辞書の`"web_search"`キー(真偽値) ―― で載せて渡してくる。無ければFalse
        (=従来どおりWeb検索を行わない)。
        """
        messages = body.get("messages", [])
        for message in reversed(messages):
            if message.get("role") == "user":
                return bool(message.get("web_search"))
        return False

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

    # 9日目④: トークン単位で逐次返してよいroute。
    # CODEは`_handle_code_reply()`がACTIONブロックの解析に全文を必要とするため除外する。
    # CLARIFYは聞き返し1文で短く、ストリーミングしても体感が変わらない割に
    # 分岐が増えるため除外する(YAGNI。⑤の文分割は最初の1文が出れば十分速い)。
    STREAM_ROUTES = {"FAST", "DEEP"}

    def _recall(
        self,
        route: str,
        user_text: str,
        attached_document: str | None = None,
        attached_document_text: str | None = None,
    ) -> str:
        """route別に過去の記憶を検索し、system用の文脈文字列を返す。失敗しても空文字を返す。

        attached_document: 13日目「直近添付ファイルを自動優先」対応。指定されている場合、
        まずそのファイル(`source = f"doc:{attached_document}"`)だけに絞り込んだ検索を
        先に行い、ヒットがあればそちらをそのまま使う(「このファイルを要約して」のような
        あいまいな依頼でも、直近添付した1ファイルの内容が確実に文脈へ入るようにする狙い)。
        ヒットが無かった場合(ファイル名不一致等のエッジケース)は、下の通常の全文脈検索へ
        フォールバックし、従来より応答が悪化することがないようにする。

        attached_document_text: 14日目「添付ファイルをそのまま1ターンだけプロンプトへ
        埋め込む」対応。指定されている(=voice_gateway.DOC_INLINE_MAX_CHARS以下で
        📷画像と同じ「生データをそのまま毎ターン渡す」経路に乗った)場合は、上のRAG検索
        (source絞り込み・通常の全文脈検索のどちらも)を一切行わず、この全文をそのまま
        文脈として返す。検索を挟まないぶん「limit件のチャンクしか載らない」制約が無く、
        添付ファイルの全文が確実にそのターンの回答へ反映される(📷画像添付との対称性を
        意識した設計)。念のためATTACHED_DOCUMENT_TEXT_MAX_CHARSでハードトランケートする
        (冒頭のコメント参照。クライアント側の閾値判定に何かあってもここで安全弁が効く)。
        """
        if attached_document_text:
            truncated = attached_document_text[:ATTACHED_DOCUMENT_TEXT_MAX_CHARS]
            return (
                f"以下は直近添付されたファイル「{attached_document}」の全文です。"
                f"この内容に基づいて回答してください。\n\n{truncated}"
            )

        if memory_store is None or not self.valves.memory_enabled or route not in self.RETRIEVE_ROUTES:
            return ""
        try:
            if attached_document:
                doc_source = f"{DOC_SOURCE_PREFIX}{attached_document}"
                doc_hits = memory_store.retrieve(
                    user_text,
                    limit=max(self.valves.memory_top_k, 20),
                    source=doc_source,
                )
                if doc_hits:
                    return memory_store.format_context(doc_hits)

            # 7日目⑤: CODEルートを`route='CODE'`のみに絞ると、role='note'/
            # route='NOTE'で取り込んだノート由来の記憶(このプロジェクトの
            # 設計ノートはコード関連の記述が中心)が一切ヒットしなくなる
            # 設計上の衝突が実データ検証で判明したため、CODEルートは
            # CODE・NOTEの両方に絞り込む。11日目④でPDF/Word/Excel/PowerPoint取り込み
            # (doc_ingest.py、role='document'/route='DOCUMENT')を追加したため、
            # 同様にCODEルートでもナレッジ由来の記憶がヒットするよう対象へ加えた
            # (FAST/DEEPはroute=Noneで元々フィルタ無しのため変更不要)。
            hits = memory_store.retrieve(
                user_text,
                limit=self.valves.memory_top_k,
                route=("CODE", "NOTE", "DOCUMENT") if route == "CODE" else None,
            )
            return memory_store.format_context(hits)
        except Exception as e:  # 記憶レイヤーの障害で本体を止めない(④完了条件)
            print(f"[claire] 記憶の検索に失敗(処理は継続): {e}")
            return ""

    def _web_search_context(self, user_text: str, requested: bool) -> tuple[str, list]:
        """要求があればSearXNG(web_search.py)を叩き、system用の文脈文字列と
        検索結果のリスト(出典表示用)を返す。失敗しても("", [])を返し、Pipe本体を止めない。

        14日目: 13日目④で`web_search.py`本体+単体テストまでに区切っていた実装を、
        ここでPipeのパイプラインへ結線する。`web_search.search()`自体も内部で
        例外を握って空リストを返す設計だが(web_search.py参照)、フェイクへの
        差し替えテストも含めて二重に保険を掛ける(_recall()の記憶レイヤー障害対応と同じ方針)。
        """
        if not requested or not self.valves.web_search_enabled:
            return "", []
        try:
            results = web_search.search(user_text, limit=self.valves.web_search_limit)
        except Exception as e:  # noqa: BLE001 - Web検索の障害で本体を止めない
            print(f"[claire] Web検索に失敗(処理は継続): {e}")
            return "", []
        return web_search.format_for_prompt(results), results

    @staticmethod
    def _format_citations(results: list) -> str:
        """Web検索結果を応答末尾に添える出典(タイトル+URL)一覧として整形する。

        モデルがformat_for_prompt()の指示(本文中に出典URLを示す)を守るとは限らないため、
        プロンプト任せにせずここで機械的に必ず付与する(defense in depth)。
        ヒットが無ければ空文字(=何も付与しない)。
        """
        if not results:
            return ""
        lines = ["\n\n---\n出典:"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.title} - {r.url}")
        return "\n".join(lines)

    def _remember(self, chat_id: str, route: str, user_text: str, reply: str) -> None:
        """ユーザー発言とアシスタント応答を記憶DBへ書き戻す。失敗しても本体は止めない。"""
        if memory_store is None or not self.valves.memory_enabled or route not in self.APPEND_ROUTES:
            return
        try:
            memory_store.append_turn(chat_id, "user", route, user_text)
            memory_store.append_turn(chat_id, "assistant", route, reply)
        except Exception as e:
            print(f"[claire] 記憶の書き戻しに失敗(処理は継続): {e}")

    def _should_stream(self, body: dict, route: str) -> bool:
        """このリクエストをトークン単位で逐次返してよいかを判定する(9日目④)。"""
        if route not in self.STREAM_ROUTES:
            return False
        mode = self.valves.streaming_mode
        if mode == "off":
            return False
        if mode == "always":
            return True
        # "auto"(既定): 呼び出し側の要求に従う。Open WebUIはユーザー設定に応じて
        # body["stream"]を立てて渡してくる。voice_gateway.py(⑥)は常にTrueで呼ぶ。
        return bool(body.get("stream"))

    def _stream_reply(
        self,
        chat_id: str,
        route: str,
        user_text: str,
        memory_context: str,
        prefix: str,
        images: list[str] | None = None,
        citations: str = "",
    ) -> Iterator[str]:
        """FAST/DEEPの応答をトークン単位でyieldするジェネレータ(9日目④)。

        設計上の要点:
          - 例外を外へ投げない。ストリーミング中に例外を投げると、Open WebUIも
            音声UIも「途中で止まったまま無反応」になるため、エラーは文字列として
            yieldして呼び出し側に見せる。
          - `_remember()`はyieldし終えた後、連結した全文で1回だけ呼ぶ。
            **ここを落とすと音声で話した内容が記憶に残らなくなる**(④の厳守事項)。
            呼び出し側が途中で読むのをやめた場合(音声UIの中断)にも
            そこまでの応答を残すため、finallyに置く。
          - デバッグ接頭辞`[route: FAST]`は記憶には含めない(6日目④⑤と同じく
            モデルの生応答だけを書き戻す)。
          - images: 11日目④-1「画像添付時はDEEPへ強制ルーティング」対応。
            画像添付が無いターン(従来通り)ではNone/空のままgenerate_streamへ渡り、
            既存呼び出し元の挙動は変わらない。
          - citations: 14日目Web検索対応。`_format_citations()`が返す出典一覧文字列。
            トークン列がエラーなく終わった場合のみ、最後に1回だけyieldする
            (デバッグ接頭辞と同じく`_remember()`が書き戻す全文には含めない)。
        """
        target_model = router.ROUTE_MODEL_MAP[route]
        if prefix:
            yield prefix

        parts: list[str] = []
        try:
            try:
                router.ensure_model_ready(route)
                # 12日目追記: 画像添付ターンだけTABLE_FORMAT_SYSTEM_PROMPTを足す
                # (手書き表などをTSVでコピペ可能に出力させるため。理由は定義箇所参照)。
                system_prompt = _combine_system_prompts(
                    TABLE_FORMAT_SYSTEM_PROMPT if images else None, memory_context
                )
                stream = generate_stream(
                    model=target_model,
                    prompt=user_text,
                    system=system_prompt,
                    images=images or None,
                    think=router.ROUTE_THINK_MAP.get(route),
                )
            except OllamaError as e:
                yield f"[error] {target_model} の呼び出しに失敗しました: {e}"
                return

            try:
                for token in stream:
                    parts.append(token)
                    yield token
            except OllamaError as e:
                yield f"\n[error] {target_model} の応答が途中で終了しました: {e}"
            else:
                if citations:
                    yield citations
        finally:
            reply = "".join(parts)
            if reply:
                self._remember(chat_id, route, user_text, reply)

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

        # 11日目④-1: 画像添付があれば、ルーター(Phi-4-mini/gemma4-e4b-cpu)自体には
        # 画像を読ませず、分類ロジックを経由せず強制的にDEEPへルーティングする
        # (実測で、ルーターは画像に対してハルシネーションを起こしたうえ、
        # gemma4:26b(DEEP)より1.7倍近く遅かった。詳細は11日目ノート④-1参照)。
        images = self._extract_last_user_images(body)
        force_route = "DEEP" if images else None

        # 13日目「直近添付ファイルを自動優先」対応: 📎の文書添付直後のターンかどうかを
        # 取り出しておき、下の_recall()呼び出しへ渡す(ルーティング自体には関与しない)。
        attached_document = self._extract_last_user_attached_document(body)
        # 14日目: DOC_INLINE_MAX_CHARS以下の抽出テキストがあれば、_recall()内で
        # RAG検索を一切介さずこの全文をそのまま文脈として使う。
        attached_document_text = self._extract_last_user_attached_document_text(body)

        error_prefix = ""
        try:
            route = session.get_route(chat_id, user_text, router.call_phi4, force_route=force_route)
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
        # 13日目: attached_documentがあれば_recall()内でその1ファイルを優先的に検索する。
        # 14日目: attached_document_textがあれば検索すら行わず全文をそのまま使う。
        memory_context = self._recall(route, user_text, attached_document, attached_document_text)

        # 14日目: 13日目④で部品実装のみだったWeb検索をここで結線する。クライアントの
        # Web検索トグルON時だけSearXNGを叩き、結果をsystem文脈へ記憶と連結して差し込む
        # (どちらかを上書きしない。CODEルートのCODE_ACTION_SYSTEM_PROMPTとも同様)。
        web_search_requested = self._extract_last_user_web_search(body)
        web_context, web_results = self._web_search_context(user_text, web_search_requested)
        context = _combine_system_prompts(memory_context, web_context)
        citations = self._format_citations(web_results)

        # 9日目④: FAST/DEEPかつ呼び出し側がストリーミングを望む場合は、
        # ここでIteratorを返して以降の「全文を待つ」経路には入らない。
        if self._should_stream(body, route):
            return self._stream_reply(
                chat_id,
                route,
                user_text,
                context,
                f"{error_prefix}{debug_prefix}",
                images=images,
                citations=citations,
            )

        try:
            router.ensure_model_ready(route)
            target_model = router.ROUTE_MODEL_MAP[route]
            if route == "CODE":
                # CODEルートは既にCODE_ACTION_SYSTEM_PROMPTをsystem=で渡しているため、
                # 記憶/Web検索の文脈は上書きせず連結する(⑤の注意点。上書きするとACTIONブロック
                # 機能が壊れる)。
                system_prompt = _combine_system_prompts(CODE_ACTION_SYSTEM_PROMPT, context)
                reply = generate(
                    model=target_model,
                    prompt=user_text,
                    system=system_prompt,
                    think=router.ROUTE_THINK_MAP.get(route),
                )
            else:
                # images: 11日目④-1。DEEPが非ストリーミング(streaming_mode="off"等)で
                # 呼ばれた場合でも画像を渡せるよう、ここでも後方互換のoptional引数として渡す。
                # 12日目追記: 画像添付ターンだけTABLE_FORMAT_SYSTEM_PROMPTを足す(理由は定義箇所参照)。
                system_prompt = _combine_system_prompts(
                    TABLE_FORMAT_SYSTEM_PROMPT if images else None, context
                )
                reply = generate(
                    model=target_model,
                    prompt=user_text,
                    system=system_prompt,
                    images=images or None,
                    think=router.ROUTE_THINK_MAP.get(route),
                )
        except OllamaError as e:
            target_model = router.ROUTE_MODEL_MAP[route]
            return f"{error_prefix}{debug_prefix}[error] {target_model} の呼び出しに失敗しました: {e}"

        # 6日目④⑤: 検索結果を反映する前の、モデルの生応答を記憶DBへ書き戻す
        # (ACTION実行結果メッセージ等の後付け文言は含めない。出典一覧も同様)。
        self._remember(chat_id, route, user_text, reply)

        if route == "CODE":
            reply = self._handle_code_reply(chat_id, reply)

        return f"{error_prefix}{debug_prefix}{reply}{citations}"
