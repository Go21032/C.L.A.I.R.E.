# ルーター実装(CODE_TRIGGERS事前フィルタ + ルータースクリプト本体) Implementation Plan

> **実施結果(2026-08-04)**: Task 1〜5すべて実装・検証完了。このvaultはgitリポジトリではないため、各タスクのgit commitステップは省略し、ファイル作成とテスト実行のみ実施した。
> - `router_rules.py` / `ollama_client.py` / `router.py` / `tests/test_router_rules.py` / `tests/test_router.py` を作成。`python -m unittest`で計18テスト全てPASS。
> - 環境に稼働中のOllamaサーバーが実在したため、Task5 Step3の疎通確認は実際にPhi-4-mini/gemma4/devstral-small-2を呼び出して実施。FAST/CODE/DEEPの代表例は期待通り分類されたが、CLARIFY境界例(「ちょっと相談があるんだけど」)が**FASTに誤判定**された。詳細は[[サポートAI作製計画/scripts/prompts/router_classification/testset_v1.md]]の実施記録を参照。
> - Task5 Step4(全20問の網羅テストによる正答率算出)は未実施。次回、時間を取って通しでテストし、`system_prompt_v1.txt`のFAST/CLARIFY境界の記述を見直してv2を作る想定。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** [[サポートAI作製計画/4日目Phi4ロジック設計.md|4日目ノート]]②の「本日のタスク」のうち、ルールベース事前フィルタ(CODE_TRIGGERS)とルータースクリプト本体を実装し、「質問入力 → (事前フィルタ) → Phi-4-mini判定 → 該当モデル呼び出し」まで一気通貫で動く状態にする。

**Architecture:** 3ファイル構成。`router_rules.py`(正規表現による事前フィルタ)、`ollama_client.py`(Ollama REST API薄いラッパー、標準ライブラリのみ)、`router.py`(分類ロジック・セッション状態・モデル切り替え・CLIエントリポイントを統合)。分類ロジック(`classify_route`, `parse_route_response`, `RouterSession`)はOllama呼び出し関数を引数として受け取る依存注入方式にし、実サーバーなしでも`unittest`で検証できるようにする。

**Tech Stack:** Python 3.12(このリポジトリの既存スクリプトと同じ)、標準ライブラリのみ(`re`, `json`, `urllib.request`, `subprocess`, `argparse`, `unittest`)。外部依存追加なし。

## Global Constraints

- 依存は標準ライブラリのみ(`monitor_ollama.py`の既存方針を踏襲。requests等は追加しない)。
- 分類プロンプトは既存の `サポートAI作製計画/scripts/prompts/router_classification/system_prompt_v1.txt` をそのまま使う(内容変更はこのプランの対象外)。
- ルート値は `"FAST" | "DEEP" | "CODE" | "CLARIFY"` の4値のみ。
- Phi-4-miniへの指示はJSON1行のみ出力(`{"route": "..."}`)。パース失敗時のフォールバックは `CLARIFY`。
- モデルマッピングは固定: `FAST→gpt-oss:20b`, `DEEP→gemma4:26b`, `CODE→devstral-small-2:24b`, `CLARIFY→phi4-mini:latest`(自己応答)。
- 会話継続中は直近routeを保持し、毎ターン再分類しない(CODE_TRIGGERSに一致した場合のみ強制的にCODEへ上書き=優先度ルール `CODE > 保持中のroute`)。
- テストは本リポジトリにpytest等の外部テストランナーが導入されていないため、標準ライブラリの`unittest`を使う。

---

## File Structure

- Create: `サポートAI作製計画/scripts/router_rules.py` — CODE_TRIGGERSの正規表現リストと、ルールベース判定関数。
- Create: `サポートAI作製計画/scripts/ollama_client.py` — Ollama REST APIの薄いラッパー(`generate`, `list_running_models`, `stop_model`)。
- Create: `サポートAI作製計画/scripts/router.py` — 分類ロジック(`parse_route_response`, `classify_route`, `RouterSession`, `ensure_model_ready`)とCLIエントリポイント(`main`)。
- Create: `サポートAI作製計画/scripts/tests/__init__.py` — 空ファイル(パッケージ化のため)。
- Create: `サポートAI作製計画/scripts/tests/test_router_rules.py` — `router_rules.py`のユニットテスト。
- Create: `サポートAI作製計画/scripts/tests/test_router.py` — `router.py`の分類ロジック(`parse_route_response`, `classify_route`, `RouterSession`)のユニットテスト(Ollama呼び出しはフェイク関数を注入してモック)。

