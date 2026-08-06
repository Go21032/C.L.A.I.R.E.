---
project: C.L.A.I.R.E.(さぽーとAI)
date: 2026-08-06
tags: [RAG, ベクトルDB, LanceDB, OpenWebUI, Pipe, チャンク分割, 作業ログ]
status: 未着手
---

[[サポートAI作製計画/5日目RAG記憶DB構築.md|5日目]]でRAGの土台(LanceDB採用・Ruri v2 base導入・`conversations`テーブル作成・意味検索の動作確認)まで完了した。今回は5日目⑨に残した課題のうち、**チャンク分割ロジックの決定**と**`C.L.A.I.R.E. (Auto)` Pipeへの記憶レイヤー組み込み(検索+書き戻し)**を片付け、「会話が終わっても覚えている」状態を実際に成立させる。

> [!note] このノートの位置づけ
> 本ノートは5日目⑨「残課題・次回への持ち越し」を起点に、[[サポートAI作製計画/ノート作成規則.md]]に従って**着手前に作業内容を列挙した計画ノート**として作成した。実行後に「結果」「分析」「改善策」を各セクションへ追記していく。

---

## ① 本日のゴールと作業内容

### 背景/目的

5日目までで「記憶を貯める箱」は出来たが、**C.L.A.I.R.E.本体(Pipe)は箱の存在を知らない**。1日目設計④の図にある「RAG記憶DBに書き戻し」「過去文脈を検索して応答に差し込む」を実装して初めてパーソナライズが機能する。ただし毎ターン無条件に検索・書き戻しを行うと、4日目⑨で計測済みのPhi-4-mini呼び出し(3〜15秒)にさらに上乗せされるため、**レイテンシを測りながら組み込む**ことが本日の実質的な主題になる。

### 5日目⑨の残課題と本日の扱い

| #   | 5日目⑨の残課題                  | 本日の扱い        | 理由                                |
| --- | ------------------------- | ------------ | --------------------------------- |
| 1   | チャンク分割ロジックが未決定            | **本日実施**(③)  | Pipe組み込みより先に決めないと、後からDBを作り直す羽目になる |
| 2   | Pipeへの組み込み(検索+書き戻し)       | **本日実施**(④⑤) | 本日の主目的。次ステップ「4.」そのもの              |
| 3   | ルーターに「RAG検索が必要か否か」を判定させるか | **本日決着**(⑤)  | 実装方針に直結するため先送りできない。まずは非LLM方式で決める  |
| 4   | 記憶の肥大化対策(要約圧縮・削除ポリシー)     | **方針のみ**(⑦)  | 実データが溜まる前に作り込んでも検証できない。設計だけ残す     |
| 5   | 外付けHDD接続後のパス差し替え・クロスOS確認  | **次回送り**     | HDDが未接続(5日目②)。物理的に不可能             |
| 6   | Ruri v2 → v3-310m 差し替え検証  | **次回送り**     | v2で精度に不満が出ていない。先に組み込みを終える方が価値が高い  |
| 7   | 二層構成(Profile層)の導入(5日目⑩)   | **設計のみ**(⑦)  | Raw層が動いてから。同時に2層作ると切り分け困難         |

### 作業内容

- [ ] 事前確認:5日目に作った`rag_memory`一式(`config.yaml`/`init_db.py`/`test_search.py`/DB実体)の**実際の所在を特定**する➡中身のコードは5日目のノートの「### ⑧-1 具体的な検証手順(登録〜検索〜意味検索確認をまとめて実施)」などにあり、C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts\rag_memoryにもwindows環境にバックアップとして保存した。実際使うときのパスは`D:\sapo_ai\rag_memory\scripts`
- [ ] チャンク分割ロジックを決定し、`chunker.py`として実装する(会話ログ用/Markdownノート用)
- [ ] 記憶の検索・登録を担う共通モジュール`memory_store.py`を作成する(Pipeから`import`して使う部品)
- [ ] `support_ai_auto_pipe.py`に検索(retrieve)と書き戻し(append)を組み込む
- [ ] route別に検索の要否・件数を切り替える方針を実装する
- [ ] 検索ありなしでの**応答レイテンシを実測**し、上乗せ分が許容範囲か判断する
- [ ] 実際のチャットで「前回話した内容」を覚えているかをエンドツーエンドで確認する
- [ ] `OLLAMA_NUM_GPU=0`によるEmbeddingのCPU固定が効いているか(VRAM取り合いが起きないか)を確認する

### 完了条件(本日分)

- [ ] 1回目のチャットで話した内容が`conversations`テーブルに登録され、**新しいチャット(別`chat_id`)から質問しても参照できる**
- [ ] route別の検索要否が動作し、`FAST`など不要なルートでは検索がスキップされている
- [ ] 検索・書き戻しによるレイテンシ増加を数値で記録している
- [ ] Pipeが記憶DBに繋がらない場合でも、**従来どおり応答を返せる**(記憶レイヤーの障害が本体を止めない)

---

## ② 事前確認:5日目の成果物の所在(⚠️ 着手前に必ず実施)

### 背景/目的

本ノート作成時点でvault内を検索したところ、5日目⑥で設計した`rag_memory/`ディレクトリと`init_db.py`・`test_search.py`・`config.yaml`・`conversations.lance`が**vault内(`サポートAI作製計画/scripts/`配下)には存在しなかった**。5日目⑧-2では実行結果まで記録されているため、これらは**vault外のどこかに作られている**可能性が高い。場所が分からないままPipeを書くと`db_path`を誤指定して「空のDBが新規作成されて何もヒットしない」という分かりにくい事故になる。

### 実施方法

```powershell
# 1. 外付けHDD(D:)と、vault内バックアップ(C:)の両方を検索
Write-Host "=== D:\sapo_ai\rag_memory 配下の確認 ==="
Get-ChildItem -Path D:\sapo_ai\rag_memory -Recurse -Include init_db.py,test_search.py,config.yaml -ErrorAction SilentlyContinue |
    Select-Object FullName

Write-Host "`n=== vault内バックアップの確認 ==="
Get-ChildItem -Path C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts\rag_memory -Recurse -Include init_db.py,test_search.py -ErrorAction SilentlyContinue |
    Select-Object FullName

# 2. LanceDB(.lance)の確認
Write-Host "`n=== LanceDB実体の確認 ==="
Get-ChildItem -Path D:\sapo_ai\rag_memory -Recurse -Directory -Filter "*.lance" -ErrorAction SilentlyContinue |
    Select-Object FullName

# 3. 見つかったconfig.yamlの中身(db_pathが何を指しているか)を確認
Get-Content <見つかったパス>\config.yaml
```

### 結果

(実行後に記入:見つかった絶対パスと`db_path`の値)

1.1 valut内のバックアップ確認
```power shell
C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts\rag_memory\init_db.py
C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts\rag_memory\test_search.py
```
1.2 外付けHDDの確認
```power shell
D:\sapo_ai\rag_memory\scripts\init_db.py
D:\sapo_ai\rag_memory\scripts\test_search.py
D:\sapo_ai\rag_memory\config.yaml
```

2. LanceDB(.lance)の確認
D:\sapo_ai\rag_memory\db\conversations.lance

3. dpのパス確認
db_path: "D:/sapo_ai/rag_memory/db"
### 分析・対応方針

- **見つかった場合**:その場所を正としてvault内の`サポートAI作製計画/scripts/rag_memory/`へ移設するか、そのままの場所を`config.yaml`で指し続けるかを決める。Pipeから使う以上、**バックアップ(Obsidian同期)の対象に入るvault内へ寄せる方が安全**なので、原則は移設する。
- **見つからなかった場合**:5日目⑥・⑧-1のコードをそのまま`サポートAI作製計画/scripts/rag_memory/`配下に作り直し、`init_db.py`→`test_search.py`を再実行して5日目⑧-2と同じ結果が再現することを確認してから③へ進む。

> [!warning] `db_path`の指定ミスは無言で失敗する
> `lancedb.connect()`は存在しないパスを指定するとエラーにならず**新規にDBを作る**。「検索結果が0件」で初めて気づくことになるため、`memory_store.py`側で「テーブルの行数が0件なら警告を出す」防御を入れる(④参照)。

---

## ③ 課題1:チャンク分割ロジックの決定

### 背景/目的

5日目⑨の筆頭課題。Embeddingは「1ベクトル=1つの意味のまとまり」が前提であり、長すぎると話題が混ざって検索精度が落ち、短すぎると文脈が欠けて何の話か分からない断片が返る。会話ログとObsidianノートでは適切な単位が異なるため、**用途ごとに分ける**方針で決着させる。

### 選択肢と比較

| 方式 | 会話ログへの適性 | Obsidianノートへの適性 | 実装コスト | 備考 |
|---|---|---|---|---|
| A. 文字数固定(例:300字・オーバーラップ50字) | △(発言の途中で切れる) | △(表やコードブロックを分断する) | 低 | 汎用だが構造を無視する |
| B. 発言(ターン)単位 | **◎**(1発言=1つの意味のまとまり) | ×(そもそもターンが無い) | 低 | 長文発言は上限超過するのでAとの併用が必要 |
| C. Markdown見出し単位 | ×(会話に見出しは無い) | **◎**(`##`ごとに話題が分かれている) | 中 | 見出し配下が長い場合はAへフォールバック |
| D. 意味的分割(semantic chunking) | ○ | ○ | 高(embedding呼び出しが増える) | 精度は高いが本日のスコープには過剰 |

### 結論:会話ログ=B(+長文はAで再分割)、ノート=C(+長文はAで再分割)

