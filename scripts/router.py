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

使い方:
    python router.py "このコードをデバッグして"
    python router.py "計画を立てて" --session-id thread1
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from router_rules import match_rule_based

SCRIPT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = SCRIPT_DIR / "prompts" / "router_classification" / "system_prompt_v3.txt"

# 7日目⓪の評価結果を踏まえ、ルーターを phi4-mini-cpu から gemma4-e4b-cpu(CPU固定版)に
# 差し替えた(testset_v1 20問で正答率100%、Phi-4-mini比で速度も同等以上)。
# 2日目ノートの検証結果と同じ理由(num_gpu 0のModelfileでCPU固定・keep_alive=-1で常駐)により、
# GPU版のgemma4:e4bをそのまま使うと FAST/DEEP/CODE(gpt-oss:20b/gemma4:26b/devstral-small-2:24b)
# とのVRAM奪い合いが発生する(GPU版は約9.9GB占有・CPU固定版は約2.8GBに圧縮できることを
# monitor_ollama.pyで実測済み)。CPU固定版は`Modelfile.gemma4-e4b-cpu`から
# `ollama create gemma4-e4b-cpu -f Modelfile.gemma4-e4b-cpu`で作成する。
ROUTER_MODEL = "gemma4-e4b-cpu:latest"

# gemma4系はOllama既定でthinkingモード(内部CoT)が有効なため、分類のような短いタスクでは
# 不要な遅延(実測で3〜4倍)を生む(7日目⓪で確認)。ルーター採用にあたり既定でFalse固定する。
# Ollama /api/generateのトップレベル`think`フィールドの上書き値:
# None: `think`を送らない(モデル既定値のまま)。False: thinkingモードを無効化。
# True: thinkingモードを明示的に有効化(罠の実測比較用)。
ROUTER_THINK: bool | None = False

