---
project: C.L.A.I.R.E.(さぽーとAI)
date: 2026-08-05
tags:
  - RAG
  - ベクトルDB
  - embedding
  - ローカルLLM
  - 作業ログ
status: 完了
---

[[サポートAI作製計画/4日目Phi4ロジック設計.md|4日目]]でPhi-4-miniの振り分けロジック(4分類)・Open WebUIのPipe連携(`C.L.A.I.R.E. (Auto)`)・CODEルートのファイル作成/実行機能まで完成し、[[サポートAI作製計画/1日目設計とモデル選定.md|1日目]]の次ステップ「3. RAGの記憶DB構築」に到達した。今回は**長期記憶(RAG)レイヤーの方式決定と、その土台(ベクトルDB・Embeddingモデル・保存先)の構築**を行う。

> [!note] このノートの成り立ち
> 本ノートの初版はクラウドのClaudeが作成した「構想メモ」だった。今回それを**実機の実測値で検証し直し、残す/修正する/削除するを判断した上で**作業ログの体裁(→[[サポートAI作製計画/ノート作成規則.md]])に書き直している。何をどう判断したかは「⑦ 初版メモの精査結果」に一覧化した。

---

## ① 本日のゴールと作業内容

### 背景/目的

4日目までで「どのモデルに振り分けるか」は完成したが、C.L.A.I.R.E.は**会話が終わると過去の経緯を一切覚えていない**。1日目の設計表(②必要な要素)にある「RAG(記憶DB) = 過去の会話・個人文脈の蓄積検索」を実装し、パーソナライズの土台を作る。

### 作業内容

- [ ] 事前調査:現状のPC環境(ドライブ構成・導入済みパッケージ・Open WebUIの対応DB)を実測する
- [ ] ベクトルDBエンジンを決定する(LanceDB / ChromaDB)
- [ ] **RAGの組み込み位置**(Open WebUI標準RAG or 自作Pipe内)を決定する ← 初版メモに無かった最重要論点
- [ ] 日本語対応・非中国系のEmbeddingモデルを選定し導入する
- [ ] 記憶DBのディレクトリ構成と、パス設定の外部化(`config.yaml`)を確定する
- [ ] 環境構築(`lancedb`等のインストール)
- [ ] `init_db.py`でDBを初期化し、1件登録→類似検索まで通す
- [ ] Windows/Linux共有(外付けHDD)を見据えた設計になっているか確認する

### 完了条件(本日分)

- [ ] `conversations`テーブルが作成され、サンプル1件のEmbedding登録→類似検索が実際に動作する
- [ ] Embeddingモデルの**実際の出力次元をコマンドで確認**し、スキーマと一致している
- [ ] DBのパスがコード直書きではなく設定ファイル経由になっており、外付けHDDへ移行する際にコード修正が不要

---

## ② 事前調査:現状環境の実測(2026-08-05)

初版メモは「外付けHDD上に構築する」「Open WebUIはChromaDB/pgvectorが中心」といった**未確認の前提**の上に書かれていたため、まず実機を確認した。

```powershell
# 1. ドライブ構成
Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free,Root

# 2. Ollamaの導入済みモデル
ollama list

# 3. Python側の導入状況
python -c "import importlib.util as u; print('lancedb:', u.find_spec('lancedb') is not None); print('ollama(py):', u.find_spec('ollama') is not None); print('sentence_transformers:', u.find_spec('sentence_transformers') is not None); print('chromadb:', u.find_spec('chromadb') is not None)"
pip show open-webui | Select-String "^Name|^Version"

# 4. Open WebUIが対応しているベクトルDBの一覧(実ファイルを直接確認)
Get-ChildItem "C:\Users\gakuh\AppData\Local\Programs\Python\Python312\Lib\site-packages\open_webui\retrieval\vector\dbs" | Select-Object Name
```

### 結果