理由:

1. C.L.A.I.R.E.が貯めるのは第一に**会話**であり、発言はもともと「1つの意図」で書かれた自然なまとまり。人工的に300字で切る理由がない。
2. Obsidianノート(このプロジェクトのノート自体が典型)は`## ①`〜`## ⑩`で話題が明確に分かれており、見出し単位が最も意味的な塊に近い。
3. どちらも「長すぎる場合だけ」文字数分割にフォールバックさせれば、実装は共通化できる(下記`chunker.py`)。Dは効果に対して呼び出しコストが見合わず、必要になってから検討する。

**上限値の暫定決定**:1チャンク=**400字**、オーバーラップ=**80字**。Ruri v2 base(BERT系・最大512トークン)の入力上限を日本語で超えないための保守的な値。⑨で実際にヒット内容を見て調整する。

### 実装:`rag_memory/scripts/chunker.py`(新規)

```python
"""テキストをEmbedding単位のチャンクへ分割する。

会話ログ = 発言単位(長すぎる場合のみ文字数で再分割)
Markdownノート = 見出し単位(同上)
単体実行はしない(memory_store.pyからimportして使う部品)。
"""
from __future__ import annotations

import re

MAX_CHARS = 400
OVERLAP = 80


def _split_by_length(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """長すぎるテキストをオーバーラップ付きで機械的に分割する(フォールバック)。"""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    step = max_chars - overlap
    while start < len(text):
        chunks.append(text[start : start + max_chars])
        start += step
    return chunks


def chunk_utterance(text: str) -> list[str]:
    """会話の1発言をチャンクへ分割する(基本は1発言=1チャンク)。"""
    return _split_by_length(text)


def chunk_markdown(text: str) -> list[str]:
    """Markdownを見出し(#〜######)単位で分割する。見出し行はチャンク先頭に残す
    (「何についての記述か」という文脈をベクトルに含めるため)。"""
    parts = re.split(r"(?m)^(?=#{1,6}\s)", text)
    chunks: list[str] = []
    for part in parts:
        chunks.extend(_split_by_length(part))
    return chunks
```

### 結果 / 分析 / 改善策

`rag_memory/scripts/test_memory_store.py`(新規)をD:\sapo_ai\rag_memory\scripts側で実行し、以下を確認した。

**結果**

| 検証項目 | 入力 | 結果 |
|---|---|---|
| 短文発言 | 12字 | 1チャンクのまま(分割されない) |
| 長文発言 | 1000字 | `[400, 400, 360, 40]`の4チャンクに分割。隣接チャンクの80字オーバーラップが完全一致 |
| Markdown見出し(3見出し、うち1つが400字超) | ― | 4チャンクに分割。`## 背景`→1チャンク、`## 実装`(400字超)→2チャンクに機械分割、`## まとめ`→1チャンク |

**分析:懸念が実際に再現した**

このノート③冒頭で懸念していた「見出し配下が400字を超えて機械分割されたチャンクが検索結果に出たとき、文脈が失われて読めなくなっていないか」が、**実測でそのまま再現した**。`## 実装`配下が400字を超えたケースで、2つ目のチャンク(197字)には見出し行`## 実装`が付いておらず、単独で検索結果に出てきた場合「何についての記述か」が分からない断片になる。`chunk_utterance`側(会話ログ)はターン単位なのでこの問題は起きない。影響があるのは`chunk_markdown`(Obsidianノート取り込み)のみで、5日目・6日目時点ではノート取り込みは未実施(⑨残課題)のため、**実害が出るのはノート取り込みを始めてから**。

**改善策**

- `chunker.chunk_markdown`の`_split_by_length`呼び出し側で、2チャンク目以降にも見出し行を先頭付与し直す(例:`f"{heading_line}\n{chunk_body}"`)よう修正する。ノート取り込み機能に着手するタイミング(⑨)で対応する。

### 残作業

- コードブロック(```〜```)を分断しない配慮は未実装。CODEルートの記憶で問題が出たら対応する。
- 見出し行が機械分割時に失われる問題への対応(上記改善策。ノート取り込み着手時)。

---

## ④ 課題2-1:記憶の検索・登録モジュール(`memory_store.py`)

### 背景/目的

Pipe本体(`support_ai_auto_pipe.py`)にLanceDB操作を直接書くと、Open WebUI上での差し替え(=コピペ貼り付け)のたびにDB周りのコードまで巻き添えで書き換わり、テストもしにくい。4日目で`router.py`/`ollama_client.py`を部品として切り出した構成に倣い、**記憶レイヤーも独立モジュールにする**。

### 実装:`rag_memory/scripts/memory_store.py`(新規)

```python
"""C.L.A.I.R.E.の長期記憶(LanceDB)への登録・検索を担う部品。

単体実行はしない(support_ai_auto_pipe.py からimportして使う部品)。
config.yaml のパス設定を唯一の情報源とし、Pipe側にパスを書かない。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import lancedb
import ollama
import yaml

import chunker

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

DB_PATH = CFG["db_path"]
EMBED_MODEL = CFG["embed_model"]
DOC_PREFIX = CFG["doc_prefix"]
QUERY_PREFIX = CFG["query_prefix"]
TABLE = "conversations"

# 5日目⑩:Ollamaは既定で空きGPUを使おうとする。残VRAMは約1GBしかなく、
# Gemma側とVRAMを取り合うとOOM・速度低下を招くためCPUへ固定する。
os.environ.setdefault("OLLAMA_NUM_GPU", "0")


def embed(text: str, is_query: bool = False) -> list[float]:
    prefix = QUERY_PREFIX if is_query else DOC_PREFIX
    return ollama.embed(model=EMBED_MODEL, input=f"{prefix}{text}")["embeddings"][0]


def _table():
    return lancedb.connect(DB_PATH).open_table(TABLE)


def append_turn(chat_id: str, role: str, route: str, text: str, topic: str = "") -> int:
    """1発言を記憶DBへ登録する。登録した行数を返す。"""
    chunks = chunker.chunk_utterance(text)
    if not chunks:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        {
            "id": str(uuid.uuid4()),
            "date": now,
            "source": f"chat:{chat_id}",
            "role": role,
            "route": route,
            "topic": topic,
            "content": c,
            "vector": embed(c, is_query=False),
        }
        for c in chunks
    ]
    _table().add(rows)
    return len(rows)


def retrieve(query: str, limit: int = 3, route: str | None = None) -> list[dict]:
    """クエリに意味的に近い過去の記憶を返す。routeを渡すとそのrouteに絞り込む。"""
    table = _table()
    if table.count_rows() == 0:
        # ②の警告どおり、db_path誤指定で空DBが新規作成された場合をここで検出する
        print("[memory_store] 警告: テーブルが空です。db_pathの指定を確認してください")
        return []
    search = table.search(embed(query, is_query=True))
    if route:
        search = search.where(f"route = '{route}'")
    df = search.limit(limit).to_pandas()
    return df[["content", "date", "role", "route", "_distance"]].to_dict("records")


def format_context(hits: list[dict], max_distance: float = 0.45) -> str:
    """検索結果をプロンプトへ差し込む文字列に整形する。
    距離が遠い(=関係ない)記憶は足を引っ張るのでmax_distanceで足切りする。"""
    useful = [h for h in hits if h["_distance"] <= max_distance]
    if not useful:
        return ""
    lines = [f"- ({h['date']}) {h['content']}" for h in useful]
    return "以下は過去の会話からの参考情報です。関連する場合のみ利用してください。\n" + "\n".join(lines)
```

### 設計上のポイント

| 項目                          | 決定                 | 理由                                                                                         |
| --------------------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| 距離の足切り(`max_distance=0.45`) | 導入する               | 5日目⑧-2の実測では、意味的に合致=0.25、無関係=0.41だった。無条件にtop-3を差し込むと**無関係な記憶がプロンプトを汚染**する。暫定値0.45は⑨の実測で調整する |
| 登録の対象                       | ユーザー発言+アシスタント応答の両方 | 「自分が何を答えたか」も文脈。ただし応答は長いためチャンク分割の効果が出る箇所                                                    |
| 例外方針                        | 呼び出し側(Pipe)で握り潰す   | 記憶レイヤーの障害で本体の応答が止まってはいけない(完了条件の4つ目)                                                        |

### 結果 / 分析 / 改善策

`test_memory_store.py`をD:\sapo_ai\rag_memory\scripts側で実行し、以下を確認した。

**結果**

| 検証項目 | 結果 |
|---|---|
| `embed()` | 768次元・約0.04秒(モデル常駐中) |
| `append_turn()` 5件登録 | モデル常駐中は1件あたり約0.06〜0.07秒。DB接続直後(モデル未ロード)の初回は1件目のロードに数秒かかり、合計3.5秒(1件あたり見かけ上0.7秒)になるケースを観測 |
| `retrieve()` 意味検索 | クエリ「休みの曜日を知りたい」→「毎週火曜日は**定休日**にしています」が`distance=0.2016`でトップ。表記ゆれをまたいだ意味検索が機能している |
| `retrieve(route=...)` 絞り込み | 指定routeのみが正しく抽出される |
| `format_context()` 距離足切り | `max_distance=0.2`では正解(0.2016)すら弾かれ、`0.45`では用意したサンプル(距離0.20〜0.36程度)が全件採用された |

**分析**