---

### Task 1: CODE_TRIGGERSルールベース事前フィルタ

**Files:**
- Create: `サポートAI作製計画/scripts/router_rules.py`
- Test: `サポートAI作製計画/scripts/tests/test_router_rules.py`

**Interfaces:**
- Consumes: なし(このタスクが最初の土台)
- Produces: `match_rule_based(text: str) -> str | None` — CODEトリガーに一致すれば`"CODE"`、しなければ`None`を返す。Task 3で`classify_route`から呼ばれる。

- [ ] **Step 1: テストディレクトリと`__init__.py`を作成**

```bash
mkdir -p "サポートAI作製計画/scripts/tests"
touch "サポートAI作製計画/scripts/tests/__init__.py"
```

- [ ] **Step 2: 失敗するテストを書く**

`サポートAI作製計画/scripts/tests/test_router_rules.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router_rules import match_rule_based


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: テストを実行して失敗することを確認**

Run: `cd "サポートAI作製計画/scripts" && python -m unittest tests.test_router_rules -v`
Expected: FAIL / ERROR (`ModuleNotFoundError: No module named 'router_rules'`)

- [ ] **Step 4: 最小実装を書く**

`サポートAI作製計画/scripts/router_rules.py`:

```python
"""
router_rules.py
----------------
Phi-4-miniによるLLM判定の前段で、明示的な「コード関連」キーワードを
正規表現で検知するルールベース事前フィルタ。

4日目ノート(サポートAI作製計画/4日目Phi4ロジック設計.md)の方針:
  - 「コード書いて」等の明示語は問答無用でCODE確定させる(VRAM 16GB制約上、
    誤って重いモデルをロードし直すレイテンシを避けるため、Phi-4-mini判定を
    スキップして即CODEにする)。
  - 複合タスク(例:「バグ修正しつつ開発スケジュールも整理して」)は
    優先度ルール(CODE > DEEP > FAST)で単一ルートに倒す。CODEトリガーが
    含まれていればこの関数がCODEを返すため、優先度ルールはこの事前フィルタが
    router.py側で最優先に呼ばれることで実現される。
"""

from __future__ import annotations

import re

# 正規表現の断片。IGNORECASEでまとめてコンパイルする。
CODE_TRIGGERS: list[str] = [
    r"コード(を)?書いて",
    r"実装して",
    r"デバッグして",
    r"バグ(を)?(直して|修正)",
    r"リファクタリング",
    r"レビューして",
    r"def\s+\w+",
    r"class\s+\w+",
    r"```",
    r"エラーが出る",
    r"TypeError|ValueError|SyntaxError|NullPointerException|IndexError|KeyError",
    r"関数を(書いて|作って)",
]

_CODE_TRIGGER_RE = re.compile("|".join(CODE_TRIGGERS), re.IGNORECASE)


def match_rule_based(text: str) -> str | None:
    """CODE_TRIGGERSのいずれかに一致すれば"CODE"を返す。一致しなければNone。

    Noneの場合は呼び出し側(router.py)がPhi-4-miniによるLLM判定にフォールバックする。
    """
    if _CODE_TRIGGER_RE.search(text):
        return "CODE"
    return None