| 調査項目                | 実測結果                                                                                                                                                                                                                                                           | 初版メモとの差異                                                                 |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| ドライブ構成              | **`C:`のみ**(空き約487GB)。外付けHDDは未接続                                                                                                                                                                                                                                | 初版は`D:\sapo_ai\...`を前提に書かれていた(未接続なので現時点では実行不能)                           |
| Ollamaモデル           | `phi4-mini-cpu` / `phi4-mini` / `gpt-oss:20b` / `gemma4:26b` / `devstral-small-2:24b`。**Embeddingモデルは未導入**                                                                                                                                                     | 差異なし(これから`pull`する)                                                       |
| Python              | 3.12.8。`lancedb`=**未導入**、`ollama`(Pythonパッケージ)=**未導入**、`chromadb`=導入済(1.5.9)、`sentence_transformers`=導入済                                                                                                                                                       | `chromadb`/`sentence_transformers`はOpen WebUIの依存として**既に入っていた**(初版メモは未把握) |
| Open WebUI          | 0.11.0 導入済                                                                                                                                                                                                                                                     | 4日目⑧で導入済み。初版メモの「Open WebUI標準はChromaDB/pgvector等が中心」は**方向性としては正しかった**     |
| Open WebUIの対応ベクトルDB | `chroma` / `elasticsearch` / `mariadb_vector` / `milvus` / `opengauss` / `opensearch` / `oracle23ai` / `pgvector` / `pinecone` / `qdrant` / `s3vector` / `valkey` / `weaviate` の13種。**`lancedb`は存在しない**(既定は`VECTOR_DB=chroma`、保存先は`open_webui/data/vector_db`) | 初版メモの「要調査」を**確定情報に更新**。LanceDBはOpen WebUI標準RAGでは使えないことが確定                |

### 分析

最大の発見は「**Open WebUI 0.11.0はLanceDBを一切サポートしていない**」こと。初版メモはこれを「要調査」として末尾に置いたまま、本文ではLanceDB採用を確定させていたため、**放置すると「DBは出来たがC.L.A.I.R.E.から使えない」という手戻りになる危険があった**。この一点で設計判断の順序が変わるので、③でDBエンジンより先に「RAGをどこに組み込むか」を決める。

---

## ③ 設計判断1:RAGの組み込み位置(初版メモに無かった論点)

### 選択肢と比較

| 方式 | 実装量 | 使えるDB | 会話の自動記憶 | 検索タイミングの制御 |
|---|---|---|---|---|
| A. Open WebUI標準RAG(Knowledge機能) | ほぼゼロ(GUI設定のみ) | Chroma等13種、**LanceDB不可** | ×(ユーザーが手動でファイルをアップロードする「資料検索」であり、会話の蓄積ではない) | ×(Open WebUI任せ) |
| B. **自作Pipe内でRAG**([[サポートAI作製計画/scripts/openwebui_pipe/support_ai_auto_pipe.py]]から直接検索) | 中(検索・書き戻しコードを自作) | **何でも可(LanceDB可)** | ○(応答のたびにPipe側で書き戻せる) | ○(routeごとに検索の有無・件数を変えられる) |
| C. 両方併用 | 大 | - | ○ | ○ |

### 結論:B(自作Pipe内RAG)を採用

理由:

1. 今回作りたいのは「アップロードした資料を検索する機能」ではなく、**1日目設計④の図にある「RAG記憶DBに書き戻し」= 会話そのものの自動蓄積**であり、Aは要件を満たさない。
2. Bなら検索・登録を自前コードで行うため、**Open WebUIが対応するベクトルDBの制約を受けない**(=LanceDBを使ってよい)。②で見つかった非対応問題は、方式Bを選んだ時点で問題ではなくなる。
3. Phi-4-miniのroute判定結果を検索条件に使える(例:`CODE`のときはコード関連の記憶だけ引く)。これはAでは不可能で、4日目までに作ったルーター資産をそのまま活かせる。

> [!warning] Aを完全に捨てるわけではない
> 「論文PDFを読ませて要約させる」用途はOpen WebUI標準RAG(A)の方が圧倒的に楽なので、**資料検索=A、会話の長期記憶=B**という住み分けは将来的にありうる。ただし本日はスコープをBに絞る(同時に2系統を作ると切り分けが困難になるため)。

---

## ④ 設計判断2:ベクトルDBエンジン → LanceDB(初版の判断を維持)

③でOpen WebUIの対応表に縛られなくなったため、純粋に要件で比較し直した結果、**初版メモのLanceDB採用は妥当**と判断し維持する。