- Embeddingの精度は5日目実測(合致=0.25)よりむしろ良い数値(0.20)が出ており、`kun432/cl-nagoya-ruri-base`の実用性は問題ない。
- `append_turn`の所要時間差(0.06秒 vs 3.5秒)は、**Embeddingモデルがアンロードされた状態からの初回呼び出しでロード時間が乗る**ためと考えられる。⑧のレイテンシ計測で「モデル常駐中」と「アイドル明けの初回」を分けて記録しないと、上乗せ時間を過小評価するおそれがある。`OLLAMA_KEEP_ALIVE`を延長して常駐させる案(⑧の改善策候補①)の重要性が実測で裏付けられた。
- `max_distance=0.45`について:今回用意したサンプルは意図的に無関係な話題(天気の話等)を混ぜていなかったため、**閾値が実際にノイズを弾けているかまでは検証できていない**。5日目実測の「無関係=0.41」を踏まえると、0.45という暫定値は無関係な記憶を通してしまう可能性がある。

**改善策**

- ⑨実データが溜まった段階で、明確に無関係な発言を混ぜたテストケースを追加し、`max_distance`を0.3〜0.35程度まで絞ることを検討する。
- ⑧のレイテンシ計測表に「モデル常駐中」「アイドル明け初回」の列を分けて追加する。

---

## ⑤ 課題2-2 / 課題3:Pipeへの組み込みとroute別の検索要否

### 背景/目的

5日目⑨で「毎ターン検索するとPhi-4-miniの3〜15秒にさらに上乗せされる」「ルーターにRAG要否も判定させるか」を未決としていた。ここで決着させる。

### 判断:**Phi-4-miniにRAG要否を判定させない**(route由来のルールで決める)

理由:

1. 4日目④⑤の経験上、Phi-4-miniに判定軸を増やすほど**誤判定とレイテンシが増える**。routeとRAG要否は別軸なので、プロンプトに足すと分類そのものが不安定になるリスクがある。
2. routeが既に「どういう種類の依頼か」を表しており、**routeからRAG要否を導く決定的なルール**で十分な精度が期待できる(追加のLLM呼び出しゼロ)。
3. 足りなければ後からルールを足せる。逆にLLM判定は一度入れると切り分けが難しくなる。

### route別の方針

| route | 検索(retrieve) | 書き戻し(append) | 理由 |
|---|---|---|---|
| `FAST` | ~~しない~~ → **する**(top-3・絞り込みなし) | する | ~~短い即答用途。速度が最優先で、過去文脈が必要な場面が少ない~~ **【⑧実機検証で方針転換】**「私の休みは何曜日ですか?」のような個人情報の想起質問はFASTに分類されるため、除外したままだと本丸(チャットBからの参照)が原理的に成立しない。速度優先の狙いは`memory_top_k`(既定3件)の絞り込みで担保する |
| `DEEP` | **する**(top-3) | する | 相談・思考系。ユーザーの背景や過去の経緯が最も効く |
| `CODE` | **する**(top-3・`route='CODE'`で絞り込み) | する | 「前に書いたあのスクリプト」を引ける。コード以外の記憶が混ざると邪魔なのでroute絞り込みを入れる |
| `CLARIFY` | しない | **しない** | 聞き返し自体は記憶する価値が薄く、曖昧な発言をDBに入れると検索ノイズになる |

### 実装方針:`support_ai_auto_pipe.py`(修正)

Before/After(要点のみ):

| 項目 | 修正前 | 修正後 | 狙い |
|---|---|---|---|
| import | `router` / `ollama_client` / `code_executor` | + `memory_store` (`sys.path`に`rag_memory/scripts`を追加) | Open WebUIは`exec`実行で`__file__`が使えない(4日目⑧の教訓)ため、**絶対パス直指定+フォールバック**の既存パターンを踏襲する |
| Valves | `show_route_debug_prefix` / `code_execution_mode` | + `memory_enabled`(既定`True`) / + `memory_top_k`(既定3) | 記憶レイヤーだけをGUIから切れるようにする。不具合時の切り分け用 |
| 生成呼び出し | `generate(model, prompt=user_text)` | 検索ヒットがあれば`system=`に文脈を差し込んで`generate` | プロンプト本文を汚さず、参考情報として渡す |
| 応答後 | そのまま返す | ユーザー発言+応答を`append_turn`で登録 | 「書き戻し」の実装 |

```python
# 追加するヘルパー(Pipeクラス内)
RETRIEVE_ROUTES = {"DEEP", "CODE"}
APPEND_ROUTES = {"FAST", "DEEP", "CODE"}

def _recall(self, route: str, user_text: str) -> str:
    """route別に過去の記憶を検索し、system用の文脈文字列を返す。失敗しても空文字を返す。"""
    if not self.valves.memory_enabled or route not in self.RETRIEVE_ROUTES:
        return ""
    try:
        hits = memory_store.retrieve(
            user_text,
            limit=self.valves.memory_top_k,
            route="CODE" if route == "CODE" else None,
        )
        return memory_store.format_context(hits)
    except Exception as e:  # 記憶レイヤーの障害で本体を止めない
        print(f"[claire] 記憶の検索に失敗(処理は継続): {e}")
        return ""

def _remember(self, chat_id: str, route: str, user_text: str, reply: str) -> None:
    if not self.valves.memory_enabled or route not in self.APPEND_ROUTES:
        return
    try:
        memory_store.append_turn(chat_id, "user", route, user_text)
        memory_store.append_turn(chat_id, "assistant", route, reply)
    except Exception as e:
        print(f"[claire] 記憶の書き戻しに失敗(処理は継続): {e}")
```

> [!important] CODEルートのシステムプロンプト衝突に注意
> CODEルートは既に`CODE_ACTION_SYSTEM_PROMPT`を`system=`で渡している。記憶の文脈を渡す際に**上書きすると、ACTIONブロック機能(4日目⑩)が壊れる**。必ず連結(`CODE_ACTION_SYSTEM_PROMPT + "\n\n" + context`)にすること。これは実装時に最も踏みやすい事故。

### 結果 / 分析 / 改善策

`support_ai_auto_pipe.py`を上記方針どおりに修正し、以下を確認した。

**結果**

- `memory_store`のimportをtry/exceptで包み、失敗時は`memory_store = None`にして記憶機能ごと無効化する実装にした(HDD未接続時でも本体が起動・応答できることを保証)。
- Valvesに`memory_enabled`(既定True)・`memory_top_k`(既定3)を追加。
- `RETRIEVE_ROUTES = {DEEP, CODE}` / `APPEND_ROUTES = {FAST, DEEP, CODE}`のルールベース判定を実装(のちに⑧実機検証で`RETRIEVE_ROUTES`へ`FAST`を追加。上記「実機検証で発覚」参照)。CODEルートは`CODE_ACTION_SYSTEM_PROMPT`と記憶文脈を連結(上書きしない)。
- `tests/test_support_ai_auto_pipe_memory.py`(新規・8ケース)で以下をフェイク(`tests/fakes.py`)によりOllama/LanceDB非依存で検証し、全て合格:
  - route別のretrieve/append要否が⑤の表どおりに動く(FAST=append のみ、DEEP/CODE=両方、CLARIFY=両方なし)
  - CODEルートで`CODE_ACTION_SYSTEM_PROMPT`が上書きされず記憶文脈と連結される
  - `memory_enabled=False`で検索・書き戻しが完全に止まる
  - `memory_store`が`None`(HDD未接続を想定)でも本体の応答が止まらない
  - `retrieve`/`append_turn`が例外を送出しても本体の応答が止まらない(④の完了条件)

**分析:実装中に実害のあるバグを発見・修正した**

`support_ai_auto_pipe.py`に記憶レイヤーを組み込んだ直後、既存のユニットテスト(`tests/test_support_ai_auto_pipe.py`等)は`generate`のみをフェイクに差し替えており`memory_store`はフェイクにしていなかったため、**`pytest`を実行するたびに本番のD:\sapo_ai\rag_memory\db(実際のOllama Embedding経由)へテストデータが書き込まれる事故が発生した**(1回の`pytest`実行で36件、2回目の実行でさらに36件が本番`conversations`テーブルに混入するのを実機で確認)。これは②で警告していた「静かに失敗する」系の事故そのもので、放置すると⑧のエンドツーエンド確認で本物の会話とテスト由来のゴミが区別できなくなるところだった。

**改善策(対応済み)**

- `tests/fakes.py`に`NoopMemoryStore`(何もしないフェイク)・`RecordingMemoryStore`(呼び出し記録用)・`FailingMemoryStore`(障害シミュレート用)を追加。
- 既存の`tests/test_support_ai_auto_pipe.py`・`tests/test_support_ai_auto_pipe_code_execution.py`の`setUp`/`tearDown`で`support_ai_auto_pipe.memory_store`を`NoopMemoryStore`に差し替えるよう修正。
- 汚染してしまった本番DBのテストデータ(`route IN ('FAST','DEEP','CODE')`)は`table.delete()`で削除し、5日目からの`route='TEST'`データ(4件)のみの状態に復旧済み。
- `python -m pytest tests/`実行前後でD:\sapo_ai\rag_memory\dbの行数(4件)が変化しないことを確認済み(64件中64件合格)。

---

## ⑥ 実装の成果物一覧

### プログラムファイル