```

- [ ] **Step 5: テストを実行してパスすることを確認**

Run: `cd "サポートAI作製計画/scripts" && python -m unittest tests.test_router_rules -v`
Expected: PASS(6 tests)

- [ ] **Step 6: コミット**

```bash
git add "サポートAI作製計画/scripts/router_rules.py" "サポートAI作製計画/scripts/tests/__init__.py" "サポートAI作製計画/scripts/tests/test_router_rules.py"
git commit -m "feat: add CODE_TRIGGERS rule-based pre-filter for router"
```

---

### Task 2: Ollama REST APIラッパー

**Files:**
- Create: `サポートAI作製計画/scripts/ollama_client.py`

**Interfaces:**
- Consumes: なし(標準ライブラリの`urllib.request`, `subprocess`のみ)
- Produces:
  - `generate(model: str, prompt: str, system: str | None = None, host: str = "http://localhost:11434", keep_alive: int | str = -1, timeout: float = 60.0) -> str` — Task 3・4・CLI(main)から呼ばれる。
  - `list_running_models(host: str = "http://localhost:11434", timeout: float = 10.0) -> list[str]` — Task 4の`ensure_model_ready`から呼ばれる。
  - `stop_model(model: str, timeout: float = 20.0) -> None` — Task 4の`ensure_model_ready`から呼ばれる。
  - `OllamaError(RuntimeError)` — API呼び出し失敗時に送出。

このタスクは実際のOllamaサーバーへの接続が前提のため、ユニットテストは書かずコード単体でのimport確認のみ行う(手動確認はTask 5でルータースクリプト全体を動かす際に実施)。

- [ ] **Step 1: 実装を書く**

`サポートAI作製計画/scripts/ollama_client.py`:

```python
"""
ollama_client.py
-----------------
Ollama REST API(http://localhost:11434)への薄いラッパー。標準ライブラリのみで実装し、
requests等の追加依存を持ち込まない(monitor_ollama.pyの既存方針を踏襲)。

提供する関数:
  - generate(): /api/generate を叩き、生成テキストを1回分(stream=False)取得する。
  - list_running_models(): /api/ps を叩き、現在ロード済みのモデル名一覧を取得する。
  - stop_model(): `ollama stop <model>` をCLI経由で実行する(/api/generateに
    keep_alive=0を送る方法もあるが、モデルがロードされていない場合のエラーを
    避けるため、monitor_ollama.pyと同じCLI経由の方式に統一する)。
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

DEFAULT_HOST = "http://localhost:11434"


class OllamaError(RuntimeError):
    """Ollama API呼び出しが失敗した場合に送出する。"""


def generate(
    model: str,
    prompt: str,
    system: str | None = None,
    host: str = DEFAULT_HOST,
    keep_alive: int | str = -1,
    timeout: float = 60.0,
) -> str:
    """/api/generate を1回だけ叩き、レスポンステキストを返す(stream=False)。"""
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive,
    }
    if system:
        body["system"] = system

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(f"Ollama /api/generate 呼び出し失敗(model={model}): {e}") from e
    except json.JSONDecodeError as e:
        raise OllamaError(f"Ollama /api/generate のレスポンスがJSONとして不正: {e}") from e

    return payload.get("response", "")


def list_running_models(host: str = DEFAULT_HOST, timeout: float = 10.0) -> list[str]:
    """/api/ps を叩き、現在ロード済みのモデル名一覧を返す。"""
    req = urllib.request.Request(f"{host}/api/ps", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(f"Ollama /api/ps 呼び出し失敗: {e}") from e
    except json.JSONDecodeError as e:
        raise OllamaError(f"Ollama /api/ps のレスポンスがJSONとして不正: {e}") from e

    return [m["name"] for m in payload.get("models", [])]


def stop_model(model: str, timeout: float = 20.0) -> None:
    """`ollama stop <model>` をCLI経由で実行する(失敗しても例外にはしない=
    停止対象がそもそも起動していないケースを許容するため)。"""
    subprocess.run(
        ["ollama", "stop", model],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
```

- [ ] **Step 2: importできることを確認**

Run: `cd "サポートAI作製計画/scripts" && python -c "import ollama_client; print('ok')"`
Expected: `ok`(構文エラーがないことのみ確認。実サーバーへの接続確認はTask 5で行う)

- [ ] **Step 3: コミット**

```bash
git add "サポートAI作製計画/scripts/ollama_client.py"
git commit -m "feat: add stdlib-only Ollama REST API client"
```

---

### Task 3: 分類ロジック(JSON解析・フォールバック・ルール優先)

**Files:**
- Create: `サポートAI作製計画/scripts/router.py`(このタスクでは分類ロジック部分のみ)
- Test: `サポートAI作製計画/scripts/tests/test_router.py`

**Interfaces:**
- Consumes: `router_rules.match_rule_based(text: str) -> str | None`(Task 1)
- Produces:
  - `VALID_ROUTES: set[str]` = `{"FAST", "DEEP", "CODE", "CLARIFY"}`
  - `ROUTE_MODEL_MAP: dict[str, str]`(Task 5・CLIで使用)
  - `DEFAULT_FALLBACK_ROUTE: str` = `"CLARIFY"`
  - `parse_route_response(raw_text: str) -> str`(Task 4で使用)
  - `classify_route(text: str, call_model: Callable[[str, str], str], model: str = "phi4-mini:latest") -> str`(Task 4・CLIで使用)

- [ ] **Step 1: 失敗するテストを書く**

`サポートAI作製計画/scripts/tests/test_router.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import DEFAULT_FALLBACK_ROUTE, classify_route, parse_route_response


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗することを確認**

Run: `cd "サポートAI作製計画/scripts" && python -m unittest tests.test_router -v`
Expected: FAIL / ERROR(`router.py`が未作成、または関数未定義)

- [ ] **Step 3: 最小実装を書く**

`サポートAI作製計画/scripts/router.py`(このタスクの範囲はここまで。Task 4で追記する):

```python
"""
router.py
----------
「リーダーエージェント(ルーター)」本体。
4日目ノート(サポートAI作製計画/4日目Phi4ロジック設計.md)②の方針に基づき、
ユーザーの質問を FAST / DEEP / CODE / CLARIFY の4ルートに振り分け、
対応するOllamaモデルを呼び出す。