| 観点 | ChromaDB | LanceDB |
|---|---|---|
| アーキテクチャ | 埋め込み型だが内部はSQLite+インデックスのメモリ展開 | 完全埋め込み型・サーバプロセスなし、Rust実装 |
| 保存形式 | SQLite+バイナリインデックス | 独自のLance列指向フォーマット(`.lance`ディレクトリ) |
| クロスOS運用 | ファイルロックまわりでやや不安あり | プロセス常駐なしのファイルベースで、Win/Linuxから同一ディレクトリを開くだけで動く |
| バージョニング | なし | 書き込みごとに自動バージョン管理(タイムトラベル・ロールバック可) |
| 大規模化した場合 | 基本はメモリ展開前提 | ディスクベースIVF-PQインデックス対応、RAMに乗り切らないサイズでも動作 |
| ハイブリッド検索(全文+ベクトル) | 弱い | Tantivyベースの全文検索を統合、SQLフィルタも可 |
| 現在の導入状況 | **導入済**(Open WebUIの依存) | 未導入(`pip install`が必要) |

**採用理由**:外付けHDD(exFAT)上でWindows/Linuxデュアルブートから同じ記憶DBを読み書きするという要件に対し、サーバプロセスを持たず「ディレクトリ1個で完結する」LanceDBの設計が最も相性が良い。加えて、会話ログは日付・route・ソースでの絞り込み検索(SQLフィルタ)や全文検索と組み合わせたくなる可能性が高く、その点でもLanceDBが有利。

**採用のリスクと受け止め**:`chromadb`は既にPCに入っており追加インストール不要という利点があるが、それは「Open WebUIが使っているDB」であって、そこに自前の記憶を相乗りさせると**Open WebUIのバージョンアップやデータ削除の巻き添えを食うリスク**がある。むしろ分離しておく方が安全なので、既に入っていることは採用理由にしない。

---

## ⑤ 設計判断3:Embeddingモデルの選定

### 候補比較(2026-08-05時点で再確認)

| モデル | 提供元 | 起源 | ライセンス | 入手経路 | 次元 |
|---|---|---|---|---|---|
| **Ruri v2**(base/large) | 名古屋大学 cl-nagoyaラボ | 🇯🇵 日本 | Apache 2.0 | **Ollama公式検索にあり**(`kun432/cl-nagoya-ruri-base` 111M / `kun432/cl-nagoya-ruri-large` 337M) | base=768 / large=1024 |
| **Ruri v3**(30m/70m/130m/310m) | 同上(ModernBERT-Ja基盤) | 🇯🇵 日本 | Apache 2.0 | **Ollamaには無い**。HuggingFaceから`sentence-transformers`経由 | 30m=256 / 70m=384 / 130m=512 / 310m=768 |
| multilingual-e5-large/small | Microsoft | 🇺🇸 | MIT | Ollama対応(`qllama/multilingual-e5-small`等) | small=384 / large=1024 |
| granite-embedding-107m-multilingual | IBM | 🇺🇸 | Apache 2.0 | 要HuggingFace経由 | 384 |

JMTEB(日本語の埋め込みベンチマーク)ではRuri v3が最上位クラスで、30M級の軽量モデルでもmultilingual-e5-large相当以上という報告がある。

### 結論:まずRuri v2 base(Ollama)、v3は次段階

- ③でRAGを**自作Pipe内**に置くと決めたため、埋め込み処理も自作コードから呼ぶ。既に`router.py`がOllama APIを叩いている以上、**同じOllama経由で完結するRuri v2 baseが最も接続コストが低い**。
- Ruri v3は`sentence-transformers`(こちらも既に導入済)から直接使えるので、初版メモの「**v3を使うにはGGUF変換が必要**」という記述は**誤り寄り**(GGUF変換はあくまでOllamaで動かしたい場合の話)。ただしその場合はEmbedding処理だけOllamaの外に出ることになり、依存(torch等)とロード時間が増えるため、**まずv2で通し、精度が不満ならv3-310m(同じ768次元なのでスキーマ変更なしで差し替え可能)へ**という二段構えにする。
- **予備候補**:multilingual-e5-small(切り分け用。Ruri側が原因かを判断したいときに使う)