| ファイル | 役割 | 実行方法 | 出力先 |
|---|---|---|---|
| `rag_memory/scripts/chunker.py`(新規) | テキストをEmbedding単位へ分割する(発言単位/見出し単位) | 単体実行はしない(`memory_store.py`からimportして使う部品) | なし(戻り値のみ) |
| `rag_memory/scripts/memory_store.py`(新規) | 記憶DBへの登録(`append_turn`)・検索(`retrieve`)・整形(`format_context`) | 単体実行はしない(Pipeからimportして使う部品) | LanceDB(`config.yaml`の`db_path`) |
| `rag_memory/scripts/test_memory_store.py`(新規・実施済み) | 記憶レイヤー単体の動作確認(登録→検索→整形)。実行後に登録したテストデータ(`route LIKE 'TEST_MEMORY_STORE%'`)を自動削除し本番DBを汚さない | D:\sapo_ai\rag_memory\scripts側で`python test_memory_store.py` | 標準出力のみ・ファイル保存なし(結果は③④へ転記済み) |
| [[サポートAI作製計画/scripts/openwebui_pipe/support_ai_auto_pipe.py]](修正) | 記憶の検索・書き戻しを追加(⑤) | Open WebUIのFunctionsへ貼り付け | Open WebUIのチャット画面/LanceDB |
| `tests/fakes.py`(新規) | `memory_store`をテストで差し替えるフェイク(`NoopMemoryStore`/`RecordingMemoryStore`/`FailingMemoryStore`) | 単体実行はしない(他のtestsからimportして使う部品) | なし |
| `tests/test_support_ai_auto_pipe_memory.py`(新規) | ⑤で組み込んだ記憶レイヤーのroute別挙動・障害耐性のユニットテスト(8ケース) | `python -m pytest tests/test_support_ai_auto_pipe_memory.py -v` | 標準出力のみ |
| `tests/test_support_ai_auto_pipe.py` / `tests/test_support_ai_auto_pipe_code_execution.py`(修正) | `memory_store`をフェイクに差し替えるよう`setUp`/`tearDown`を修正(本番DB汚染事故の再発防止) | `python -m pytest tests/ -q` | 標準出力のみ |
| `rag_memory/scripts/memory_store.py`(修正・`count_rows()`追加) | `verify_memory_toggle.py`が「Pipe呼び出し前後で記憶DBの行数が変化したか」を機械的に判定できるようにする薄いヘルパー | 単体実行はしない | なし(戻り値のみ) |
| [[サポートAI作製計画/scripts/verify_memory_toggle.py]](新規) | ⑧「`memory_enabled`をOFFにすると5日目以前と同じ挙動に戻る」ことをOpen WebUI操作なしで自動検証する(実際のOllama・実際の記憶DBを使用。テストデータは自動後片付け) | `python verify_memory_toggle.py` | 標準出力のみ(本番DBへの書き込みは実行後に自動削除) |
| [[サポートAI作製計画/scripts/measure_memory_overhead.py]](新規) | ⑧「レイテンシ実測」「nvidia-smiでのVRAM確認」を自動化する。`monitor_ollama.py`の`GpuSampler`を流用し、`memory_enabled` ON/OFFでのPipe応答時間とVRAM使用量を比較する | `python measure_memory_overhead.py [--repeat 3]` | `results/Memory Latency/*.csv` |
| `prompts/router_classification/system_prompt_v3.txt`(修正) | ⑧-4:想起質問(「私の猫の名前は何ですか?」等)をFASTと判断する文章ルール・具体例を追加(汎化は限定的だったため、後述のRECALL_TRIGGERSの保険として残置) | Open WebUIの操作は不要(router.pyがC:側から都度読み込む) | なし |
| [[サポートAI作製計画/scripts/router_rules.py]](修正) | ⑧-4:`RECALL_TRIGGERS`(正規表現)を追加し、想起質問をPhi-4-miniを介さずルールベースでFASTに確定させる | 単体実行はしない(`router.py`からimportして使う部品) | なし(戻り値のみ) |
| `tests/test_router_rules.py`(修正) | ⑧-4:RECALL_TRIGGERS関連のテスト7件を追加(想起質問6パターンのFAST確定・CODE優先の回帰・誤爆防止の回帰) | `python -m pytest tests/test_router_rules.py -v` | 標準出力のみ |

### 設定ファイル

| ファイル | 役割 | 備考 |
|---|---|---|
| `rag_memory/config.yaml`(修正) | `db_path`・`embed_model`・接頭辞に加え、`max_chars`/`overlap`/`max_distance`を追加 | ⚠️ **この行は記録の誤り**(7日目②で発覚):実体は4キー(`db_path`/`embed_model`/`doc_prefix`/`query_prefix`)のみで、`max_chars`等は追加されていなかった。実際は`chunker.py`の定数・`format_context`の引数既定値にハードコードされたまま。[[サポートAI作製計画/7日目Obsidianノート取り込みとCloudflareTunnel.md|7日目]]③で実際に追い出す |

---

## ⑦ 設計のみ:肥大化対策とProfile層(実装は次回以降)

5日目⑨・⑩で挙げた課題に対する現時点の方針だけ残す(実データが溜まる前に作っても検証できないため、本日は実装しない)。

| 課題 | 方針 | 着手条件 |
|---|---|---|
| 記憶の肥大化 | ①`date`による期間フィルタで古い記憶の検索優先度を下げる → ②一定期間経過分をLLMで要約圧縮し原文を削除 | 行数が数万件を超える、または検索が体感で遅くなったら |
| 検索ノイズの増大 | 5日目⑩の二層構成(Raw層=`conversations` / Profile層=`user_profile`上書き型)を導入 | Raw層の運用が安定し、「同じことを何度も説明している」実感が出たら |
| 重複登録 | 同一`content`のハッシュを持たせて登録前に判定 | 同じ発言の再送で重複が観測されたら |

---

## ⑧ 動作確認手順(実施予定)

### ⑧-0 具体的な実施手順(`test_memory_store.py`はPowerShellで実施)

> [!note] Open WebUIの画面は使わない
> このステップは`support_ai_auto_pipe.py`とは無関係に、`chunker.py`/`memory_store.py`単体の動作を確認するものです。Open WebUIを起動する必要はなく、**PowerShellだけで完結**します。Open WebUIの画面が登場するのは⑧の4つ目の項目(「Open WebUIで`C.L.A.I.R.E. (Auto)`を選び…」)からです。

1. `test_memory_store.py`の実体が正しい場所にあるか確認する。このファイルは`config.yaml`を「自分の1階層上」から探す実装(`memory_store.py`の`CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"`)になっているため、**`D:\sapo_ai\rag_memory\scripts\test_memory_store.py`の位置に実体がないと動かない**(ファイル冒頭にもその旨の注意書きがある)。vault内(`サポートAI作製計画/scripts/rag_memory/`)はバックアップ置き場であり、実行場所ではない。

   ```powershell
   # D:側に実体があるか確認。無ければvaultバックアップからコピーする
   Test-Path D:\sapo_ai\rag_memory\scripts\test_memory_store.py
   Copy-Item "C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts\rag_memory\test_memory_store.py" `
             "D:\sapo_ai\rag_memory\scripts\test_memory_store.py" -Force
   ```
➡結果：**"D:\sapo_ai\rag_memory\scripts\test_memory_store.py"** と出力されたので問題なし。

2. PowerShellで`D:\sapo_ai\rag_memory\scripts`に移動し、そのまま実行する(仮想環境を使っている場合は先にactivateする)。

   ```powershell
   cd D:\sapo_ai\rag_memory\scripts
   python test_memory_store.py
   ```

➡結果：
```PowerShell
============================================================
1. chunker.py の確認
============================================================
[短文発言] 入力12字 → 1チャンク
  OK: 短文は分割されない
[長文発言] 入力1000字 → 4チャンク (各チャンク長: [400, 400, 360, 40])
  オーバーラップ(80字)が一致: True
  OK: 長文はオーバーラップ付きで分割される
[Markdown] 見出し3つ(うち1つは400字超) → 4チャンク
  chunk[0] (17字): ## 背景\nこれは背景の説明です。...
  chunk[1] (400字): ## 実装\nこれは実装の説明です。ああああああああああああああああああああああ...
  chunk[2] (197字): ああああああああああああああああああああああああああああああああああああああああ...
  chunk[3] (16字): ## まとめ\nこれはまとめです。...
  OK: 見出し単位で分割され、長い見出し配下だけ機械分割される

============================================================
2. memory_store.py の確認
============================================================
接続先DB: D:/sapo_ai/rag_memory/db
Embeddingモデル: kun432/cl-nagoya-ruri-base
[embed] 次元数=768 所要時間=5.408秒

[append_turn] テストデータを登録
  5件登録 / 所要時間=1.244秒(1件あたり約0.249秒)

============================================================
2-1. 意味検索(表記ゆれでもヒットするか)
============================================================
クエリ: 「休みの曜日を知りたい」(所要時間=0.149秒)
  distance=0.2016 route=TEST_MEMORY_STORE    content=毎週火曜日は定休日にしています。
  distance=0.2113 route=TEST_MEMORY_STORE    content=承知しました。火曜定休として記録しますね。
  distance=0.3027 route=TEST                 content=明日は病院の予約があるので午前中は出かける。

最上位ヒット: '毎週火曜日は定休日にしています。'
  OK: 表記が違っても意味的に近い発言がトップに来ている

============================================================
2-2. route絞り込み(CODEルート想定)
============================================================
route='TEST_MEMORY_STORE_CODE'で絞り込んだ結果: 2件
  distance=0.3408 route=TEST_MEMORY_STORE_CODE content=chunker.pyの_split_by_length関数を修正しました。
  distance=0.3565 route=TEST_MEMORY_STORE_CODE content=前に書いたchunker.pyのオーバーラップ処理を直したい。
  OK: route絞り込みが機能している