分類の優先順位:
  1. router_rules.match_rule_based() によるルールベース事前フィルタ
     (CODE_TRIGGERSに一致すれば即CODE確定。Phi-4-mini呼び出しをスキップする)
  2. Phi-4-miniへの分類依頼(scripts/prompts/router_classification/system_prompt_v1.txt を
     システムプロンプトとして使用し、JSON1行で route を返させる)

Phi-4-miniの出力はJSON1行の想定だが、崩れて返ってくる場合に備えて
parse_route_response() で正規表現による救済とフォールバックを行う。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from router_rules import match_rule_based

SCRIPT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = SCRIPT_DIR / "prompts" / "router_classification" / "system_prompt_v1.txt"

ROUTE_MODEL_MAP: dict[str, str] = {
    "FAST": "gpt-oss:20b",
    "DEEP": "gemma4:26b",
    "CODE": "devstral-small-2:24b",
    "CLARIFY": "phi4-mini:latest",
}
VALID_ROUTES: set[str] = set(ROUTE_MODEL_MAP)
DEFAULT_FALLBACK_ROUTE = "CLARIFY"

_JSON_ROUTE_RE = re.compile(r'"route"\s*:\s*"(\w+)"')


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def parse_route_response(raw_text: str) -> str:
    """Phi-4-miniの出力からrouteを取り出す。

    1. まずJSONとしてパースを試み、"route"の値が正しい4値のいずれかならそれを返す。
    2. JSONパースに失敗しても、"route": "XXX" のパターンが正規表現で拾えればそれを使う
       (崩れたJSON・前後に余計な文字が付いた場合の救済)。
    3. どちらも失敗、または値が4値以外なら DEFAULT_FALLBACK_ROUTE("CLARIFY")を返す。
    """
    text = raw_text.strip()

    try:
        data = json.loads(text)
        route = data.get("route") if isinstance(data, dict) else None
        if route in VALID_ROUTES:
            return route
    except json.JSONDecodeError:
        pass

    m = _JSON_ROUTE_RE.search(text)
    if m and m.group(1) in VALID_ROUTES:
        return m.group(1)

    return DEFAULT_FALLBACK_ROUTE