> [!important] Ruriの接頭辞はバージョンで違う(実装時の事故ポイント)
> Ruriは文章とクエリで接頭辞を変える必要があるが、**v2とv3で文字列が異なる**。
> - **v2**: 文書 → `文章: ` / クエリ → `クエリ: `
> - **v3**: 文書 → `検索文書: ` / クエリ → `検索クエリ: `(他に`トピック: `、接頭辞なし)
>
> 初版メモのコードは`文章: `固定だった。v2ではこれで正しいが、**v3に差し替える際に接頭辞を直し忘れると精度が静かに劣化する**(エラーにならないので気づきにくい)。そのため実装では接頭辞をモデルごとの定数として`config.yaml`側に持たせる。

---

## ⑥ 実装計画

### 6-1. ディレクトリ構成(外付けHDD前提・パスは設定で切替)

最終形は1日目設計どおり外付けHDD(exFAT)上に置く。ただし②のとおり**現時点でHDDは未接続**なので、パスは`config.yaml`の1行で切り替えられるようにし、HDD接続後にコード修正なしで移行できる形にする。

```
(最終形) <RAG_ROOT>/            例: D:\sapo_ai\rag_memory\  ← HDD接続後
(暫定)   <RAG_ROOT>/            例: サポートAI作製計画\scripts\rag_memory\  ← 本日はこちらで動作確認
└── rag_memory/
    ├── db/                  # LanceDBの実体(.lanceディレクトリ群)
    │   └── conversations.lance/
    ├── ingest/              # Obsidianノート等を取り込む一時領域
    ├── scripts/             # 構築・更新用Pythonスクリプト
    └── config.yaml          # DBパス・embeddingモデル名・接頭辞・チャンクサイズ等
```

- Obsidian vaultはWindows側ローカルに置いたままとし、`ingest/`へコピーして取り込む(vault本体を直接DB化せず、Obsidian側の編集とRAG側のインデックスを疎結合に保つため)。
- exFATはファイルパーミッション・シンボリックリンクを持たないため、パスは**小文字・スペース無し・相対参照**で統一する。
- ⚠️ exFAT特有の注意として、**Win/Linuxから同時にマウントして同時書き込みしない**(ジャーナルが無く破損しやすい)。運用上は「片方のOSでのみ書き込む」ルールとする。

### 6-2. 環境構築

```powershell
cd "C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts"
pip install lancedb ollama pyarrow pandas pyyaml
```

- `chromadb`と`sentence_transformers`は導入済みのため対象外(②参照)。
- Open WebUIと同じPython環境に入れる点に注意。C.L.A.I.R.E.のPipeは`open-webui serve`のプロセス内で`exec`されるため(4日目⑧の`ModuleNotFoundError`の教訓)、**venvを分けるとPipeからimportできなくなる**。今回はvenvを作らずグローバル環境に入れる。
- Linux側でも同じ構成を再現できるよう`rag_memory/scripts/requirements.txt`を用意する。

###  `config.yaml` を作成する

場所: `D:\sapo_ai\rag_memory\config.yaml`

db_path: "D:/sapo_ai/rag_memory/db"
embed_model: "kun432/cl-nagoya-ruri-base"
doc_prefix: "文章: "
query_prefix: "クエリ: "


### 6-3. Embeddingモデルの取得

```powershell
ollama pull kun432/cl-nagoya-ruri-base
ollama list

# 実際の出力次元を確認(スキーマ確定前に必ず実施)
python -c "import ollama; r=ollama.embed(model='kun432/cl-nagoya-ruri-base', input='文章: テスト'); print(len(r['embeddings'][0]))"
```

### 6-4. スキーマ設計

| カラム | 型 | 用途 |
|---|---|---|
| `id` | string | 一意識別子(UUID) |
| `date` | string | 会話/ノートの日付(ISO8601)。時系列フィルタ用 |
| `source` | string | 由来(Obsidianノートパス / `chat:<chat_id>`)。トレーサビリティ確保 |
| `role` | string | `user` / `assistant`(会話ログの場合) |
| `route` | string | そのターンのroute(FAST/DEEP/CODE/CLARIFY)。検索時の絞り込みに使う |
| `topic` | string | タグ的な絞り込みキー |
| `content` | string | 元テキスト(チャンク済み) |
| `vector` | list<float32>[N] | Embeddingベクトル本体(Nは6-3で実測した値) |