============================================================
2-3. format_context()の距離足切り
============================================================
  max_distance=0.2: 採用0件 / 空文字=True
  max_distance=0.45: 採用5件 / 空文字=False
  max_distance=1.0: 採用5件 / 空文字=False
  OK: max_distanceを厳しくするほど採用件数が減ることを確認

すべてのテストが正常に完了しました。

============================================================
3. テストデータの後片付け
============================================================
route LIKE 'TEST_MEMORY_STORE%' を削除: 9件 → 4件(削除数: 5)
※ 5日目からの既存データ(route='TEST')には触れていない
```
- [x] 3. 標準出力を上から確認する。中で`assert`が複数走っており、**途中で例外が出ずに最後まで到達すれば合格**。目安は以下(③④で実施した回と同じスクリプト)。

   - `1. chunker.pyの確認`:短文=1チャンク/長文(1000字)=4チャンクでオーバーラップ一致/Markdown見出し=4チャンク以上、それぞれ`OK:`表示が出る
   - `2. memory_store.pyの確認`:`[embed]`の次元数・所要時間、`[append_turn]`の登録件数・所要時間が表示される
   - `2-1`:クエリ「休みの曜日を知りたい」→`定休日`を含む発言がトップでヒット(`OK:`表示)
   - `2-2`:route絞り込みが機能している(`OK:`表示)
   - `2-3`:`max_distance`を厳しくするほど採用件数が減る
   - 最後に`すべてのテストが正常に完了しました。`が出れば合格。例外(`AssertionError`など)が出た場合は、`try/finally`でも`cleanup()`(手順4)は必ず走るので、まずエラーメッセージを読んで原因(Embeddingモデル未起動・`db_path`誤りなど)を特定する。
   ➡結果:1番・2番の実行結果ログ(上記)にすべての`OK:`表示および`すべてのテストが正常に完了しました。`の出力を確認。例外なく最後まで到達しているため合格。

- [x] 4. スクリプト自身が最後に後片付けを行うため、**手動でのテストデータ削除は不要**。`route LIKE 'TEST_MEMORY_STORE%'`のデータを`table.delete()`で削除し、「削除数」がログに出る。この時点で本番DBの行数は5日目からの`route='TEST'`データ(4件)のみに戻っているはず。次のチェック項目(`route = 'TEST'`の削除)は、この`TEST_MEMORY_STORE%`とは別物(5日目の残骸)なので混同しないこと。
   ➡結果:1番・2番の実行結果ログの「3. テストデータの後片付け」に`route LIKE 'TEST_MEMORY_STORE%' を削除: 9件 → 4件(削除数: 5)`と出力されており、自動後片付けが正常に完了していることを確認。

- [x] ②の所在確認が完了し、`config.yaml`の`db_path`が実在するDBを指している
- [x] `python test_memory_store.py` で 登録→検索→`format_context`整形 が通る(**PowerShellで実施。Open WebUIの画面は使わない**。手順は直下の「### ⑧-0 具体的な実施手順」参照)
- [x] 5日目のテストデータ(`route = 'TEST'`)を`table.delete("route = 'TEST'")`で削除し、本運用データと混ざらない状態にする【C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts\rag_memoryにdelete_test_data.pyを新規で作成した】➡４件削除した

### ⑧-1 OpwnWebUIでの編集
1. [x]　**Open WebUI → 管理者パネル → Workspace → Functions** を開く
2. [x]　`C.L.A.I.R.E. (Auto)` を探し、編集(鉛筆アイコン)を開く
3. [x]　**中のコードを、更新済みの`support_ai_auto_pipe.py`(⑤で記憶検索・書き戻しを追加したもの)の**全文で上書きして保存
4. [x] 保存後、Valvesの設定画面に `memory_enabled`(既定True)・`memory_top_k`(既定3)が新しく追加されていることを確認
5. [x] 念のため一度Functionを無効化→有効化し直す(反映されないことがあるため)

- [x] Open WebUIで`C.L.A.I.R.E. (Auto)`を選び、**チャットA**で個人的な事実を1つ伝える(例:「私は火曜日と木曜日、土曜日と日曜日が休みです。」)
- [x] **新しいチャットB**(別`chat_id`)で「私の休みはいつ?」と聞き、チャットAの内容を参照して答えられるか確認する ← 本日の本丸

> [!bug] 実機検証で発覚:両方ともCLARIFYに誤判定され、本丸が失敗した
> チャットAの発言もチャットBの質問もPhi-4-miniによって`CLARIFY`と判定され、(1)チャットAの発言が`APPEND_ROUTES`から漏れて記憶DBに書き込まれず、(2)チャットBの質問も検索(retrieve)が行われず、「あなたの休みの日付は私だけでは決定できません」という聞き返し文が返ってきた。
>
> **原因1(分類プロンプトの抜け漏れ)**: `system_prompt_v3.txt`のFAST/DEEP/CODEの定義はすべて「質問・依頼」を前提にしており、「休みは火曜日と木曜日です」のような**事実を一方的に伝えるだけの発言**を想定していなかった。CLARIFYの判断基準「質問の内容(何をしてほしいか)が本当に何も書かれていない場合はCLARIFY」に忠実に従った結果、事実の申告がCLARIFYに倒れていた。
>
> **原因2(設計そのものの穴)**: 「私の休みは何曜日ですか?」は本来FASTに分類されるべき単純な質問だが、仮に正しくFASTと分類されていたとしても、当時の`RETRIEVE_ROUTES = {"DEEP", "CODE"}`ではFASTはretrieveされない設計だったため、分類が直っても記憶を参照できないままだった。
>
> **対応(実施済み)**:
> 1. `system_prompt_v3.txt`に「事実を一方的に伝えるだけの発言もFASTと判断する」旨のルールと具体例(「私は火曜日と木曜日、土曜日と日曜日が休みです。」「私の休みは何曜日ですか?」→ともにFAST)を追加。
> 2. `support_ai_auto_pipe.py`の`RETRIEVE_ROUTES`に`"FAST"`を追加(`{"FAST", "DEEP", "CODE"}`)。速度最優先という当初の狙いは`memory_top_k`(既定3件)の絞り込みで担保する。
> 3. `tests/test_support_ai_auto_pipe_memory.py`の`test_fast_route_appends_but_does_not_retrieve`を`test_fast_route_retrieves_unfiltered_and_appends`に置き換え、FASTでもretrieveが呼ばれ検索結果がsystemへ差し込まれることを検証するように修正。
> 4. `python -m pytest tests/ -q` で64件全て合格を確認済み。
>
> **残作業**: プロンプト修正はユニットテストで検証できない(実際のPhi-4-miniの応答依存)ため、Open WebUI上で実際にチャットA→チャットBの流れを再実行し、CLARIFYに落ちずFASTに分類されること・チャットBが記憶を参照して答えられることを実機で再確認する必要がある。
- [x] `FAST`に分類される短い質問で、検索がスキップされている(ログに検索が出ない)ことを確認する
- [x] `CODE`ルートでACTIONブロック(ファイル作成・実行)が**従来どおり動く**ことを確認する(⑤の衝突注意点の検証)
- [x] `Valves`の`memory_enabled`をOFFにすると、5日目以前と同じ挙動に戻ることを確認する
- [x] レイテンシ実測:同一質問を`memory_enabled` ON/OFF で各3回実行し、平均応答時間を比較する
- [x] `nvidia-smi`でEmbedding実行中もVRAM使用量が跳ねない(CPU固定が効いている)ことを確認する

> [!note] 3項目とも手動(GUIクリック・ストップウォッチ・nvidia-smi目視)ではなく自動化した
> Open WebUIはまだ導入されておらず(⑤冒頭の注記のとおり)、`⑧-1`のチャットA/B確認も
> 実際には`smoke_test_pipe.py`と同じ方式で`Pipe`クラスを直接呼び出して行っていた。
> であれば「`memory_enabled`をOFFにして再現する」「ON/OFFの応答時間を比較する」
> 「nvidia-smiでVRAMを見る」は毎回手作業でやり直すより**スクリプト化した方が再現性が高く、
> 判定基準(数値)も曖昧さなく残せる**ため、以下の2本を新規作成し実行した。

#### `Valves`の`memory_enabled`をOFFにする確認:何を入れて何が出れば良いか

**入れるプロンプト**(新規`verify_memory_toggle.py`):

- チャットA:「私が飼っている亀の名前はナポレオンです。」(固有名詞を含む事実を1つ伝える)
- チャットB(別`chat_id`):「私が飼っている亀の名前は何ですか?」(想起質問)

**判定方法で気をつけたこと(試行錯誤込みで記録)**:

1. 最初は⑧-1の実機バグ調査で使った「私は火曜日と木曜日が休みです」→「私の休みは何曜日?」を
   そのまま流用したが、`memory_enabled=False`(記憶を一切見ていない)にもかかわらず`gpt-oss:20b`が
   一般的な勤務パターン例として偶然「火曜」「木曜」を生成し、単純な部分一致判定が
   誤って「想起できた」と誤判定する事故が実機で発生した。**曜日名のような「モデルが記憶なしでも
   それらしく言い当てられる」内容は判定材料に向かない**。
2. 次に「合言葉は『ペンギン38号』です」のような明示的パスワード形式を試したが、
   今度はPhi-4-mini(ルーター)がこれを「意図不明」と解釈してCLARIFYに分類してしまい、
   CLARIFYはそもそも検索・書き戻しの対象外(⑤の表)のためON/OFFの差そのものが出ず判定不能になった。
3. 最終的に、⑧-1で実績のある「私は～です」という自然な事実申告の文型は維持しつつ、
   中身だけをモデルが記憶なしに偶然言い当てる可能性が低い固有名詞(ペットの名前)に
   差し替えた。これで文型はFAST分類を維持しつつ、単語自体は偶然一致しない。

**期待される結果(=OFFが5日目以前と同じ挙動に戻ったと言える条件)**:

| 項目 | `memory_enabled=False` | `memory_enabled=True`(対照) |
|---|---|---|
| チャットAの発言後、記憶DB(`conversations`)の行数 | **変化しない**(書き戻されない) | 増える(user発言+assistant応答で2行) |
| チャットBの応答 | 「ナポレオン」を**言い当てられない**(わからない旨の返答) | 「ナポレオン」を**正しく言い当てる** |
| Pipe自体の応答 | エラーにならず何かしら返る | エラーにならず何かしら返る |

**実施結果(2026-08-06実機実行・`python verify_memory_toggle.py`)**:

```
[OFF] memory_enabled=False
  記憶DB行数: 15 -> 15 (差分 0)
  チャットB応答: 「そうなんですね!🐢 それでは『ナポレオン』さんについてもう少し聞かせて…」
  (種類・年齢などを聞き返すのみで、名前を言い当ててはいない)
  『ナポレオン』を含む(=想起できた): False

