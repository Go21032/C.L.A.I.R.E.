"""
code_executor.py
-----------------
CODEルート(devstral)に「実際にPC上でファイルを作成し、コードを実行するところまで」
やらせるための実行エンジン。4日目ノート(サポートAI作製計画/4日目Phi4ロジック設計.md)⑩の方針:

  - devstralの応答に、以下の専用フォーマット(ACTIONブロック)が含まれていれば
    「ファイル作成+実行」の意図とみなす。含まれていなければ通常のテキスト回答として
    そのまま扱う(コードレビュー・質問応答など、ファイル操作を伴わない場合はこれでよい)。

        <ACTION path="hello.py" run="true">
        ```python
        print("Hello World")
        ```
        </ACTION>

  - 書き込み先は必ずWORKSPACE_DIR(既定: scripts/workspace/)配下に限定する。
    router.py・ollama_client.py等の既存スクリプトを誤って(あるいは意図的に)
    上書きされることを防ぐための安全対策。相対パスに".."を含むなどして
    WORKSPACE_DIRの外に出ようとする場合はUnsafePathErrorを送出して拒否する。
  - 実行は「作成したPythonファイルをpythonコマンドで実行する」ことのみをサポートする。
    任意のシェルコマンド実行は許可しない(安全性のスコープを絞るため)。
  - タイムアウト(既定30秒)を必ず設ける。無限ループ等でプロセスが残り続けることを防ぐ。

このモジュール単体はOllama通信を一切行わない(責務を分離し、テストしやすくするため)。
実際にdevstralへ「ACTIONブロックで返して」と指示するプロンプト、confirm/autonomous
モードの切り替え、ユーザーへの確認メッセージの提示は
openwebui_pipe/support_ai_auto_pipe.py側の責務とする。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = SCRIPT_DIR / "workspace"

_ACTION_RE = re.compile(
    r'<ACTION\s+path="(?P<path>[^"]+)"\s+run="(?P<run>true|false)"\s*>\s*'
    r"```[a-zA-Z0-9_+-]*\n(?P<content>.*?)```\s*"
    r"</ACTION>",
    re.DOTALL,
)


class UnsafePathError(ValueError):
    """WORKSPACE_DIRの外側を指すパスが指定された場合に送出する。"""


@dataclass
class CodeAction:
    path: str
    content: str
    run: bool


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool


def parse_action(text: str) -> CodeAction | None:
    """devstralの応答テキストからACTIONブロックを1つ抽出する。

    見つからなければNoneを返す(=ファイル操作を伴わない通常の回答とみなす)。
    複数ブロックがある場合は最初の1つのみを対象とする(初版のスコープ)。
    """
    m = _ACTION_RE.search(text)
    if m is None:
        return None
    return CodeAction(
        path=m.group("path").strip(),
        content=m.group("content"),
        run=(m.group("run") == "true"),
    )


def resolve_safe_path(path_str: str, workspace_dir: Path) -> Path:
    """path_strをworkspace_dir配下の絶対パスに解決する。

    workspace_dirの外を指す場合(".."による親ディレクトリ脱出、別ドライブの絶対パス等)は
    UnsafePathErrorを送出する。
    """
    workspace_dir = workspace_dir.resolve()
    candidate = (workspace_dir / path_str).resolve()
    try:
        candidate.relative_to(workspace_dir)
    except ValueError:
        raise UnsafePathError(
            f"'{path_str}' はworkspace_dir({workspace_dir})の外を指しているため許可されません"
        ) from None
    return candidate


def write_action_file(action: CodeAction, workspace_dir: Path = WORKSPACE_DIR) -> Path:
    """CodeActionの内容をworkspace_dir配下に書き込み、書き込んだ絶対パスを返す。

    必要な親ディレクトリは自動作成する。パスがworkspace_dirの外を指す場合は
    resolve_safe_path()がUnsafePathErrorを送出する(=ファイルは作成されない)。
    """
    target = resolve_safe_path(action.path, workspace_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(action.content, encoding="utf-8")
    return target


def execute_python_file(path: Path, timeout: float = 30.0) -> ExecutionResult:
    """`python <path>` をサブプロセスで実行し、標準出力・標準エラー・終了コードを返す。

    timeout秒を超えて実行が終わらない場合はプロセスを強制終了し、
    timed_out=Trueとして返す(returncodeはNoneになる)。
    """
    try:
        proc = subprocess.run(
            ["python", str(path)],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        return ExecutionResult(stdout=stdout, stderr=stderr, returncode=None, timed_out=True)

    return ExecutionResult(
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
        timed_out=False,
    )