初版メモのスキーマに`role`と`route`を追加した。理由は③で決めた「routeを検索条件に使う」方針と、会話ログでは発言者の区別が必須なため。

### 6-5. 初期化スクリプト(雛形)

**`rag_memory/scripts/init_db.py`**(新規・本日作成予定)

```python
import lancedb
import ollama
import pyarrow as pa
import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

DB_PATH = CFG["db_path"]                 # HDD接続後はここを書き換えるだけで移行できる
EMBED_MODEL = CFG["embed_model"]         # 例: kun432/cl-nagoya-ruri-base
DOC_PREFIX = CFG["doc_prefix"]           # v2: "文章: " / v3: "検索文書: "
QUERY_PREFIX = CFG["query_prefix"]       # v2: "クエリ: " / v3: "検索クエリ: "


def embed(text: str, is_query: bool = False) -> list[float]:
    prefix = QUERY_PREFIX if is_query else DOC_PREFIX
    resp = ollama.embed(model=EMBED_MODEL, input=f"{prefix}{text}")
    return resp["embeddings"][0]


def main():
    dim = len(embed("次元確認用のテキスト"))   # ハードコードせず実測値でスキーマを作る
    print(f"embedding dim = {dim}")

    db = lancedb.connect(DB_PATH)
    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("date", pa.string()),
        pa.field("source", pa.string()),
        pa.field("role", pa.string()),
        pa.field("route", pa.string()),
        pa.field("topic", pa.string()),
        pa.field("content", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
    ])

    if "conversations" not in db.table_names():
        db.create_table("conversations", schema=schema)
        print("テーブル作成完了")
    else:
        print("既存テーブルを使用")


if __name__ == "__main__":
    main()
```

初版メモからの主な変更:
- DBパスのハードコード(`D:\sapo_ai\...`)を廃止し`config.yaml`から読む(HDD未接続でも動かせる/移行時にコード修正不要)。
- ベクトル次元の`768`決め打ちを廃止し、**実際にembedして得た次元でスキーマを作る**(初版は「要確認(TODO)」のまま768と書かれており、モデルを変えた瞬間に壊れる書き方だった)。
- 接頭辞を定数化し、クエリ側/文書側を引数で切り替えられるようにした(⑤の事故ポイント対策)。

---

## ⑦ 初版メモの精査結果(残す/修正/削除の判断)

| 初版メモの記載 | 判断 | 理由 |
|---|---|---|
| LanceDB採用と比較表 | **残す(表現を修正)** | 判断自体は妥当。ただし「ChromaDBはParquet永続化」等の不正確な記述を修正し、③の組み込み位置の議論を前段に追加 |
| Embedding候補比較表 | **残す(情報を追加)** | 候補選定は妥当。次元数・入手経路を実測/一次情報で補強 |
| 「Ruri v3はGGUF変換 or sentence-transformersが必要」 | **修正** | `sentence-transformers`は既に導入済みで、Open WebUIも`RAG_EMBEDDING_MODEL=cl-nagoya/ruri-v3-310m`の形で直接扱える。「GGUF変換が必要」は**Ollamaで動かす場合限定の話**であり、そのまま読むと過大な障壁に見える |
| Ruriの接頭辞`文章: `固定 | **修正(注意喚起を追加)** | v2では正しいがv3では`検索文書: `。差し替え時に無言で精度劣化する事故ポイントなので設定化 |
| `DB_PATH = r"D:\sapo_ai\..."`のハードコード | **修正** | 実測の結果、外付けHDDは**未接続でC:のみ**。設定ファイル化して、HDD前提の最終形は維持しつつ本日から動かせる形にした |
| `pa.list_(pa.float32(), 768)`の決め打ち | **修正** | 「要確認TODO」のまま数値が入っていた。実測値からスキーマを作る方式に変更 |
| スキーマ(id/date/source/topic/content/vector) | **残す(列を追加)** | `role`・`route`を追加。会話ログには発言者の区別が必須で、routeは検索絞り込みに使えるため |
| 「Open WebUIとLanceDBの連携方法 → 要調査」(6章) | **削除(確定情報へ昇格)** | ②で実機確認し、Open WebUI 0.11.0はLanceDB非対応と確定。③で「自作Pipe内RAG」を選ぶことで論点自体が解消したため、未解決事項からは削除 |
| `python -m venv .venv`での環境構築 | **削除** | 4日目⑧の教訓(PipeはOpen WebUIプロセス内で`exec`される)より、**venvを分けるとPipeからimportできない**。むしろ有害なので削除 |
| 「作業ログ(本ファイル)はHDDの`logs/`に保存」 | **削除** | 事実と異なる(このノートはObsidian vault内`サポートAI作製計画/`にある)。混乱の元なので削除 |
| 冒頭のモデル構成の記述 | **修正** | プロジェクト名がC.L.A.I.R.E.に確定済み(4日目⑧)なので反映 |
| 「本日中に実施」というチェックリスト | **残す(⑧へ移動)** | 検証手順としては妥当。クロスOSテストのみHDD未接続のため次回送りに変更 |