[ON] memory_enabled=True
  記憶DB行数: 15 -> 17 (差分 2)
  チャットB応答: 「あなたが飼っている亀の名前は『ナポレオン』です。」
  『ナポレオン』を含む(=想起できた): True

判定: OFF時に『書き戻しなし・想起なし』(5日目以前と同じ挙動)か: OK
     ON時に『書き戻しあり・想起あり』(6日目の狙いどおり)か  : OK
[PASS]
```

`memory_enabled=False`で書き戻し・想起の両方が完全に止まり、`True`に戻すと即座に機能することを実機で確認した。スクリプトは実行後にテストデータ(`content`一致・`source LIKE 'chat:verify-toggle-on-%'`)を自動削除するため、本番DBの行数は実行前後で15件のまま変化しない(⑤で汚染事故を起こした反省を踏まえた自己後片付け方式)。

#### レイテンシ・nvidia-smi計測結果(`measure_memory_overhead.py`)

DEEPルート(`gemma4:26b`)固定・同一プロンプトで`memory_enabled`だけを切り替えて3回ずつ実行した(ルートが違うとモデルそのものの生成時間差でON/OFFの比較にならないため固定)。

初回実行時はウォームアップなしで測ったところ`OFF: 66.0s/46.0s/33.2s`のように**1回目だけモデルロード時間が乗って大きくばらついた**(④の分析で懸念していた「アイドル明け初回」がそのまま再現)。本計測の前に1回捨て呼び(ウォームアップ)を入れて再測定したところ、以下の安定した値が得られた。

| 条件 | 1回目 | 2回目 | 3回目 | 平均 | OFF比 |
|---|---|---|---|---|---|
| `memory_enabled=False`(DEEP) | 34.49s | 32.35s | 33.41s | 33.41s | - |
| `memory_enabled=True`(DEEP・検索あり) | 32.92s | 33.59s | 31.56s | 32.69s | **-0.73s**(誤差の範囲) |

| 項目 | OFF側ピーク | ON側ピーク | 差 |
|---|---|---|---|
| VRAM使用量(nvidia-smi) | 15751 MiB | 15745 MiB | **-6 MiB** |

**判断基準(1秒以内なら許容)に対する判定: OK**。検索・書き戻しによる上乗せは実測で誤差の範囲(-0.7秒、むしろ僅かに速い誤差)であり、④で懸念されていた「Phi-4-mini呼び出し3〜15秒への上乗せ」は無視できるレベルだった。VRAMピークもON/OFFでほぼ同値(-6MiB)であり、`OLLAMA_NUM_GPU=0`によるEmbeddingのCPU固定が効いていることも確認できた(GPUを使っていれば数百MiB〜のジャンプが出るはず)。生データは`scripts/results/Memory Latency/memory_overhead_20260806_174603.csv`に保存済み。

> [!note] 旧計測案「`memory_enabled=True`(FAST・検索なし)」について
> 当初案ではFASTを「検索なし」の比較対象としていたが、⑧-1の実機検証でバグ修正した結果
> `RETRIEVE_ROUTES`に`FAST`が追加され、**現在はFASTも検索対象**になっている(⑤参照)。
> そのためこの行は前提が崩れており、削除して`CLARIFY`固定でのDEEP比較に一本化した。
> 「検索が完全にスキップされるルート」を数値で見たい場合はCLARIFYで同様に計測できるが、
> CLARIFYはPhi-4-mini自身が応答するため生成モデルが異なり、DEEPとの直接比較には向かない。

---

## ⑧-2 実機バグ:「マツコ問題」(チャットAの発言がCLARIFYに化けて記憶されない)

### 現象

外付けHDD接続・Open WebUIサーバー再起動後、新規チャット「Cat Name Discussion」で**1ターン目**として「私の猫の名前はマツコです」と入力したところ`[route: CLARIFY]`と判定され、聞き返し文が返ってきた。CLARIFYは`RETRIEVE_ROUTES`/`APPEND_ROUTES`のどちらにも含まれないため、`D:\sapo_ai\rag_memory\db\conversations.lance`には何も書き込まれなかった。

一見⑧-1で対応済みの「事実申告文がCLARIFYに倒れる」バグの再発に見えたが、以下の切り分けで**原因は別物**と判明した。

### 切り分け(systematic-debugging)

1. `router.classify_route("私の猫の名前はマツコです", call_phi4)`を単体で28回連続実行 → **全てFAST**。system_prompt_v3.txtの事実申告ルール自体は機能している。
2. `webui.db`(`open_webui/data/webui.db`)を直接調べ、Functionテーブルの`C.L.A.I.R.E. (Auto)`の保存内容(`updated_at`)とOpen WebUIプロセスの起動時刻(`Get-CimInstance Win32_Process`)を確認 → Functionコードの保存(21:35)・サーバー再起動(21:44)・実際にCLARIFYになった実チャット(21:46)の順で、**いずれも修正後の最新コードで発生していた**。「GUI側が古いコードのまま」という仮説は棄却。
3. `chat`テーブルの当該チャットの`history.messages`を直接読むと、1ターン目(最初の発言)だけで既にCLARIFYになっていたことを確認(`last_route`による「前回CLARIFYだったから継続」という汚染ではなく、**その場の1発だけで誤判定**)。
4. `ollama_client.generate()`および`router.call_phi4()`のコードを確認したところ、**Ollamaへの`/api/generate`呼び出しにtemperature等の`options`が一切指定されていなかった**(Modelfile側にも`num_gpu`しか設定なし)。分類タスクにもかかわらずOllama既定のサンプリング温度(モデル依存・通常0.8前後)で生成させていたため、**同一入力・同一プロンプトでも呼び出しごとに出力がブレる**状態だった。
5. 検証: `classify_route(..., last_route="CLARIFY")`を8回実行すると**8/8でCLARIFYに固定**される一方、`last_route=None`(今回の1ターン目相当)は28回中28回FASTだった。つまり「低確率でCLARIFYを引く」こと自体は温度ありサンプリングでは常に起こり得て、たまたま実機の1回目でそれを引いた、というのが真因。

### 根本原因

**ルーター(Phi-4-mini)の分類呼び出しに`temperature`を固定していなかったため、本来決定的であるべき分類タスクが確率的にブレていた。** system_prompt_v3.txtの事実申告ルール自体は正しく機能しているが、温度ありサンプリングである以上、低確率でCLARIFY(またはその他の誤ルート)を引く可能性が構造的に残っていた。プロンプトに例文を増やす対応(⑧-1)は誤判定の**確率を下げる**効果はあっても、**ゼロにはできない**ため、根本対応にならなかった。

### 対応(実施済み)

- `ollama_client.generate()`に`options: dict | None`引数を追加し、指定時は`/api/generate`のボディに`options`として渡すようにした。
- `router.call_phi4()`から`options={"temperature": 0}`を渡すよう修正(`CLASSIFY_OPTIONS`定数として定義)。分類タスクのみを対象とし、実際の応答生成(FAST/DEEP/CODEの本文生成、CLARIFYの聞き返し文生成)は従来どおり既定温度のまま(創造性が必要なため意図的に変更しない)。
- `python -m pytest tests/ -q` で64件全て合格(既存テストは`call_model`をフェイクで直接差し替えているため、`options`追加の影響を受けない)。
- 修正後、`classify_route("私の猫の名前はマツコです", call_phi4)`を15回連続実行し**15/15でFAST**を確認。実際に`Pipe.pipe()`をフルスタックで呼び出し、`[route: FAST]`判定・`conversations`テーブルへの書き戻し(user+assistant 2行)を実機で確認済み(検証用データは自動削除済み、本番DBは15件のまま変化なし)。

### 残課題(既知だが今回はスコープ外)

- **CLARIFYの「粘着」問題**: `last_route="CLARIFY"`を渡すと8/8でCLARIFYが維持されることを確認した。`build_user_prompt`の「話題が継続していれば直前のrouteのままにしてください」という文言が、CLARIFY(=そもそも話題不明)の場合はほぼ常に「継続」と判定されてしまうため、**一度CLARIFYを引いたチャットはそのチャット内でずっとCLARIFYに固定され続けるリスクが残っている**。今回のバグ自体は1ターン目の誤判定(temperature起因)だったため直接の原因ではないが、temperature=0化で1ターン目の誤判定確率は大幅に下がったとはいえゼロではない以上、粘着リスクは論理的に残る。対応案:CLARIFYだけは`last_route`として引き継がない(常に文脈なしで再判定させる)よう`build_user_prompt`を修正する。次回以降の課題とする。
- **Open WebUIの内部メタ呼び出し(タイトル生成・follow-up質問生成)がC.L.A.I.R.E.パイプ経由でCODEルートに誤分類され、記憶DBに雑音として書き込まれている**ことを今回の調査で発見した(`route='CODE'`で"Suggest 3-5 relevant follow-up questions..."のような英語のシステムタスク文が複数行登録されていた)。→ **⑧-3で真因判明・対応済み**(このメモ自体は誤り。「CODEに誤分類」ではなく「CLARIFYにフォールバックしてセッションを汚染する」のが実害の本体だった)。

---

## ⑧-3 「マツコ問題」再発:真因は⑧-2ではなくOpen WebUIタスクモデルのセッション汚染だった

### 現象(再発)

⑧-2の`temperature=0`修正・サーバー再起動後も、実機で「私の猫の名前はマツコです」「私の休みは火曜日と木曜日です」が**依然としてCLARIFYになる**ことを確認した。⑧-2の対応だけでは不十分だった。

### 切り分け(systematic-debugging・実機への直接計装)

ローカルCLIでは`temperature=0`後、95回連続でFASTを再現できず(バックグラウンドで40回×2文=80回含む)、原因の切り分けにローカル検証だけでは限界があると判断。`router.call_phi4()`に一時的なデバッグログ(`router_debug.log`)を仕込み、実機で再現してもらったところ、以下の実行順序が記録された(すべて同一`chat_id`):

```
22:48:01  user_text = "### Task:\nGenerate a concise title summarizing the chat history...
                        <chat_history>\nUSER: 私の猫の名前はマツコです\nASSISTANT: \n</chat_history>"
          raw_response = '{ "title": "Cat Name Discussion" }'
          parsed_route = CLARIFY   ← "route"キーが無いJSONなのでparse_route_responseがフォールバック