ROUTE_MODEL_MAP: dict[str, str] = {
    "FAST": "gpt-oss:20b",
    "DEEP": "gemma4:26b",
    "CODE": "devstral-small-2:24b",
    "CLARIFY": ROUTER_MODEL,
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


def build_user_prompt(text: str, last_route: str | None) -> str:
    """Phi-4-miniに渡す実際のプロンプト文字列を組み立てる。

    last_routeが渡された場合(=会話が2ターン目以降)は、直前の分類結果を
    コンテキストとして添えることで、Phi-4-mini自身に「話題が継続しているなら
    そのまま/明確に変わったなら再分類」を判断させる。
    """
    if last_route is None:
        return text
    return (
        f"[会話の文脈] このスレッドはここまで \"{last_route}\" として扱われています。\n"
        f"今回の発言が同じ話題の続きであれば \"{last_route}\" のままにしてください。\n"
        "今回の発言の内容そのものが、他のカテゴリに明確に該当する具体的な依頼・質問である場合は、"
        "その内容に従って正しいカテゴリに判定し直してください。\n\n"
        f"質問: {text}"
    )


def classify_route(
    text: str,
    call_model: Callable[[str, str], str],
    model: str = ROUTER_MODEL,
    last_route: str | None = None,
) -> str:
    """質問文からルートを1つ決定する。

    call_model(system_prompt, user_text) -> raw_text の関数を注入することで、
    実際のOllama呼び出し(ollama_client.generate)とテスト用のフェイクを差し替えられるようにする。

    last_routeを渡すと、直前の分類結果をプロンプトに添えてPhi-4-miniに再判定させる
    (RouterSessionでの会話継続時の文脈保持に使用)。
    """
    rule_route = match_rule_based(text)
    if rule_route is not None:
        return rule_route

    system_prompt = load_system_prompt()
    user_prompt = build_user_prompt(text, last_route)
    raw = call_model(system_prompt, user_prompt)
    return parse_route_response(raw)


class RouterSession:
    """会話スレッドごとに直近のrouteを保持し、話題継続時の無用な再判定ブレを抑えるための状態管理。

    4日目ノートのエッジケース対応方針:
      - 同一スレッド内で話題が継続している場合は、直近のrouteをなるべく維持する(モデルスワップの頻発防止)。
      - ただしCODE_TRIGGERSに明示的に一致した場合は、保持中のrouteより優先してCODEに上書きする
        (複合タスクの優先度ルール CODE > DEEP > FAST の実装箇所)。

    2026-08-05実機検証で発覚した修正:
      当初は「同一session_idなら2ターン目以降は一切再分類しない(Phi-4-miniを呼ばない)」設計だったが、
      実際にOpen WebUI上で同じチャット内で全く異なる話題の質問を続けて送ると、最初に判定されたrouteに
      永続的に固定されてしまい、FASTの質問がCLARIFYのまま、DEEPの質問がCODEのまま返る、という誤動作が
      発生した(⑤で検証したのは「同じ話題の短い続き」のケースのみで、「話題そのものが変わるケース」は
      未検証だった)。
      対策として、毎ターン必ずPhi-4-miniに問い合わせるように変更した上で、直前のrouteを
      `classify_route(..., last_route=...)`経由でプロンプトに文脈として渡し、Phi-4-mini自身に
      「話題が継続 or 曖昧なら直前のrouteを維持」「話題が明確に変わったら再分類」を判断させる方式にした。
      これにより、⑤で確認した「短い曖昧な続きでの無用なモデルスワップ防止」という効果は保ちつつ、
      「話題が変わったのに固定され続ける」バグを解消した(トレードオフとして、2ターン目以降も
      毎回Phi-4-mini呼び出しのオーバーヘッド(3〜15秒程度)が発生するようになった)。
    """

    def __init__(self) -> None:
        self._last_route: dict[str, str] = {}

    def get_route(
        self,
        session_id: str,
        text: str,
        call_model: Callable[[str, str], str],
        *,
        force_route: str | None = None,
    ) -> str:
        """質問文からルートを1つ決定し、セッションへ記録する。

        force_route: 11日目④-1で結論を出した「画像添付時はDEEPへ強制ルーティングし、
        ルーター自体には画像を読ませない」を実現するための引数。指定された場合は
        ルールベース判定(match_rule_based)もPhi-4-mini/gemma4-e4b-cpu呼び出し
        (call_model)も一切行わず、そのrouteをそのままセッションへ記録して返す。
        画像添付の有無だけを見る軽い判定は呼び出し側(support_ai_auto_pipe.pipe())の
        責務とし、ここでは「強制されたら従う」だけに徹する。
        """
        if force_route is not None:
            self._last_route[session_id] = force_route
            return force_route

        rule_route = match_rule_based(text)
        if rule_route is not None:
            self._last_route[session_id] = rule_route
            return rule_route

        last_route = self._last_route.get(session_id)
        route = classify_route(text, call_model, last_route=last_route)
        self._last_route[session_id] = route
        return route

    def reset(self, session_id: str) -> None:
        """明示的にセッションの記憶をクリアする(新しい話題を始める際などに使用)。"""
        self._last_route.pop(session_id, None)


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
        # ROUTER_MODEL(CPU固定のPhi-4-mini)はVRAMを使わないため常駐させたままにする。
        # 停止対象はGPU上のFAST/DEEP/CODEモデルのみ。
        if m != target_model and m != ROUTER_MODEL:
            stop_model(m)



# 分類タスクなので生成のブレを許容しない。temperatureを指定しないと
# Ollama側の既定値(モデル依存・通常0.8前後)でサンプリングされ、
# 同じ入力・同じプロンプトでも呼び出しごとにFAST/CLARIFYがブレる
# (2026-08-06実機:「私の猫の名前はマツコです」がCLARIFYに化けた"マツコ問題"の
#  根本原因。system_prompt_v3.txtの例文を増やしても、根が温度=ランダム性なので
#  低確率での誤判定は残り続ける。temperature=0で貪欲デコードに固定し、
#  同一入力なら常に同一routeを返すようにする)。
CLASSIFY_OPTIONS: dict = {"temperature": 0}


def call_phi4(system_prompt: str, user_text: str) -> str:
    from ollama_client import generate

    return generate(
        model=ROUTER_MODEL,
        prompt=user_text,
        system=system_prompt,
        options=CLASSIFY_OPTIONS,
        think=ROUTER_THINK,
    )


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
            reply = generate(model=ROUTER_MODEL, prompt=clarify_prompt)
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