---

## ⑧ 動作確認手順(実施予定)

- [x] `pip install lancedb ollama pyarrow pandas pyyaml` 完了確認
- [x] `ollama pull kun432/cl-nagoya-ruri-base` 完了確認(`ollama list`に表示される)
- [x] Embeddingの実出力次元をコマンドで確認し、値をノートに記録する（768）
- [x] `init_db.py`実行、`conversations.lance`が生成されることを確認
- [x] サンプルテキスト(例:本ノートの一部)を1件Embedding→登録
- [x] `db.open_table("conversations").search(query_vector).limit(3).to_pandas()`で類似検索が動作することを確認
- [x] 日本語の意味検索が実際に効いているか、**表記が違うが意味が同じクエリ**(例:登録文「学習計画を立てて」に対し検索文「勉強の予定を組みたい」)でヒットするかを確認する
- [ ] ~~Windows側で登録→Linux側で検索するクロスOSテスト~~ → 外付けHDD未接続のため次回以降

### ⑧-1 具体的な検証手順(登録〜検索〜意味検索確認をまとめて実施)

	上の3項目(登録・検索・意味検索確認)は個別にコマンドを打つより、1本のスクリプトにまとめて実行した方が確実。`D:\sapo_ai\rag_memory\scripts\test_search.py` として新規作成する。

```python
# D:\sapo_ai\rag_memory\scripts\test_search.py
import uuid
from datetime import date
from pathlib import Path

import lancedb
import ollama
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

DB_PATH = CFG["db_path"]
EMBED_MODEL = CFG["embed_model"]
DOC_PREFIX = CFG["doc_prefix"]
QUERY_PREFIX = CFG["query_prefix"]


def embed(text: str, is_query: bool = False) -> list[float]:
    prefix = QUERY_PREFIX if is_query else DOC_PREFIX
    resp = ollama.embed(model=EMBED_MODEL, input=f"{prefix}{text}")
    return resp["embeddings"][0]


# 意味検索の効果を確認するため、わざと話題の異なる文をまとめて登録する
SAMPLES = [
    "今日は学習計画を立てて、来週までにRAGの実装を終わらせる予定です。",
    "夕食は何を作ろうか迷っている。冷蔵庫に鶏肉があったはず。",
    "Pythonのlancedbライブラリでベクトル検索のテストをしている。",
    "明日は病院の予約があるので午前中は出かける。",
]


def insert_samples():
    db = lancedb.connect(DB_PATH)
    table = db.open_table("conversations")
    rows = [
        {
            "id": str(uuid.uuid4()),
            "date": date.today().isoformat(),
            "source": "test_search.py",
            "role": "user",
            "route": "TEST",
            "topic": "動作確認",
            "content": text,
            "vector": embed(text, is_query=False),
        }
        for text in SAMPLES
    ]
    table.add(rows)
    print(f"{len(rows)}件登録しました")


def search(query_text: str, limit: int = 3):
    db = lancedb.connect(DB_PATH)
    table = db.open_table("conversations")
    qvec = embed(query_text, is_query=True)
    results = table.search(qvec).limit(limit).to_pandas()
    print(f"\nクエリ: 「{query_text}」の検索結果(上位{limit}件)")
    print(results[["content", "_distance"]].to_string(index=False))


if __name__ == "__main__":
    insert_samples()
    # 登録文は「学習計画を立てて」、検索文はあえて表記を変えた「勉強の予定を組みたい」
    search("勉強の予定を組みたい")
```