22:48:08  user_text = '[会話の文脈] このスレッドはここまで "CLARIFY" として扱われています。
                        今回の発言が同じ話題の続きであれば "CLARIFY" のままにしてください。...
                        質問: 私の猫の名前はマツコです'
          raw_response = '{"route": "CLARIFY"}'
          parsed_route = CLARIFY   ← 直前のダミーCLARIFYを"継続"と誤認して追従
```

`open_webui`パッケージ(`open_webui/routers/tasks.py`)を直接調べたところ、Open WebUIはタイトル生成・タグ生成・フォローアップ生成などの内部ユーティリティ呼び出し時、`__metadata__`に以下を設定してモデル(=Pipe)を呼んでいることを確認した。

```python
'metadata': {
    'task': str(TASKS.TITLE_GENERATION),   # 'title_generation' 等
    'task_body': form_data,
    'chat_id': form_data.get('chat_id', None),   # 本物の会話と同一のchat_id
}
```

Open WebUIの「タスクモデル」設定が既定の「現在のモデル」のままだったため、**C.L.A.I.R.E. (Auto)が選択されているチャットでは、Open WebUI自身のタイトル生成等まで同じchat_idでこのPipeを経由していた**。

### 根本原因

`Pipe.pipe()`にはOpen WebUIの内部タスク呼び出しと本物のユーザー発言を区別するガードが無かった。そのため:

1. タイトル生成タスク文(`### Task: Generate a concise title...`)が本物の発言と同じ`chat_id`で`session.get_route()`に渡る
2. タスク文への応答はJSON形式が`{"title": ...}`であり`"route"`キーを持たないため、`parse_route_response()`が`DEFAULT_FALLBACK_ROUTE`(`CLARIFY`)を返す
3. `RouterSession.get_route()`はこの**タスク由来の偽routeを無条件に`_last_route[chat_id] = "CLARIFY"`として記録する**
4. 直後に届く本物の発言が、この汚染された`last_route="CLARIFY"`を文脈として引き継ぎ、⑧-2で確認済みの「CLARIFY粘着」挙動(8/8再現)によりCLARIFYへ追従してしまう

⑧-2の`temperature=0`修正自体は正しく機能しており(分類のブレという別の問題は解消済み)、それとは独立に**セッション汚染という別経路**でCLARIFYへ落ちていたため、⑧-2だけでは直らなかった。

### 対応(実施済み)

- `support_ai_auto_pipe.py`の`Pipe.pipe()`冒頭に、`__metadata__.get("task")`が設定されている場合(=Open WebUI内部のタスク呼び出し)は**分類・`RouterSession`・記憶レイヤーを一切経由せず**、軽量モデル(`router.ROUTER_MODEL`)に直接プロンプトを渡して素直に返すガードを追加した。
- 再発防止テスト`tests/test_support_ai_auto_pipe.py::test_task_call_bypasses_routing_and_does_not_poison_session`を追加。「タイトル生成タスク → 本物の発言」という実機で起きた順序をそのまま再現し、(a) タスク呼び出しが`router.call_phi4`・`pipe._sessions`に一切触れないこと、(b) 直後の本物の発言が汚染されずFASTに分類されることを検証。`python -m pytest tests/ -q`で65件全て合格。
- 実機同等の順序(タイトル生成タスク→本物の発言、同一`chat_id`)をフルスタック(実際のOllama・実際の記憶DB)で再現し、タスク呼び出し後も`pipe._sessions`が空のまま・本物の発言が`[route: FAST]`で分類され`conversations`テーブルへ正しく書き戻されることを確認済み(検証用データは自動クリーンアップ済み、本番DBは15件のまま変化なし)。
- 調査用に追加した`router.py`内の一時デバッグログ計装は原因特定後に削除済み。

### 教訓

- 「ローカルCLIでは再現しない」ことを「バグが直った証拠」と早合点しなかったのが功を奏した。実機とローカル検証の間に環境差がある場合、**推測で2手目3手目の修正を重ねるのではなく、実際の呼び出しをその場でログに落として見る**(systematic-debuggingの「多コンポーネント境界での証拠収集」)のが最短だった。
- Open WebUIのPipe/Function機構は、**ユーザーが直接送った発言だけでなく、UIの裏で動く補助的なLLM呼び出し(タイトル・タグ・フォローアップ・自動補完等)も同じインターフェースに流れてくる**。カスタムPipeを作る際は`__metadata__["task"]`の有無で「本物の会話ターンかどうか」を必ず判別する必要がある。

---

## ⑧-4 「マツコ問題」再々発:想起質問がプロンプトの文章ルールでは救えなかった問題

### 現象(再々発)

⑧-3の対応(タスク呼び出しガード)後、実機で改めて「私の猫の名前はマツコです」→(別チャットで)「私の猫の名前は何ですか?」を試したところ、**チャットBが再びCLARIFYになり**、以下のような要領を得ない聞き返し文が返ってきた。

> この特定なリクエストにはAIとして、この場合のあなた自身が持っていない個人的な情報へのアクセス権限があります。(中略)その他人やデータへのアクセス権限やプライバシーへの配慮など、一方的な回答には慎重にアプローチする必要があります。

### 切り分け(systematic-debugging)

1. まず「保存自体ができているか」を`conversations`テーブルへの直接クエリで確認。**チャットAの発言(`私の猫の名前はマツコです`)は`route=FAST`で正しくuser/assistant両方が保存済み**であることを確認(⑧-2/⑧-3の対応が保存側では機能していたことの裏付け)。一方でDB全体を見てもチャットBの質問自体の痕跡が一切無く、**質問側がCLARIFYに分類されてappend/retrieve自体が行われていない**ことが分かった。
2. `router.classify_route("私の猫の名前は何ですか？", call_phi4, last_route=None)`を**前回文脈なし・temperature=0**で5回実行 → **5/5でCLARIFY**。⑧-2で対応した「ランダムなブレ」でも、⑨に残っていた「CLARIFY粘着(last_route由来)」でもなく、**この質問単体が確定的にCLARIFYと判定される**ことが判明。
3. `router.py`の`ROUTER_SCRIPTS_DIR`を確認し、`router.py`関連(`prompts/`含む)はDドライブではなく**Cドライブ(vault)側`サポートAI作製計画/scripts`から常に読み込まれる**設計であることも合わせて確認(D側にコピーが必要なのは`memory_store.py`等の記憶DB関連のみ)。GUI側の反映漏れ(⑧-2で疑って棄却したのと同じ仮説)は今回も原因ではなかった。

### 根本原因:プロンプトの文章ルールは小型モデル(Phi-4-mini)に汎化しない

`system_prompt_v3.txt`のFASTの判断ヒントには「休みは何曜日ですか?」という**特定パターンの想起質問の例しか無く**、「私の猫の名前は何ですか?」のような一般的な想起質問には対応していなかった。

最初の対応として、FASTのヒントに「ユーザー自身についての想起質問(名前・予定など)もFASTと判断する」という**文章による汎用ルール**と、「私の猫の名前は何ですか?」の具体例を追加した。これは効果があり、この具体例と完全一致する質問はFASTになったが、**汎化には失敗した**ことが以下の追加検証で判明した:

| 質問 | 結果(temperature=0・3回) | 備考 |
|---|---|---|
| 私の猫の名前は何ですか?(既存の具体例と完全一致) | FAST×3 | ○ |
| 私の亀の名前は何ですか?(動物名を変えただけ) | CLARIFY×3 | ✗ 汎化せず |
| 昨日やった残りの課題を教えて(**新たに追加した具体例と完全一致**) | CLARIFY×3 | ✗ 例文自体が守られない |
| さっき話してたやつどうなった? | CLARIFY×3 | ✗ |
| 前に言ってたパスワードなんだっけ? | CLARIFY×3 | ✗ |
| この前決めたチャンク分割の上限値って何字だっけ? | CLARIFY×3 | ✗ |