def classify_route(
    text: str,
    call_model: Callable[[str, str], str],
    model: str = "phi4-mini:latest",
) -> str:
    """質問文からルートを1つ決定する。

    call_model(system_prompt, user_text) -> raw_text の関数を注入することで、
    実際のOllama呼び出し(ollama_client.generate)とテスト用のフェイクを差し替えられるようにする。
    """
    rule_route = match_rule_based(text)
    if rule_route is not None:
        return rule_route

    system_prompt = load_system_prompt()
    raw = call_model(system_prompt, text)
    return parse_route_response(raw)
```

- [ ] **Step 4: テストを実行してパスすることを確認**

Run: `cd "サポートAI作製計画/scripts" && python -m unittest tests.test_router -v`
Expected: PASS(7 tests)

- [ ] **Step 5: コミット**

```bash
git add "サポートAI作製計画/scripts/router.py" "サポートAI作製計画/scripts/tests/test_router.py"
git commit -m "feat: add router classification logic with JSON parse fallback"
```

---

### Task 4: セッション状態管理(直近route保持・優先度ルール)

**Files:**
- Modify: `サポートAI作製計画/scripts/router.py`(`RouterSession`クラスを追記)
- Modify: `サポートAI作製計画/scripts/tests/test_router.py`(`TestRouterSession`を追記)

**Interfaces:**
- Consumes: `classify_route`, `match_rule_based`(Task 1・3で定義済み)
- Produces: `RouterSession`クラス — `get_route(session_id: str, text: str, call_model: Callable[[str, str], str]) -> str` と `reset(session_id: str) -> None`。CLI(Task 5)から使われる。

- [ ] **Step 1: 失敗するテストを追記する**

`サポートAI作製計画/scripts/tests/test_router.py`の末尾(`if __name__ == "__main__":`の直前)に追記:

```python
from router import RouterSession


class TestRouterSession(unittest.TestCase):
    def test_first_turn_classifies_via_llm(self):
        session = RouterSession()

        def fake_call(system: str, text: str) -> str:
            return '{"route": "DEEP"}'

        route = session.get_route("s1", "計画を立てて", fake_call)
        self.assertEqual(route, "DEEP")

    def test_second_turn_reuses_last_route_without_calling_llm(self):
        session = RouterSession()
        calls = []

        def fake_call(system: str, text: str) -> str:
            calls.append(text)
            return '{"route": "DEEP"}'

        session.get_route("s1", "計画を立てて", fake_call)
        route2 = session.get_route("s1", "続けて詳しく教えて", fake_call)
        self.assertEqual(route2, "DEEP")
        self.assertEqual(len(calls), 1)  # 2ターン目はLLMを呼ばない(再分類しない)

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
```

- [ ] **Step 2: テストを実行して失敗することを確認**

Run: `cd "サポートAI作製計画/scripts" && python -m unittest tests.test_router -v`
Expected: FAIL / ERROR(`ImportError: cannot import name 'RouterSession'`)

- [ ] **Step 3: 最小実装を`router.py`に追記**

`classify_route`関数の直後に追記:

```python
class RouterSession:
    """会話スレッドごとに直近のrouteを保持し、毎ターンの再分類を避けるための状態管理。

    4日目ノートのエッジケース対応方針:
      - 同一スレッド内は直近のrouteを保持し、毎ターン再分類しない(モデルスワップの頻発防止)。
      - ただしCODE_TRIGGERSに明示的に一致した場合は、保持中のrouteより優先してCODEに上書きする
        (複合タスクの優先度ルール CODE > DEEP > FAST の実装箇所)。
    """

    def __init__(self) -> None:
        self._last_route: dict[str, str] = {}

    def get_route(
        self,
        session_id: str,
        text: str,
        call_model: Callable[[str, str], str],
    ) -> str:
        rule_route = match_rule_based(text)
        if rule_route is not None:
            self._last_route[session_id] = rule_route
            return rule_route

        if session_id in self._last_route:
            return self._last_route[session_id]

        route = classify_route(text, call_model)
        self._last_route[session_id] = route
        return route

    def reset(self, session_id: str) -> None:
        """明示的にセッションの記憶をクリアする(新しい話題を始める際などに使用)。"""
        self._last_route.pop(session_id, None)