**実行方法**
```powershell
cd "D:\sapo_ai\rag_memory\scripts"
python test_search.py
```

**確認ポイント**
- 出力される4件(登録した`SAMPLES`)のうち、`_distance`(値が小さいほど類似)が最も小さいのが**「今日は学習計画を立てて…」の行**になっていれば、表記が違っても意味で正しくヒットしている=意味検索が機能している証拠。
- もし「Pythonのlancedbライブラリで…」のような無関係な文の方が距離が近い、または4件の`_distance`がほぼ同じ値でばらつかない場合は、Embeddingモデルや接頭辞(`DOC_PREFIX`/`QUERY_PREFIX`)の設定ミスを疑う(⑤の「接頭辞はバージョンで違う」参照)。
- 複数回スクリプトを実行すると`SAMPLES`が重複登録されていくため、検証が終わったら`db/conversations.lance`フォルダを削除して`init_db.py`から取り直すか、`table.delete("route = 'TEST'")`で消してから本運用に進む。

### ⑧-2 実行結果(2026-08-05)

`test_search.py`を実行し、動作確認まで完了した。

```
4件登録しました

クエリ:「勉強の予定を組みたい」の検索結果(上位3件)
                             content  _distance
  今日は学習計画を立てて、来週までにRAGの実装を終わらせる予定です。   0.254611
              明日は病院の予約があるので午前中は出かける。   0.318625
Pythonのlancedbライブラリでベクトル検索のテストをしている。   0.413203
```

**分析**:登録文「学習計画を立てて」と表記の異なる検索文「勉強の予定を組みたい」に対し、`_distance`最小(0.2546)で正しく意味的にヒットした。「夕食は何を作ろうか」は上位3件に入らず、無関係な話題(食事・病院・lancedb自体の話)より意味的に近い文が優先される結果となり、**意味検索が想定どおり機能していることを確認**した。⑧のチェックリストはこれで全て完了(クロスOSテストのみHDD未接続のため次回送り)。

---

## ⑨ 残課題・次回への持ち越し

- **チャンク分割ロジックが未決定**(Markdown見出し単位 or 文字数固定)。会話ログとObsidianノートで最適解が違う可能性があるため、実データで比較してから決める。
- **Pipeへの組み込みは本日スコープ外**。`support_ai_auto_pipe.py`のどこで「検索して文脈に差し込むか/応答を書き戻すか」の設計は次回。特に、毎ターン検索すると4日目⑨で増えたPhi-4-mini呼び出し(3〜15秒)にさらに上乗せされるため、**routeごとに検索の要否を変える**等の設計が必要。
- **Phi-4-miniのルーターに「RAG検索が必要か否か」を判定させるか**は未決定(routeとは別軸の判定を足すとレイテンシと誤判定が増えるため、まずは無条件検索で様子を見る案も含めて検討)。
- 外付けHDDの接続後、`config.yaml`のパス差し替えとクロスOS動作確認。
- Ruri v2 → v3-310m への差し替え検証(同一768次元なのでスキーマ変更不要。接頭辞の変更を忘れないこと)。
- 記憶の**肥大化対策**(古い会話の要約圧縮・削除ポリシー)は未検討。長期運用で必ず問題になるため、どこかの回で扱う。

---

## ⑩ 質問への回答:RAG設計方針・Embedding選定の最終結論(2026-08-05)

③④⑤で自力検証した内容を踏まえ、外部(クラウドClaude)に「案Bメイン方針の妥当性」と「Embedding選定・VRAM制約への対処」を相談した結果を記録する。

### Q1. RAGの役割分担(案Bメイン+案Aの使い分け)

**結論:認識は妥当。完全統合はせず、案Bメイン+案Aをスポット参照用に残す二層構成を採用する。**