**新しく追加した具体例そのものがCLARIFYと判定される**という結果から、Phi-4-mini(小型モデル)は文章で書かれた抽象的な条件ルールを十分に遵守できず、プロンプト末尾の「50%以上の確信が持てない場合は必ずCLARIFYを選ぶこと」という保守的デフォルトに引っ張られていると判断した。プロンプトへの文章追記(⑧-1で有効だった対策)は、今回のような**表現のバリエーションが多い想起質問カテゴリ**には効果が乏しく、根本対応にならなかった。

### 対応(実施済み・ルールベース事前判定への切り替え)

文章によるプロンプト改善では小型モデルの汎化能力の限界に当たるため、既存の`CODE_TRIGGERS`(4日目導入)と同じ**LLMを介さない正規表現による事前確定方式**を想起質問にも適用した。

- [[サポートAI作製計画/scripts/router_rules.py]](修正)に`RECALL_TRIGGERS`を追加:
  - `だっけ` / `でしたっけ`(日本語口語で想起質問にほぼ限定される言い回し)
  - `(私|僕|自分)の.{1,20}(何|いつ|どこ|誰)(です|でした)?(か|っけ)`(「私の〇〇は何ですか」型)
  - `(昨日|一昨日|先週|さっき|前に|この前|以前).{0,20}(教えて|どうな(った|ってる)|覚えてる)`(過去参照+想起動詞型)
  - `match_rule_based()`の優先順位を「CODE_TRIGGERS一致→CODE」「RECALL_TRIGGERS一致→FAST」「どちらも不一致→Phi-4-miniへフォールバック」に拡張。CODEを先にチェックすることで、「前に書いたコードのバグ直して」のような想起+コード依頼の複合表現は従来通りCODE優先(4日目の優先度ルール)を維持する。
- `system_prompt_v3.txt`の文章ルール・具体例(「私の猫の名前は何ですか?」等)は**残したまま**にした。RECALL_TRIGGERSに一致しない未知の言い回しに対する保険としては無害なため。ただし今後同種の誤判定パターンが見つかった場合は、プロンプト文章の追記ではなく**まずRECALL_TRIGGERSへの正規表現追加を優先する**方針とする。
- `tests/test_router_rules.py`に想起質問系のテスト7件を追加(⑧-2で問題になった6パターン全てのFAST確定、CODE優先の複合表現の回帰、「教えて」単体では誤爆しないことの回帰)。`python -m pytest tests/ -q`で**72件全て合格**。
- `router.classify_route()`にPhi-4-mini呼び出しを検知したら例外を出す偽関数を差し込み、上記6パターンの想起質問が**Phi-4-miniを一切呼ばずルールベースだけでFASTに確定する**ことを確認(副次効果としてルーター呼び出し分のレイテンシも短縮される)。曖昧な既存CLARIFY例(「あれ、どうすればいい?」)は引き続きLLM経由でCLARIFYと判定され、既存挙動を壊していないことも確認済み。

### 結果:実機で最終確認

対応後、Open WebUIで改めて「私の猫の名前は何ですか?」を聞き直したところ、以下の応答が返り、**本丸(チャットAで伝えた情報を、別チャットBから思い出して答える)が成立した**。

```
[route: FAST]
あなたの猫の名前は **マツコ**（Matsuko）です。
```

### 教訓

- 「プロンプトに例文を追加する」対応(⑧-1)は万能ではない。今回のように**表現のバリエーションが本質的に多いカテゴリ(想起質問)**では、例文を1つ追加してもその例文自体に完全一致する入力にしか効かず、汎化を期待できない。小型ルーターモデルを使う設計では、**曖昧さが許されない判定は最初から正規表現の事前フィルタに寄せる**方が結果的に安定する。
- 「保存はできているのに参照できない」という症状が出た場合、まず疑うべきは検索(retrieve)側の不具合ではなく、**質問側の分類自体が想定外のrouteに落ちていないか**(retrieve/appendの対象外routeに落ちていれば、症状としては「記憶が無視された」ように見える)。DBを直接クエリして「そもそも質問側の発言がDBに痕跡を残しているか」を見るのが、原因切り分けの最短ルートだった。

---

## ⑨ 残課題・次回への持ち越し(記入用)

- ~~外付けHDD接続後の`config.yaml`パス差し替え~~ → **完了**(7日目②で棚卸し):`db_path`は既に`"D:/sapo_ai/rag_memory/db"`を指しており、⑧-1〜⑧-4の実機検証はすべてこのパスで成功している。5日目から機械的に引き継いだままリストに残っていただけだった。残る「クロスOS動作確認」は、`db_path`のWindows固有表記(`D:/`)をやめる別課題として[[サポートAI作製計画/7日目Obsidianノート取り込みとCloudflareTunnel.md|7日目]]⑨へ書き換えた
- Ruri v2 → v3-310m 差し替え検証(768次元で同一。接頭辞`検索文書: `/`検索クエリ: `への変更を忘れないこと)
- Profile層(`user_profile`テーブル)の実装
- 記憶の肥大化対策(要約圧縮・削除ポリシー)の実装
- Obsidianノート自体の取り込み(`ingest/`経由・`chunk_markdown`使用)。本日は会話ログのみを対象とした
- `chunk_markdown`のフォールバック機械分割時に見出し行が失われる問題への対応(③改善策。ノート取り込み着手時に対応)
- `format_context`の`max_distance=0.45`の妥当性検証(④分析。明確に無関係な発言を混ぜたテストケースが未実施)
- ⑧のレイテンシ計測で「Embeddingモデル常駐中」と「アイドル明け初回(モデルロード込み)」を分けて記録する(④分析。`OLLAMA_KEEP_ALIVE`延長の要否判断に必要)
- CLARIFYの「粘着」対策:`build_user_prompt`が`last_route="CLARIFY"`を渡すとPhi-4-miniがCLARIFYを維持し続ける(⑧-2で発見)。⑧-3の対応でタスク呼び出しによる汚染経路は塞いだが、「本物の発言だけでCLARIFYを引いた場合」の粘着リスクは論理的にまだ残っている。CLARIFYだけは`last_route`として引き継がない実装への修正を検討する
- Open WebUIの「タスクモデル」設定を専用軽量モデルへ分離する(パフォーマンス最適化):⑧-3のコード側ガードで実害(セッション汚染・記憶DB汚染)は解消済みだが、タイトル生成等のたびに`Pipe.pipe()`を経由すること自体は変わらない。Open WebUI管理画面で「タスクモデル」を明示的にphi4-mini等へ固定すれば、そもそもC.L.A.I.R.E.を経由しなくなり、よりシンプルになる
- **RECALL_TRIGGERSの網羅性には限界がある**(⑧-4で追加):`だっけ/でしたっけ`・`私の/僕の/自分のX は何/いつ/どこ/誰`・`昨日/前に/この前/さっき/以前+教えて等`のいずれにも当てはまらない言い回しの想起質問は、依然としてPhi-4-mini任せになりCLARIFYへ誤判定されるリスクが残る。実運用で同種の誤判定を見つけるたびに、プロンプト文章の追記ではなく`router_rules.py`への正規表現追加で対応する方針とする
- (実行後に追記)

### ⑨-1 上記3課題の優先度づけ

| 優先度 | 課題 | 着手タイミング | 理由 |
|---|---|---|---|
| **P1(⑧実施と同時)** | レイテンシ計測分割 | ⑧の「レイテンシ実測」チェック項目を実施するそのとき | 独立した将来課題ではなく、⑧に元々ある計測項目の**測り方の修正**そのもの。分けずに計測すると④の分析どおり数値が誤って読めてしまい、⑧の判断基準(上乗せ1秒以内か)を誤判定する。コストもほぼゼロなので後回しにする理由がない。 |
| **P2(実運用を数日〜1週間回してから)** | 閾値検証(`max_distance=0.45`) | ⑧のE2E確認が通り、`conversations`テーブルに実データが溜まり始めてから | 今すぐ人工的なテストケースを足すことはできるが、`test_memory_store.py`のサンプルのように意図的に作った無関係発言では実際のノイズ傾向を再現しにくい。実運用ログで初めて「無関係な記憶が紛れ込んでいるか」を判断できるため、実データが溜まってからが合理的。 |
| **P3(着手条件が揃うまで凍結)** | 見出し欠落対応(`chunk_markdown`) | Obsidianノート取り込み(`ingest/`)機能に着手する日 | この不具合は`chunk_markdown`(ノート取り込み用)にしか影響せず、ノート取り込み機能自体がまだ実装に着手していない。検証対象(実際に壊れたチャンク)が存在しないうちに直しても意味がなく、着手条件が揃うまで待つのが妥当。 |

---

## 📌 次のステップ

1. ~~4モデルをOllamaで実際にダウンロード・動作検証~~
2. ~~Phi-4-miniの振り分けロジック(プロンプト設計・実装)~~
3. ~~RAGの記憶DB構築(土台)~~ → [[サポートAI作製計画/5日目RAG記憶DB構築.md|5日目]]
4. C.L.A.I.R.E. (Auto) Pipeへの記憶レイヤー組み込み(検索+書き戻し) ← 今回
5. Cloudflare Tunnelのセットアップ
6. STT/TTSパイプラインの組み立て