```

- [ ] **Step 4: テストを実行してパスすることを確認**

Run: `cd "サポートAI作製計画/scripts" && python -m unittest tests.test_router -v`
Expected: PASS(12 tests)

- [ ] **Step 5: コミット**

```bash
git add "サポートAI作製計画/scripts/router.py" "サポートAI作製計画/scripts/tests/test_router.py"
git commit -m "feat: add RouterSession for route persistence across turns"
```

---

### Task 5: モデル自動起動・切り替え + CLIエントリポイント統合

**Files:**
- Modify: `サポートAI作製計画/scripts/router.py`(`ensure_model_ready`, `call_phi4`, `main`を追記)

**Interfaces:**
- Consumes: `ollama_client.generate/list_running_models/stop_model`(Task 2)、`ROUTE_MODEL_MAP`, `RouterSession`(Task 3・4)
- Produces: CLI実行可能な`main()`(このタスクが末端。後続タスクなし)

このタスクは実Ollamaサーバーへの接続が前提のため、`ensure_model_ready`自体のユニットテストは書かない(`list_running_models`/`stop_model`をモックしたテストは費用対効果が低いため見送り、手動確認に留める)。ロジックの正しさはTask 3・4のテストで担保済みの`classify_route`/`RouterSession`を使う。

- [ ] **Step 1: `router.py`の末尾に追記**

```python
def ensure_model_ready(route: str) -> None:
    """routeに対応するモデルが未起動なら、他モデルを止めてから呼び出せる状態にする。

    実際のロードはgenerate()呼び出し時にOllamaが自動で行う(keep_alive=-1で常駐継続)。
    ここでは「別モデルが起動中なら止める」ことだけを担当する
    (3日目のスワップ検証で計測した待ち時間を踏まえ、無駄な二重ロードを避ける)。
    """
    from ollama_client import list_running_models, stop_model

    target_model = ROUTE_MODEL_MAP[route]
    running = list_running_models()
    if target_model in running:
        return
    for m in running:
        if m != target_model:
            stop_model(m)


def call_phi4(system_prompt: str, user_text: str) -> str:
    from ollama_client import generate

    return generate(model="phi4-mini:latest", prompt=user_text, system=system_prompt)


def main() -> None:
    import argparse
    import sys

    from ollama_client import OllamaError, generate

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(
        description="質問をFAST/DEEP/CODE/CLARIFYに振り分け、対応するOllamaモデルを呼び出すルーター"
    )
    parser.add_argument("question", help="ユーザーの質問文")
    parser.add_argument(
        "--session-id",
        default="default",
        help="会話スレッドを識別するID(同じIDなら直近routeを保持する)",
    )
    args = parser.parse_args()

    session = RouterSession()
    try:
        route = session.get_route(args.session_id, args.question, call_phi4)
    except OllamaError as e:
        print(f"[error] Phi-4-miniによる分類に失敗しました: {e}")
        print(f"[info] フォールバックとして {DEFAULT_FALLBACK_ROUTE} を採用します")
        route = DEFAULT_FALLBACK_ROUTE

    print(f"[route] {route} -> model: {ROUTE_MODEL_MAP[route]}")

    if route == "CLARIFY":
        clarify_prompt = (
            "以下の質問は曖昧で、どのカテゴリに分類すべきか判断できませんでした。"
            "何について知りたいのか具体的に聞き返してください。\n\n"
            f"質問: {args.question}"
        )
        try:
            reply = generate(model="phi4-mini:latest", prompt=clarify_prompt)
        except OllamaError as e:
            reply = f"(聞き返し文の生成にも失敗しました: {e})"
        print(reply)
        return

    try:
        ensure_model_ready(route)
        target_model = ROUTE_MODEL_MAP[route]
        reply = generate(model=target_model, prompt=args.question)
    except OllamaError as e:
        print(f"[error] {ROUTE_MODEL_MAP[route]} の呼び出しに失敗しました: {e}")
        return

    print(reply)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 既存の自動テストが壊れていないことを確認**

Run: `cd "サポートAI作製計画/scripts" && python -m unittest discover tests -v`
Expected: PASS(全19テスト。Task 1で6件、Task 3で7件、Task 4で5件+既存分)