- Open WebUI標準RAG(案A)は「ユーザーが能動的にアップロードしたファイルを検索する」設計であり、③で確認済みの「会話ターンごとの自動蓄積」とは目的もライフサイクルも別物。この点で案Bメインの判断は変わらない。
- 案Aを案Bで置き換えるのは非効率。ファイルアップロードUI・チャンク設定・権限管理などOpen WebUIのGUI資産を自作し直すコストに見合わない。「論文PDFの要約」程度のスポット利用は案Aのまま残す。
- クラウドLLM利用でスポット参照を代替する案は、判断基準を精度ではなく「オフライン運用したいか/機密情報を外部に出したくないか」に置くべき。C.L.A.I.R.E.はローカル完結志向のため、案Aをクラウド替えする必然性は薄いと判断し、**案Aは維持**する。

**長期記憶の設計への追加提案(二層構成)**:単純に発話をそのままembeddingして`conversations`テーブルに貯めるだけだと、「蓄積するほど確度が上がるべき属性情報」と「時系列で移り変わる作業内容」が混在し検索ノイズになる。そのため以下の二層構成を次回(④のPipe組み込み)で検討する。

| 層 | 内容 | 更新方法 | 保存先 |
|---|---|---|---|
| Raw層(episodic) | 会話ログそのもの(chunk済み) | 毎ターン追記 | 本日設計した`conversations`テーブル |
| Profile層(semantic) | ユーザー属性・進行中プロジェクトの状態・未共有の前提を構造化した要約 | 一定ターン数/セッション終了時にLLMで差分要約→upsert | 別テーブル(`user_profile`、キー+最新値で上書き) |

Profile層は上書き型のため肥大化せず、⑨に挙げた「記憶の肥大化対策」の一部解決策にもなる。

### Q2. Embeddingモデル選定とVRAM制約

**結論:Llama3系の採用は非推奨。⑤で選定済みのRuri v2 base(111M・CPU駆動)が制約下の現実解として妥当。**

- Llama3は decoder-only の生成モデルであり、embedding専用に学習されたモデル(E5/BGE/GTE/Ruri等)より抽出精度・効率で劣る。Llama3系のembedding専用モデル(NV-Embed-v2等)は存在するが7B級でVRAM数GB〜十数GBを要し、残りVRAM約1GBの制約とは根本的に相容れないため不採用。
- VRAM制約への3案評価:

| 案 | 判定 | 理由 |
|---|---|---|
| ①CPU駆動の軽量Embedding | **採用** | Ruri v2 base(111M)クラスはCPUでも数十〜数百ms/件。Phi-4-miniのルーティングが既に3〜15秒(4日目⑨)かかるため、CPU embeddingの遅延はボトルネックにならない |
| ②専用Embeddingモデルを常時VRAM常駐/都度スワップ | **非採用** | 残り1GBでは常駐が厳しく、都度スワップはGemma側の再ロードで数秒単位のレイテンシ増を招く。複雑さに見合わない |
| ③外部API/別プロセス分離 | **条件付き採用(変形)** | 「外部API」はローカル完結方針と衝突するため不採用。ただし①の変形として、embeddingをCPU専用の別プロセスに切り出しPipeからローカル呼び出しする構成は運用上むしろ良い |

**実装上の注意(次回のPipe組み込み時に反映)**:Ollama経由でRuri v2 baseを呼ぶ際、Ollamaは既定で空きGPUを使おうとするため、`OLLAMA_NUM_GPU=0`等でembeddingモデルをCPU固定にすること。設定を怠ると残り1GBのVRAMを取り合いGemma側でOOM・速度低下を招くリスクがある。Ruri v3-310mへ差し替える場合(`sentence-transformers`経由)も同様に`device="cpu"`を明示する。



1. ~~4モデルをOllamaで実際にダウンロード・動作検証~~
2. ~~Phi-4-miniの振り分けロジック(プロンプト設計・実装)~~
3. RAGの記憶DB構築 ← 今回(土台の構築まで。Pipeへの組み込みは次回)
4. C.L.A.I.R.E. (Auto) Pipeへの記憶レイヤー組み込み(検索+書き戻し)
5. Cloudflare Tunnelのセットアップ
6. STT/TTSパイプラインの組み立て