- [ ] **Step 3: Ollamaサーバーを起動した状態で手動疎通確認する**

Run:
```bash
cd "サポートAI作製計画/scripts"
python router.py "今日の天気を教えて"
python router.py "このコードをデバッグして"
python router.py "来月の家族旅行のスケジュールを組んで"
python router.py "ちょっと相談があるんだけど"
```
Expected:
- 1問目: `[route] FAST -> model: gpt-oss:20b` の後、gpt-oss-20bの応答
- 2問目: ルールベースで即`[route] CODE -> model: devstral-small-2:24b`(Phi-4-mini呼び出しをスキップしている旨は`classify_route`の実装から自明。ログで確認したい場合はTask 6以降で検討)
- 3問目: `[route] DEEP -> model: gemma4:26b`
- 4問目: `[route] CLARIFY -> model: phi4-mini:latest` の後、聞き返し文
- 3日目のスワップ検証結果を踏まえ、切り替えごとの待ち時間が体感で許容範囲か目視確認する

- [ ] **Step 4: `testset_v1.md`のFAST/DEEP/CODE各5問+CLARIFY5問を通しで流し、正答率を記録する**

`サポートAI作製計画/scripts/prompts/router_classification/testset_v1.md`の「実際の出力」「正誤」欄に結果を追記する(手動)。8割未満のカテゴリがあれば`system_prompt_v1.txt`を調整して`v2`として保存し、再テストする(完了条件に明記されている基準)。

- [ ] **Step 5: コミット**

```bash
git add "サポートAI作製計画/scripts/router.py"
git commit -m "feat: add model auto-switch and CLI entrypoint for router"
```

---

## Self-Review

**1. Spec coverage:**
- 分類プロンプトの3→4分類書き換え → 前回セッションで`system_prompt_v1.txt`として完了済み(このプランの対象外、Task 3で読み込むだけ)。
- CODE_TRIGGERSキーワードリスト + フィルタ一致時の即CODE確定 → Task 1。
- ルータースクリプト本体(Ollama API・keep_alive -1) → Task 2・5。
- 対象モデル未起動時の自動起動・切り替え → Task 5の`ensure_model_ready`。
- JSON1行の安定性・パース失敗時のフォールバック → Task 3の`parse_route_response`。
- 会話継続時のroute保持ロジック → Task 4の`RouterSession`。
- 複合タスクの優先度ルール(CODE > DEEP > FAST) → Task 1のCODE_TRIGGERSがCODE最優先を担保し、Task 4の`RouterSession.get_route`が保持中routeより優先してCODEに上書きすることで実現。DEEP > FASTの優先度は、DEEP的表現がある質問はそもそもPhi-4-mini側がDEEPと判定する設計のため追加コードは不要と判断(system_prompt_v1.txtのDEEPヒントで担保)。
- CLARIFYルート時にPhi-4-mini自身が聞き返す → Task 5の`main()`内で実装。
- Open WebUI Pipe機能側の実装確認 → このプランの対象外(別タスクとして残す。下記「次のステップ」参照)。
- テスト用質問セットでの精度記録 → Task 5 Step 4。

**2. Placeholder scan:** 各StepにTBD/「後で実装」等のプレースホルダなし。コードは全て具体的な内容を記載済み。

**3. Type consistency:** `classify_route`/`RouterSession.get_route`とも`call_model: Callable[[str, str], str]`のシグネチャで統一。`ROUTE_MODEL_MAP`・`VALID_ROUTES`・`DEFAULT_FALLBACK_ROUTE`はTask 3で定義し、Task 4・5はそれをそのまま参照している(再定義なし)。

## 次のステップ(このプランの範囲外)

- Open WebUI側のPipe機能実装・検証(`さぽーとAI (Auto)`仮想モデルとしての組み込み)は、`router.py`のロジックをPipe用に呼び出す形に切り出す必要があり、別プランとして扱う。
- テスト結果(Task 5 Step 4)で正答率が8割を下回るカテゴリが出た場合の`system_prompt_v1.txt` v2への調整は、テスト実施後にあらためて着手する。
