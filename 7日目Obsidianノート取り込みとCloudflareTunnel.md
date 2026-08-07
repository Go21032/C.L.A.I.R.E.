---
project: C.L.A.I.R.E.(さぽーとAI)
date: 2026-08-07
tags: [RAG, Obsidian取り込み, ingest, チャンク分割, CloudflareTunnel, 外部公開, セキュリティ, ルーター候補評価, gemma4, 作業ログ]
status: 未着手
---

[[サポートAI作製計画/6日目RAG記憶レイヤーのPipe組み込み.md|6日目]]で記憶レイヤーのPipe組み込み(検索+書き戻し)が完了し、「チャットAで伝えたことを別チャットBから思い出せる」状態が実機で成立した。今回は記憶の**対象を会話ログからObsidianノート本体へ広げる**(6日目⑨の本命残課題)ことと、ロードマップ上の次ステップである**Cloudflare Tunnelによる外部アクセス環境の構築**の2本立てで進める予定だったが、[[サポートAI作製計画/参考文献.md]]の「次のアクション」(`gemma4:e4b`/`gemma4:e2b`のルーター候補評価)を**先に⓪として実施する**(理由は⓪冒頭を参照)。

> [!note] このノートの位置づけ
> 本ノートは6日目⑨「残課題・次回への持ち越し」を起点に、[[サポートAI作製計画/ノート作成規則.md]]に従って**着手前に作業内容を列挙した計画ノート**として作成した。実行後に「結果」「分析」「改善策」を各セクションへ追記していく。

---

## ⓪ 参考文献の次のアクション:`gemma4:e4b`/`gemma4:e2b`のルーター候補評価(①〜⑥より先に実施)

### 背景/目的

[[サポートAI作製計画/参考文献.md]]の調査メモ(②「ルーターモデル候補としてのGemma4 E2B/E4B」)で、現行ルーター(`phi4-mini-cpu:latest`)の代替候補として`gemma4:e4b`/`gemma4:e2b`が挙がっている。同メモの「次のアクション」1〜3は次の内容:

> 1. `gemma4:e4b`と`gemma4:e2b`(`think:false`指定)を`ollama pull`し、既存の`testset_v1.md`(20問)を`run_testset.py`で複数回実行して分類正答率を比較。
> 2. 同時に`monitor_ollama.py`でtokens/s・TTFT・VRAMを実測し、Phi-4-mini(CPU固定版)と横並び比較。
> 3. 精度(目安8割以上、できればv3プロンプトの実績である95%前後)と速度の両方で優位なモデルをルーターとして採用する。

①〜⑥(Obsidianノート取り込み・Cloudflare Tunnel)は数時間〜複数日かかる作業だが、こちらは**pull+既存スクリプトの軽微な拡張+実行**で完結する小さめの検証であり、かつ「ルーターをどのモデルにするか」は今後の全作業の前提になる。着手前に済ませておく。

> [!warning] E2Bのthinkingモードの罠(参考文献③より)
> `gemma4:e2b`は既定でthinkingモード(内部CoT)が有効。`think: false`を明示しないと分類のような短いタスクで**E4Bの5〜10倍遅くなる**逆転が起きる(参考文献記載: キーワード抽出でE2B 7.4秒 vs E4B 0.74秒)。本セクションでは**必ずthink:false版と既定(think有効)版の両方を計測し、罠を実データで確認する**。

### 作業内容

- [x] `ollama pull gemma4:e4b` / `ollama pull gemma4:e2b`を実行する
- [x] `ollama_client.generate()`に`think`パラメータ(Ollama `/api/generate`のトップレベル`think`フィールド)を追加する(コード修正)
- [x] `router.py`に`ROUTER_THINK: bool | None = None`を追加し、`call_phi4`が`generate(..., think=ROUTER_THINK)`を渡すようにする(コード修正)
- [x] `run_testset.py`に`--model`/`--think`オプションを追加し、実行前に`router.ROUTER_MODEL`/`router.ROUTER_THINK`を差し替えられるようにする(コード修正)
- [x] 3パターン(`gemma4:e4b` / `gemma4:e2b`+`think:false` / `gemma4:e2b`既定=think有効・罠の実測用)で`testset_v1.md`(20問)を`run_testset.py`で実行し、正答率を記録する
- [x] `monitor_ollama.py`で同じ3パターンのtokens/s・TTFT・VRAMを実測する(`--think`引数を追加する追加改修が必要だった。後述)
- [x] 既存の`Phi4mini-CPU固定_run*`(2日目実測)と横並びで速度を比較する
- [x] 精度8割以上(目標95%前後)かつ速度で優位な組み合わせを選び、ルーター候補として結論を出す(採用の本実装は本日ではなく次回以降でよい)

### `--model`/`--think`未対応エラーの原因と対応(着手前に発生した不具合)

**症状**: `python run_testset.py --model gemma4:e4b --label gemma4_e4b` を実行すると
`run_testset.py: error: unrecognized arguments: --model gemma4:e4b` で落ちる。

**根本原因**: このセクションの作業内容チェックリストにある通り、`--model`/`--think`オプションの追加自体が
**本日実施予定のコード修正**であり、実行時点ではまだ`run_testset.py`の`argparse`に定義されていなかった
(=「未実装のオプションを実装前に呼んだ」だけで、既存コードのバグではない)。`router.py`側も
`ROUTER_THINK`定数・`think`引数の受け渡しが未実装だった。

**対応**: チェックリスト通り3ファイルを修正した。
- `ollama_client.generate()`に`think: bool | None = None`引数を追加し、`None`以外なら`/api/generate`の
  リクエストボディに`"think"`フィールドを含めるようにした。
- `router.py`に`ROUTER_THINK: bool | None = None`を追加し、`call_phi4()`が`generate(..., think=ROUTER_THINK)`
  を渡すようにした(`ROUTER_MODEL`と同様、モジュールグローバルとして外部から差し替え可能)。
- `run_testset.py`に`--model`/`--think {true,false}`を追加し、指定時は実行前に`router.ROUTER_MODEL`/
  `router.ROUTER_THINK`を上書きするようにした。`call_phi4`はモジュールグローバルを参照時に読むため、
  `router.py`をimportし直す必要はなく上書きだけで反映される。

修正後、`python run_testset.py --model gemma4:e4b --label gemma4_e4b --help`含め正常動作を確認。

### 実施方法(予定)

```powershell
# 1. モデルをpull
ollama pull gemma4:e4b
ollama pull gemma4:e2b

# 2. 作業ディレクトリへ移動(router.py/run_testset.py/monitor_ollama.pyの実行場所)
cd "C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts"

# 3. 分類正答率の比較(--model/--thinkは本日追加するオプション。デフォルトのsystem_prompt_v3を使用)
python run_testset.py --model gemma4:e4b --label gemma4_e4b
python run_testset.py --model gemma4:e2b --think false --label gemma4_e2b_nothink
python run_testset.py --model gemma4:e2b --label gemma4_e2b_thinkdefault   # 罠の実測用(think既定=有効のまま)

# 4. 速度・VRAM実測(FAST分類相当の短い質問で揃える。既存の実測との条件を合わせる)
python monitor_ollama.py --model gemma4:e4b --prompt "今日の東京の天気を教えて" --label "Gemma4-e4b"
python monitor_ollama.py --model gemma4:e2b --prompt "今日の東京の天気を教えて" --label "Gemma4-e2b-nothink"
```

> [!warning] `monitor_ollama.py`は`ollama run <model> --verbose "<prompt>"`をCLI経由で呼ぶ実装で、現状`think`を渡す口がない
> `gemma4:e2b`のthink無効版を速度計測したい場合、`ollama run gemma4:e2b --help`で`--think`相当のCLIフラグが存在するか先に確認する。存在しなければ`monitor_ollama.py`にも`--think`オプションを追加するか、`run_testset.py`実行時の`elapsed_s`(`retrieve()`ではなく分類1問あたりの実測)を速度の代用指標として使う。どちらの方針にするかは実施時に決めて結果欄に記録する。

### 結果 / 分析 / 改善策

#### 分類正答率(`run_testset.py`、`testset_v1.md`20問、`system_prompt_v3`、`temperature=0`)

| パターン | 正答率 | FAST | DEEP | CODE | CLARIFY | 誤判定 |
|---|---|---|---|---|---|---|
| `gemma4:e4b`(think未指定=既定) | **20/20 (100.0%)** | 5/5 | 5/5 | 6/6 | 4/4 | なし |
| `gemma4:e2b` + `think:false` | 19/20 (95.0%) | 5/5 | 5/5 | 6/6 | 3/4 | X4(期待CLARIFY→実際DEEP) |
| `gemma4:e2b` 既定(think有効) | 19/20 (95.0%) | 5/5 | 5/5 | 6/6 | 3/4 | X4(期待CLARIFY→実際DEEP、同一問題) |

3パターンとも目安の8割は大きく上回り、`gemma4:e4b`は目標の95%前後どころか**testset_v1全問正解**。
`gemma4:e2b`はthinkの有無に関わらず「このコードのアルゴリズムを勉強計画に組み込みたい」(X4)を一貫して
DEEPに誤判定する。thinkのON/OFFで結果が変わらないため、これは**thinkingの罠ではなくe2bというモデル
サイズそのものの分類能力の限界**と判断できる(v3プロンプトの例文でカバーしきれていない曖昧文)。

#### 速度・VRAM実測(`monitor_ollama.py`、プロンプト「今日の東京の天気を教えて」で統一)

| パターン | total_duration | eval_rate | TTFT | eval_count(生成トークン) | VRAM peak |
|---|---|---|---|---|---|
| `gemma4:e4b` 既定(think有効) | 3.73s | 137.7 tok/s | 393ms | 453 | 9920 MiB |
| `gemma4:e4b` + `think=false` | **1.19s** | 130.7 tok/s | 465ms | 93 | 9915 MiB |
| `gemma4:e2b` 既定(think有効) | 2.52s | 204.1 tok/s | 506ms | 410 | 9911 MiB |
| `gemma4:e2b` + `think=false` | **0.65s** | 209.7 tok/s | 437ms | 44 | 9941 MiB |
| (参考)`phi4-mini-cpu`(2日目実測・3回平均) | 3.57s | 21.6 tok/s | 1084ms | - | 1981 MiB |

#### 分析

1. **thinkingの罠は実データで確認できたが、参考文献の想定と範囲が違った**。参考文献③は「E2Bのみ
   thinkingが既定で罠になる」としていたが、実測では**`gemma4:e4b`も既定でthinkingが有効**で、
   `think:false`により`e4b`は3.73s→1.19s(約3.1倍)、`e2b`は2.52s→0.65s(約3.9倍)高速化した。
   生成トークン数もe4bは453→93、e2bは410→44と5〜9割削減されており、罠は両モデルに共通する
   Gemma4系の挙動と考えられる。「参考文献記載の5〜10倍」ほどの劇的な逆転は今回の1問だけの
   計測では再現しなかったが、方向性(thinkingが数倍の無駄な遅延を生む)は明確に裏付けられた。
   **結論:ルーター採用時は`think:false`を必ず明示指定する(router.pyの`ROUTER_THINK=False`固定)。**

2. **精度は`gemma4:e4b`が明確に優位**。`e2b`はthink有無に関係なく同じ1問(X4)を落とすため、
   サイズを削って得られる速度差(e4bとe2b+think:falseの差はわずか0.5秒程度)に対して精度低下
   (100%→95%)が見合わない。**分類タスクという性質上、速度よりまず精度を優先すべきであり、
   `gemma4:e4b`(think:false固定)を第一候補とする。**

3. **速度面ではPhi-4-mini(CPU固定)に対しGemma4系が圧倒的に優位**。eval_rateはe4b/think:falseで
   phi4-mini-cpuの約6倍(130.7 vs 21.6 tok/s)、TTFTも約半分弱(465ms vs 1084ms)。CPU推論という
   ハンデを差し引いても、分類のような短いタスクを繰り返し呼ぶルーターにとって明確な改善になる。

4. **ただしVRAM消費が最大の懸念点**。Gemma4系はGPU常駐のため約9.9GB(16GB中)を占有するのに対し、
   現行の`phi4-mini-cpu`はCPU固定でVRAMを**1.98GB**しか使わない。この設計は2日目ノートで
   「FAST(`gpt-oss:20b`)/DEEP(`gemma4:26b`)/CODE(`devstral-small-2:24b`)とのVRAM奪い合いを避ける」
   目的で意図的にCPU固定にしたものであり(`router.py`のコメント参照)、`gemma4:e4b`をこのまま
   GPU常駐でルーターに採用すると**その設計意図を壊し、モデルスワップの頻発・待ち時間増大を
   再発させるリスクがある**。

5. **副産物:`monitor_ollama.py`の`--think`実装で新たなCLIの罠を発見**。Ollamaの`ollama run --think`は
   cobraの「値省略可」フラグ(`string[="true"]`)のため、`--think false`のように**スペース区切りで
   値を渡すと値を消費してくれず**、`false`が独立した引数としてプロンプト側に紛れ込む
   (実際に`gemma4:e2b`が「ユーザー入力は"false 今日の東京の天気を教えて"」と誤認する出力を実機で確認)。
   `--think=false`のように**必ず`=`区切りで渡す必要がある**。この罠は`monitor_ollama.py`の
   `execute_ollama_run()`にコメント付きで対応済み(`--think=false`形式で組み立てるよう修正)。
   `ollama_client.py`側はREST APIのJSONボディに`think`フィールドを直接入れる方式のため、
   この罠の影響は受けない(CLI経由の`monitor_ollama.py`だけの問題)。

#### CPU固定版`gemma4-e4b-cpu`の作成・再検証(VRAM占有をゼロ許容にするため追加実施)

GPU版`gemma4:e4b`は約9.9GBのVRAMを占有し、FAST(`gpt-oss:20b`)/DEEP(`gemma4:26b`)/CODE
(`devstral-small-2:24b`)とのVRAM奪い合いを許容しない方針としたため、2日目の`phi4-mini-cpu`と
同じ手順でCPU固定版を作成した。

```powershell
@'
FROM gemma4:e4b
PARAMETER num_gpu 0
'@ | Set-Content -Encoding utf8 Modelfile.gemma4-e4b-cpu

ollama create gemma4-e4b-cpu -f Modelfile.gemma4-e4b-cpu
```

**再検証結果**:

| 項目 | GPU版(`gemma4:e4b`)+think:false | CPU固定版(`gemma4-e4b-cpu`)+think:false | (参考)`phi4-mini-cpu` |
|---|---|---|---|
| VRAM peak | 9915 MiB | **2775 MiB**(3回平均) | 1981 MiB |
| PROCESSOR(`ollama ps`) | 100% GPU | **100% CPU** | 100% CPU |
| monitor_ollama単発応答の`eval_rate` | 130.7 tok/s | 16.5 tok/s(3回平均) | 21.6 tok/s |
| **ルーター実運用相当**(`run_testset.py`、ルールベース即決を除く実モデル呼び出しの平均`elapsed_s`) | 2.76s/回 | **4.11s/回** | 2.93s/回 |
| 分類正答率(testset_v1 20問) | 100.0% | **100.0%**(誤判定なし、GPU版と完全一致) | (system_prompt_v3実績95〜100%) |

- CPU固定化によりVRAM占有は9915MiB→**2775MiB**まで圧縮でき、目標(GPU非占有)は達成。
  `phi4-mini-cpu`(1981MiB)より若干重いが、モデルサイズ差(e4bは9.4GB、phi4-miniは2.5GB)を
  考えれば妥当な範囲。
- 単発応答生成の`eval_rate`はCPU化で130.7→16.5 tok/sまで低下し、`phi4-mini-cpu`(21.6 tok/s)より
  やや遅い。ただし**ルーターの実運用は「route判定用の短いJSON1行を生成するだけ」**であり、
  `run_testset.py`で実測した「実際の分類1回あたりの所要時間」は**4.11秒**(`phi4-mini-cpu`は2.93秒)と、
  体感で気になるレベルの差ではない(2日目ノート「速度低下は体感上気になるレベルではない」という
  判断を踏襲)。
- 分類精度はCPU化後も**20/20(100%)を完全維持**しており、`num_gpu 0`化による精度劣化は確認されなかった。

#### 結論(確定・本日中に切り替え実施済み)

**ルーターを`gemma4-e4b-cpu:latest`(CPU固定・`think:false`固定)に切り替えた。** VRAM占有ゼロを
必須要件として、GPU版ではなく最初からCPU固定版を正式採用とする。`router.py`を以下の通り変更済み:

```python
ROUTER_MODEL = "gemma4-e4b-cpu:latest"
ROUTER_THINK: bool | None = False
```

`python router.py "今日の東京の天気を教えて"` / `python router.py "...TypeErrorが出るんだけど..."`で
実機動作を確認し、`ollama ps`で**ルーター(`gemma4-e4b-cpu`)がCPU、ターゲットモデル(`gpt-oss:20b`等)が
GPU**という意図通りの棲み分けになっていることも確認済み。


### OpenWebUIで実際の回答速度検証
31~36sほどで回答可能
使用したプロンプトは「私の猫の名前は何ですか？」
### 残課題

- `gemma4:e2b`のX4誤判定(「このコードのアルゴリズムを勉強計画に組み込みたい」→DEEP)はthinkの
  有無に関係なく再現する、モデルサイズに起因する分類精度の限界。`e2b`は不採用のため実害はないが、
  将来的にv3プロンプトの例文を拡充する際の参考ケースとして記録しておく
- `monitor_ollama.py`の`--think`実装で見つけた`--think=false`(=区切り必須)の罠は、CLI経由で
  Ollamaモデルを検証する際に再発しうる汎用的な注意点として、今後CLIを直接叩くスクリプトを書く際は
  必ず意識すること(`ollama_client.py`はREST APIのJSONボディ経由のためこの罠の影響を受けない)
- ルーター切り替え後、実運用(Open WebUI経由のPipe)でも体感速度・精度に問題がないか、次回以降の
  作業で実機確認する(今回はCLI単体・`run_testset.py`での検証まで)

---

## ① 本日のゴールと作業内容

### 背景/目的

6日目までで「会話を貯めて思い出す」ことはできるようになった。しかしC.L.A.I.R.E.が参照できるのは**このPipe経由で交わした会話だけ**であり、1日目設計の「第二の脳」という位置づけからすると、既にvaultに蓄積されている作業ログ・設計ノート(このノート群そのもの)を参照できないのは片手落ちである。「5日目に決めたチャンク分割の上限値は?」のような質問に、ノートを読み込ませておけば答えられるようになる。

同時に、現状C.L.A.I.R.E.は自宅PCのlocalhostでしか使えない。外出先やスマホから使えるようにするのがロードマップ上の次ステップ(Cloudflare Tunnel)だが、**CODEルートには任意のPythonファイルを作成・実行する機能(4日目⑩のACTIONブロック)がある**ため、認証なしで外部公開することは絶対にできない。セキュリティ設計を含めて片付ける。

### 6日目⑨の残課題と本日の扱い

| #   | 6日目⑨の残課題 | 本日の扱い | 理由 |
| --- | --- | --- | --- |
| 1   | Obsidianノート自体の取り込み(`ingest/`経由・`chunk_markdown`使用) | **本日実施**(④⑤) | 本日の主目的。6日目で「会話ログのみを対象とした」と明記した積み残しの本命 |
| 2   | `chunk_markdown`のフォールバック機械分割時に見出し行が失われる問題(6日目③改善策・P3) | **本日実施**(③) | 6日目⑨-1で「ノート取り込みに着手する日」が着手条件と定めていた。本日その条件が揃う |
| 3   | Cloudflare Tunnelのセットアップ | **本日実施**(⑥) | ロードマップ「次のステップ 5.」そのもの |
| 4   | `format_context`の`max_distance=0.45`の妥当性検証(P2) | **一部実施**(⑤) | ノート取り込みでデータが一気に増えるため、6日目④で懸念した「無関係な記憶がノイズになる」状況が初めて現実的になる。取り込み後の実データで再評価する |
| 5   | 外付けHDD接続後のパス差し替え・クロスOS動作確認 | **棚卸しのみ**(②) | パス差し替えは実質完了済み(後述)。残るクロスOS対応の要否を判断して課題としてクローズする |
| 6   | CLARIFYの「粘着」対策(`last_route`を引き継がない) | **保留** | 6日目⑧-3のガード追加で実害の経路は塞がっており、実機で再発していない。優先度が低いと判断 |
| 7   | Ruri v2 → v3-310m 差し替え検証 | **保留** | v2で精度に不満が出ていない(6日目④で合致0.20の良好な数値)。先にノート取り込みを終える方が価値が高い |
| 8   | RECALL_TRIGGERSの網羅性(6日目⑧-4) | **運用しながら** | 実運用で誤判定を見つけ次第、正規表現を追加する運用課題。単独で作業日を取るものではない |
| 9   | Open WebUIの「タスクモデル」設定を専用軽量モデルへ分離 | **⑥と同時**(任意) | Open WebUI管理画面を触る作業なので、Cloudflare Tunnel設定のついでに実施できる |

### 作業内容

- [x] 事前確認:`config.yaml`の実キーと6日目⑥の記載の食い違いを解消する(②)
- [x] 事前確認:取り込み前の`conversations`テーブル行数を記録する(取り込み後との比較用)(②)
- [ ] `chunker.chunk_markdown`を修正し、機械分割された2チャンク目以降にも見出し行を付与する(③)
- [ ] ノート取り込みスクリプト`ingest_notes.py`を新規作成する(④)
- [ ] 取り込み対象範囲・除外ルール・スキーマへのマッピングを決定する(④)
- [ ] **再取り込み時に重複が増殖しない**冪等な設計にする(④)
- [ ] 実際にvaultのノートを取り込み、件数・所要時間を記録する(⑤)
- [ ] 取り込んだノートが実際に検索でヒットするか、Pipe経由で確認する(⑤)
- [ ] CODEルートの`route`絞り込みとノート取り込みの整合性を判断・対応する(⑤)
- [ ] データ増加後に`max_distance=0.45`が妥当か再評価する(⑤)
- [ ] Cloudflare Tunnelをセットアップし、外部からOpen WebUIへ到達できるようにする(⑥)
- [ ] **Cloudflare Accessで認証を必須にし、認証なしでは一切到達できないことを検証する**(⑥)

### 完了条件(本日分)

- [ ] vault内のノートが`conversations`(または新テーブル)に取り込まれ、件数が記録されている
- [ ] 「5日目に決めたチャンク分割の上限値は何字か」のような**ノートにしか書かれていない情報**を、Pipe経由の質問で引き出せる
- [ ] `ingest_notes.py`を2回連続実行しても行数が二重に増えない(冪等性)
- [ ] 機械分割されたチャンクにも見出し行が含まれており、単独で読んで何の話か分かる
- [ ] 外部ネットワーク(スマホのモバイル回線等)からOpen WebUIにアクセスできる
- [ ] **認証を通していない状態では、外部からOpen WebUIに一切到達できない**(最重要)

---

## ② 事前確認:記録と実体の食い違いの棚卸し(⚠️ 着手前に必ず実施)

### 背景/目的

7日目ノート作成時点で実体を確認したところ、6日目ノートの記載と実際のファイルが食い違っている箇所が2つ見つかった。ノート取り込みは`config.yaml`のチューニング値(`max_chars`等)を前提に実装するため、着手前に解消しておく。

### 食い違い1:`config.yaml`に`max_chars`/`overlap`/`max_distance`が存在しない

6日目⑥の設定ファイル表には次のように書かれている:

> `rag_memory/config.yaml`(修正) | `db_path`・`embed_model`・接頭辞に加え、**`max_chars`/`overlap`/`max_distance`を追加** | チューニング値をコードから追い出し…

しかし実体(`D:\sapo_ai\rag_memory\config.yaml`)は以下の4キーしかなく、**追加されていない**:

```yaml
db_path: "D:/sapo_ai/rag_memory/db"
embed_model: "kun432/cl-nagoya-ruri-base"
doc_prefix: "文章: "
query_prefix: "クエリ: "
```

現状、`MAX_CHARS = 400` / `OVERLAP = 80`は`chunker.py`に、`max_distance=0.45`は`memory_store.format_context`の引数既定値にハードコードされたままである。6日目⑥の記載は**「そうする予定だった」ものが実装されないままノートにだけ書かれていた**状態であり、記録の誤りにあたる。

**対応方針**:③でどのみち`chunker.py`を触るため、そのタイミングで`config.yaml`へ追い出す。あわせて6日目⑥の該当行に「7日目②で実体との食い違いが判明・7日目③で対応」と追記して記録を正す。

### 食い違い2:外付けHDDの残課題が実質完了しているのにリストに残っていた

6日目⑨に「外付けHDD接続後の`config.yaml`パス差し替えとクロスOS動作確認(5日目からの持ち越し)」が残っていたが、実体を確認すると:

- `D:\sapo_ai`は接続済みで存在する
- `config.yaml`の`db_path`は既に`"D:/sapo_ai/rag_memory/db"`を指している
- 6日目の実機検証(⑧-1〜⑧-4)はすべてこのパスのDBに対して成功している

つまり**「パス差し替え」は6日目の作業中に実質完了しており、5日目から機械的に引き継いだままリストに残っていただけ**だった。

残るのは「クロスOS動作確認」だが、これは`db_path`がWindows固有表記(`D:/`)で直書きされている以上、Mac/Linuxから同じHDDを使うなら**設定の持ち方そのもの(環境変数化・OS判定など)を変える必要がある**別の課題である。現状クロスOSで使う具体的な予定がないため、**「パス差し替え」は完了としてクローズし、「クロスOS対応」は着手条件(実際に別OSから使う必要が生じたとき)付きの課題として書き換える**。

### 実施方法

```powershell
# 1. 現在のconfig.yamlの中身を確認(食い違い1の確認)
Get-Content D:\sapo_ai\rag_memory\config.yaml

# 2. 取り込み前のDB行数を記録する(④⑤の前後比較に必須)
cd D:\sapo_ai\rag_memory\scripts
python -c "import memory_store; print('取り込み前の行数:', memory_store.count_rows())"

# 3. C:側バックアップとD:側実体の差分がないか確認
Compare-Object `
  (Get-Content C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts\rag_memory\config.yaml) `
  (Get-Content D:\sapo_ai\rag_memory\config.yaml)
```

### 結果
１つ目のコマンド：中身が出力された
２つ目のコマンド：取り込み前の行数: 29
３つ目のコマンド：出力無し
### 分析・改善策

すべて正常

---

## ③ 課題1:`chunk_markdown`の見出し欠落修正(6日目P3・着手条件が揃った)

### 背景/目的

6日目③の実測で、`## 実装`配下が400字を超えて機械分割された際、**2チャンク目に見出し行が付かず「何についての記述か分からない断片」になる**ことが再現していた。会話ログ(`chunk_utterance`)には影響せず、ノート取り込み(`chunk_markdown`)にのみ影響するため「ノート取り込み着手時に対応」(6日目⑨-1のP3)としていた。本日その着手条件が揃う。

これを直さないまま取り込むと、検索でヒットしたチャンクが文脈を欠いた状態でプロンプトに差し込まれ、**応答品質を下げるだけでなく原因の切り分けも難しくなる**(「検索は当たっているのに答えがおかしい」という分かりにくい症状になる)。取り込みより先に直す。

### Before/After

| 項目        | 修正前                                       | 修正後                            | 狙い                           |
| --------- | ----------------------------------------- | ------------------------------ | ---------------------------- |
| 機械分割時の見出し | 1チャンク目にしか含まれない                            | **2チャンク目以降にも見出し行を再付与**する       | 単独で検索結果に出ても「何についての記述か」が分かる   |
| チャンク先頭    | `ああああ…`(本文の途中から)                          | `## 実装\nああああ…`                 | ベクトルに話題の文脈が含まれ、検索精度も上がることを期待 |
| チューニング値   | `chunker.py`に定数直書き(`MAX_CHARS`/`OVERLAP`) | `config.yaml`から読む(②の食い違い1への対応) | 再取り込みなしで値を変えて試せるようにする        |

### 実装方針:`rag_memory/scripts/chunker.py`(修正)

```python
def chunk_markdown(text: str) -> list[str]:
    """Markdownを見出し(#〜######)単位で分割する。

    見出し配下が長く機械分割される場合、2チャンク目以降にも見出し行を
    再付与して「何についての記述か」という文脈を保持する(7日目③)。
    """
    parts = re.split(r"(?m)^(?=#{1,6}\s)", text)
    chunks: list[str] = []
    for part in parts:
        lines = part.split("\n", 1)
        heading = lines[0] if re.match(r"^#{1,6}\s", lines[0]) else ""
        pieces = _split_by_length(part)
        for i, piece in enumerate(pieces):
            # 1つ目は元々見出しを含んでいるのでそのまま。
            # 2つ目以降は見出しを失っているため先頭に付け直す。
            if i > 0 and heading and not piece.startswith(heading):
                piece = f"{heading}\n{piece}"
            chunks.append(piece)
    return chunks
```

> [!warning] 見出し再付与でチャンクが`max_chars`を超える点に注意
> 見出し行を後から足すぶん、チャンクは`MAX_CHARS`(400字)をわずかに超える。
> Ruri v2 base(最大512トークン)の上限に対して400字は保守的な値なので実害は
> 出ない想定だが、**「上限ぴったりに詰める」設計ではなくなる**ことは意識しておく。
> 見出しが極端に長いノートがあると効いてくるため、③の検証で最大チャンク長を確認する。

### 作業内容

- [x] `chunk_markdown`を上記方針で修正する
- [ ] `MAX_CHARS`/`OVERLAP`を`config.yaml`から読むように変更する(②の食い違い1)
- [x] `tests/`に見出し再付与のユニットテストを追加する(400字超の見出し配下で全チャンクが見出しで始まること)
- [x] 生成される最大チャンク長を確認し、512トークン上限に対して余裕があることを確認する

### 反映確認の方法

作業内容チェックリストが要求しているのは「見出し再付与のユニットテスト」と「最大チャンク長が512トークン上限に収まることの確認」の2点のみ。それ以外(動作確認スクリプト・DB取り込み後の統合確認・統計スクリプト等)は④⑤で実際にノートを取り込んだ後でなければ意味を持たない工程であり、この時点では不要と判断し実施しなかった。

> [!important] C:側(vault)はバックアップ、実行対象はD:側
> `memory_store.py`/`ingest_notes.py`が実際にimportする`chunker.py`は`D:\sapo_ai\rag_memory\scripts\chunker.py`であり、vault(C:側)の`サポートAI作製計画/scripts/rag_memory/chunker.py`はバックアップ(6日目`test_memory_store.py`の冒頭コメントと同じ位置づけ)。C:側だけでテストが通っても「本番が読み込むファイルに修正が入っているか」の証明にはならないため、**D:側でも同じテストを実行して確認した**。

- **作成したファイル(2箇所)**:
  1. `サポートAI作製計画/scripts/tests/test_chunker.py`(vault内・新規)
     - 既存の`tests/test_router.py`と同じ`unittest`スタイルに合わせ、`sys.path`に`scripts/rag_memory`を追加して`chunker`をimportする形にした
  2. `D:\sapo_ai\rag_memory\scripts\test_chunker.py`(本番実体・新規、同内容をD:側の構成=`import chunker`直読みに合わせて配置)
     - `test_memory_store.py`と同じ流儀で、D:側では`sys.path`操作なしに直接`import chunker`できる(同一ディレクトリのため)
  - どちらも`TestChunkMarkdownHeadingPreservation`クラスにテスト2件:
    1. `test_heading_repeated_in_every_chunk_after_mechanical_split`:見出し配下を`MAX_CHARS`(400字)超のテキストにして`chunk_markdown`を呼び、複数チャンクに分割されること・**全チャンクが見出し行で始まること**を確認
    2. `test_max_chunk_length_has_margin_against_ruri_token_limit`:同条件で生成した全チャンクの最大文字数が、Ruri v2 baseの上限512トークンに対して余裕(512字未満)があることを確認
- **実行結果**:
  - C:側(vault):`python -m pytest tests/test_chunker.py -v` → 2件とも`PASSED`。`python -m pytest tests/ -q` → 既存分含め**74 passed**(6日目時点72件+今回2件、既存テストの破損なし)
  - **D:側(本番実体)**:`cd D:\sapo_ai\rag_memory\scripts && python test_chunker.py -v` → 2件とも`ok`(`実行対象chunker.py: D:\sapo_ai\rag_memory\scripts\chunker.py`と出力させ、対象ファイルを明示して確認)。事前に`diff`でC:側とD:側の`chunker.py`が完全一致していることも確認済み

これにより「`chunker.py`の修正(見出し再付与)が、実際にingest/memory_storeが読み込むD:側の実体にも反映され、正しく動作していること」を裏付けた。

### 結果 / 分析 / 改善策

`chunk_markdown`の修正は意図通り反映されていることをユニットテストで確認済み(上記参照)。`config.yaml`からのチューニング値読み込み(2番目のチェックリスト項目)は未着手のため、`結果`の確定は保留する。

### 残作業

- コードブロック(\`\`\`〜\`\`\`)を分断しない配慮は引き続き未実装(6日目③からの持ち越し)。ノート取り込みでは**このプロジェクトのノート自体がコードブロックだらけ**なので、6日目時点より問題が顕在化しやすい。③の結果を見て、必要なら④の前に対応する

---

## ④ 課題2:ノート取り込みスクリプト`ingest_notes.py`の設計・実装

### 背景/目的

6日目までの`memory_store.append_turn()`は「会話の1発言」を登録する前提のインターフェース(`chat_id`/`role`/`route`が必須)で、ノート取り込みには合わない。ノート用の登録経路を用意する。

### 決めるべき設計論点

| 論点 | 選択肢 | 暫定方針 | 理由 |
|---|---|---|---|
| 取り込み対象 | (A)vault全体 / (B)`サポートAI作製計画/`配下のみ | **B(まず限定)** | 全体を入れると個人的なノートまで混ざり、検索ノイズと精度の切り分けが同時に難しくなる。まずこのプロジェクトのノートだけで挙動を見る |
| 除外対象 | `.obsidian/`・添付ファイル・テンプレート | **除外する** | 設定JSONや画像パスをベクトル化しても検索ノイズにしかならない |
| `role`の値 | `note` / `user`流用 | **`note`(新設)** | 会話由来かノート由来かを後から区別・削除できるようにする |
| `route`の値 | `NOTE`(新設) / 既存4値に混ぜる | **`NOTE`(新設)** | 同上。ただし⑤のCODEルート絞り込みと衝突する(後述) |
| `source`の値 | `note:<vault相対パス>` | 採用 | `chat:<chat_id>`と同じ形式で揃える。**再取り込み時の削除キーとして使う** |
| `topic`の値 | frontmatterの`project` or ファイル名 | ファイル名(拡張子なし) | 「どのノート由来か」が検索結果で分かるようにする |
| frontmatter | 取り込む / 除外する | **除外する** | YAMLのキー名(`project:`等)がベクトルに混ざると精度を下げる。ただし`tags`は`topic`に活かす余地あり |
| 冪等性 | 追記のみ / 同一`source`を削除してから追加 | **削除してから追加(upsert相当)** | 完了条件「2回実行しても二重に増えない」。ノートは更新され続けるので、追記のみだと古い版が残り続ける |

### 実装方針:`rag_memory/scripts/ingest_notes.py`(新規)

```python
"""Obsidianのノートを記憶DB(LanceDB)へ取り込む。

会話ログ(memory_store.append_turn)と違い、ノートは「更新され続ける」ため、
同一sourceの既存行を削除してから登録し直す(冪等・upsert相当)。

使い方:
    python ingest_notes.py                    # 既定(サポートAI作製計画/配下)を取り込む
    python ingest_notes.py --dry-run          # 登録せずに対象ファイルとチャンク数だけ表示
    python ingest_notes.py --path <相対パス>   # 対象を限定して取り込む
"""
```

処理の流れ(擬似コード):

1. 対象ディレクトリを再帰的に走査し`*.md`を列挙(除外パターンを適用)
2. 各ファイルについて:
   1. frontmatter(`---`〜`---`)を除去して本文を取り出す
   2. `chunker.chunk_markdown(本文)`でチャンク化
   3. `source = f"note:{相対パス}"`で**既存行を`table.delete()`してから**追加(冪等)
   4. 各チャンクを`embed()`して`role="note"` / `route="NOTE"` / `topic=ファイル名`で登録
3. 取り込んだファイル数・チャンク数・所要時間を標準出力に表示

> [!important] `--dry-run`を必ず先に実行する
> 6日目⑤で**pytestが本番DBに36件のゴミを書き込む事故**を起こしている。取り込みは
> 件数が桁違いに多く、失敗すると復旧が面倒(どの行が今回の取り込み分か区別が要る)。
> `--dry-run`で「対象ファイル数・総チャンク数・推定所要時間」を確認してから本実行する。
> `source`が`note:`接頭辞で統一されているため、最悪`table.delete("source LIKE 'note:%'")`で
> ノート由来だけを一括削除してやり直せる設計にしておく。

### 見積もり

`embed()`は6日目④の実測で**1件あたり約0.06〜0.07秒**(モデル常駐中)。仮にノート20本×平均30チャンク=600チャンクなら、Embeddingだけで**約40秒**。初回はモデルロード分が上乗せされる。この規模なら現実的な範囲。

### 作業内容

- [x] `ingest_notes.py`を新規作成する
- [x] `--dry-run`で対象ファイル数・チャンク数を確認する
- [x] 本実行し、件数・所要時間を記録する
- [x] **2回連続実行して行数が二重に増えないこと**(冪等性)を確認する
- [x] 取り込んだチャンクを数件目視し、frontmatterが混ざっていないか・見出しが付いているかを確認する

### 実装差分:設計時点からの変更点

- `config.yaml`(食い違い1で追加した`max_chars`/`overlap`/`max_distance`に加え)に**`vault_root`
  (vault絶対パス)と`ingest_default_path`(既定の取り込み対象。vault相対パス)を新規追加**した。
  `ingest_notes.py`はD:側(`D:\sapo_ai\rag_memory\scripts`)から実行するが、取り込み対象のノートは
  C:側vault(`C:\Users\gakuh\Documents\obsidian`)にあるため、実行場所とは別に「vaultの場所」を
  知る手段が設計時点で漏れていた。`memory_store.py`と同じ`config.yaml`単一情報源の方針に合わせ、
  ハードコードせずconfig.yamlへ追加する形にした(C:側バックアップ・D:側実体の両方に反映済み)。
- 除外対象(設計論点表の「除外対象」)は当初`.obsidian`・添付ファイル・テンプレートのみを想定していたが、
  実装時に`.git`(このプロジェクトフォルダ自体がgitリポジトリになっている)・`.pytest_cache`・
  `__pycache__`も除外リストに加えた。いずれも`*.md`を直接globすれば実害はほぼ無いが、
  `.pytest_cache/README.md`のようなノイズファイルを確実に弾くため明示した。
- `--path`は設計通りディレクトリ相対パスを受け付けるほか、**単一の`.md`ファイルパスも受け付ける**
  よう拡張した(1ファイルだけ再取り込みして動作確認したい場面を想定)。

### 実施したコマンド

```powershell
# 作業ディレクトリ(D:側=実行対象。C:側vaultは同一内容のバックアップ)
cd D:\sapo_ai\rag_memory\scripts

# 1. ユニットテスト(C:側vaultのtests/で実施。frontmatter除去・除外判定の純粋関数を検証)
#    cd "C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts" で実行
python -m pytest tests/ -q
# → 80 passed(6日目時点72件+7日目③2件+7日目④(ingest_notes)6件)

# 2. 取り込み前の行数確認(②で記録した29件と一致することを確認)
python -c "import memory_store; print('取り込み前の行数:', memory_store.count_rows())"

# 3. dry-run(対象ファイル・チャンク数のプレビュー。文字化け防止にPYTHONIOENCODING指定)
$env:PYTHONIOENCODING = "utf-8"
python ingest_notes.py --dry-run

# 4. 本実行
python ingest_notes.py

# 5. 取り込み後の行数確認
python -c "import memory_store; print('取り込み後の行数:', memory_store.count_rows())"

# 6. 冪等性確認:もう一度本実行し、行数が変わらないことを確認
python ingest_notes.py
python -c "import memory_store; print('2回目実行後の行数:', memory_store.count_rows())"

# 7. 目視確認:role='note'のサンプル抽出・frontmatter混入チェック・最大チャンク長確認
python -c "
import memory_store
t = memory_store._table()
df = t.to_pandas()
notes = df[df['role']=='note']
print('note行数:', len(notes), '/ source種類数:', notes['source'].nunique())
lens = notes['content'].str.len()
print('最大チャンク長:', lens.max(), '/ 平均:', round(lens.mean(),1), '/ 512字以上:', (lens>=512).sum())
for _, row in notes.sample(3, random_state=1).iterrows():
    print('source:', row['source'], '/ topic:', row['topic'])
    print('先頭120字:', row['content'][:120].replace(chr(10), '\\\\n'))
    print('frontmatterキー混入(project:):', 'project:' in row['content'])
"
```

### 結果 / 分析 / 改善策

#### 結果

| 項目 | 結果 |
|---|---|
| ユニットテスト(C:側vault、`pytest tests/ -q`) | **80 passed**(既存74件+今回`test_ingest_notes.py`6件、破損なし) |
| 取り込み前行数 | 29件(②の記録と一致) |
| dry-run 対象ファイル数 | 11件(`サポートAI作製計画/`直下7ノート+`scripts/plans`1件+`scripts/prompts`1件+`ノート作成規則.md`+`参考文献.md`) |
| dry-run 推定チャンク数 | 777チャンク |
| 本実行 所要時間 | **27.5秒**(見積もりの約40秒より速かった。EmbeddingモデルはD:側実行のたび毎回ロードが走るが、Ollama側で既に常駐していたためロード分がほぼ乗らなかったと考えられる) |
| 取り込み後行数 | **806件**(29+777で一致。差分ゼロ) |
| 2回目実行後行数 | **806件**(冪等性確認:増殖なし。所要時間19.9秒とやや短縮=DB接続・モデルとも温まっていたため) |
| role='note'行数/source種類数 | 777件 / **11**(ファイル数と一致。1ファイル=1source) |
| 最大チャンク長 | **488字**(512字未満で目標クリア) / 平均311.3字 / 512字以上のチャンクは**0件** |
| frontmatter混入チェック | サンプル3件とも`project:`等のキー文字列は検出されず(除去できている) |
| 見出し付与の目視 | サンプルに`### 実装:...`・`## ⑧-4 「マツコ問題」再々発:...`など見出し行が先頭に残っていることを確認 |

#### 分析

1. **設計通り、冪等な取り込みが機能している**。同一`source`(`note:<vault相対パス>`)を
   `delete`してから`add`する方式のため、2回連続実行しても806件から増えなかった。完了条件
   「`ingest_notes.py`を2回連続実行しても行数が二重に増えない」を満たした。
2. **frontmatter除去・見出し保持は狙い通り動作**。正規表現1本(`\A---\n.*?\n---\n?`)での
   除去でも、このプロジェクトのノートは全て`---`で始まる単純なYAMLブロックのみのため
   問題なく機能した。目視サンプルでもYAMLキーの混入は確認されなかった。
3. **設計時点で見落としていた「vaultの場所をどう知るか」という論点が実装時に表面化した**。
   設計論点表(決めるべき設計論点)には出てこなかったが、D:側で実行するスクリプトが
   C:側vaultのファイルを読む以上、両者を結ぶ設定が必要だった。ハードコードせず
   `config.yaml`に追い出したことで、6日目⑥・7日目②で問題になった「そうする予定だった
   ものが実装されずノートにだけ書かれていた」状態の再発を避けられた。
4. **既知の残課題(コードブロック分断)が実データで再現することを確認した**。目視サンプルの
   1件(`6日目RAG記憶レイヤーのPipe組み込み.md`由来)で、Pythonの関数定義の途中で
   チャンクが切れている(`, overlap: int = OVERLAP) -> list[str]:`から始まる断片)ことを
   確認した。③の残作業で「このプロジェクトのノートはコードブロックだらけなので問題が
   顕在化しやすい」と予想していた通りの結果であり、**⑤で検索精度に実害が出るかを見て、
   対応の優先度を判断する**(現時点ではコードを直接検索で引きたい質問は少ないと想定される
   ため、⑤の4問の検証結果を見てから着手を判断する)。
5. **速度は見積もりより余裕があった**。見積もりは「600チャンクでEmbeddingのみ約40秒
   (モデル常駐時)」だったが、実際は777チャンク(見積もりより3割弱多い)で27.5秒。
   ノート取り込みが数十秒オーダーで完結することが確認でき、今後ノートを追記した際の
   再取り込み運用(全量再取り込み・upsert相当)も現実的なコストで回せる。
6. **チャンク長の余裕は維持されている**。見出し再付与(③)によりMAX_CHARS(400字)を
   超えるチャンクは出るが、実データでの最大値は488字。Ruri v2 baseの512トークン上限
   (③のテストで使った保守的な字数上限512字)に対してまだ余裕があり、③の懸念
   (「見出しが極端に長いノートがあると効いてくる」)は今回のノート群では顕在化しなかった。

#### 改善策

- コードブロックを分断しないチャンク分割は、⑤の検証結果(検索精度への実害の有無)を見てから
  着手の要否・優先度を判断する(残課題へ記録)。
- 再取り込みの運用(ノート更新のたびに全量再実行するか、変更ファイルのみ`--path`で個別実行するか)
  は今後の運用課題として残課題へ記録する。

---

## ⑤ 課題3:取り込んだノートが検索・応答に効くかの確認

### 背景/目的

登録できても、実際にPipe経由の質問で引き出せなければ意味がない。6日目⑧-1で「保存はできているのに参照できない」(質問側がCLARIFYに落ちていた)という事故を経験しているため、**登録の確認と参照の確認は必ず分けて行う**。

### ⚠️ 設計上の衝突:CODEルートの`route`絞り込みでノートが引けない

6日目⑤で決めたroute別の検索方針は以下だった:

| route | 検索(retrieve) |
|---|---|
| `FAST` | する(top-3・絞り込みなし) |
| `DEEP` | する(top-3・絞り込みなし) |
| `CODE` | **する(top-3・`route='CODE'`で絞り込み)** |

ここに`route='NOTE'`のデータを入れると、**CODEルートの質問では`route='CODE'`で絞り込まれるためノートが一切ヒットしない**。しかし「このプロジェクトのノート」はコード関連の記述が中心であり、**CODEルートでこそ引きたい**という逆転が起きる。

**対応方針(案)**:CODEルートの絞り込みを`route = 'CODE'`から`route IN ('CODE', 'NOTE')`に変更する。実装は`memory_store.retrieve()`の`where`句と、Pipe側`_recall()`の引数の見直しが必要。⑤で実際に検証してから決める。

### 検証に使う質問(ノートにしか書かれていない情報)

モデルが一般知識で偶然答えられる内容だと6日目⑧-1と同じ誤判定事故を起こすため、**このプロジェクトのノートを読まない限り絶対に答えられない**質問を選ぶ:

| # | 質問 | 正解(ノート内の記述) | 想定route |
|---|---|---|---|
| 1 | チャンク分割の上限値は何字に決めた? | 400字(オーバーラップ80字) | FAST |
| 2 | Ruri v2で意味が合致したときのdistanceはいくつだった? | 0.20〜0.25 | FAST |
| 3 | マツコ問題の最終的な原因は何だった? | 想起質問がプロンプトの文章ルールで汎化されず、正規表現(RECALL_TRIGGERS)で対応した | DEEP |
| 4 | CODE_TRIGGERSを語幹で区切るようにした理由は? | 「バグも直して」等の口語の活用揺れを拾うため | CODE |

### 作業内容

- [x] 上記4問を`memory_store.retrieve()`単体で検索し、正しいチャンクがヒットするか確認する
- [x] ヒットしたチャンクの`_distance`を記録し、`max_distance=0.45`で足切りされないか確認する
- [x] CODEルートの`route`絞り込み問題(上記)を検証し、対応方針を決めて実装する
- [x] Pipe経由(`smoke_test_pipe.py`相当)で4問を実行し、応答に正解が含まれるか確認する
- [x] Open WebUI上でも1問試し、実機で成立することを確認する(**ユーザー実機確認で成功**。詳細は下記結果参照)
- [x] **データ増加後に`max_distance=0.45`が妥当か再評価する**(6日目⑨のP2。ノートが増えた今なら「明確に無関係な記憶」が自然に存在するので検証できる)

### 実施したコマンド

```powershell
cd D:\sapo_ai\rag_memory\scripts

# 1. retrieve()単体で4問(route絞り込みなし/CODEのみ/CODE+NOTE)の距離を確認
#    (検証用の使い捨てスクリプトで実施。結果を記録後は削除済み)

cd "C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts"

# 2. Pipe経由(smoke_test_pipe.py相当)で4問を実行
python verify5_pipe_tmp.py   # 検証用の使い捨てスクリプト。結果を記録後は削除済み

# 3. 変更を加えたユニットテストの再実行
python -m pytest tests/ -q
```

### 実装差分:CODEルートの`route`絞り込み対応

⚠️で書いた懸念(`route='CODE'`で絞り込むとrole='note'/route='NOTE'のノートが一切ヒットしない)が実データで実際に再現したため、次の対応を実装した:

- `memory_store.retrieve()`の`route`引数を、単一文字列に加えて**tuple/listも受け付ける**よう拡張した
  (`route`が文字列なら`route = '<route>'`、tuple/listなら`route IN ('a', 'b')`のWHERE句を組み立てる)。
  D:側実体・C:側vaultバックアップの両方に反映済み。
- `support_ai_auto_pipe.py`の`_recall()`で、CODEルートの検索を`route="CODE"`から
  **`route=("CODE", "NOTE")`**に変更した(単一route・他の複数route指定への互換は`retrieve()`側で維持)。
- 既存のユニットテスト`test_support_ai_auto_pipe_memory.py`の
  `test_code_route_retrieves_filtered_and_context_is_appended_not_overwritten`を、
  期待値`"CODE"`→`("CODE", "NOTE")`に更新した。`pytest tests/ -q`は**80 passed**を維持。

### 結果 / 分析 / 改善策

#### 結果:4問の`retrieve()`単体検証(route絞り込みなし、`limit=5`)

| # | 質問 | 想定route | 最上位distance | 正解チャンクの有無(上位5件以内) |
|---|---|---|---|---|
| 1 | チャンク分割の上限値は何字に決めた? | FAST | 0.2788 | ○(400字/オーバーラップ80字の記述を含むチャンクが1〜2位) |
| 2 | Ruri v2で意味が合致したときのdistanceはいくつだった? | FAST | 0.2464 | ○(「400字、オーバーラップ80字」を含むチャンクが2〜3位。0.20〜0.25の直接記述チャンクも上位圏内) |
| 3 | マツコ問題の最終的な原因は何だった? | DEEP | 0.2130 | ○(「⑧-3」「⑧-4」の該当見出しチャンクが1・2位) |
| 4 | CODE_TRIGGERSを語幹で区切るようにした理由は? | CODE | 0.3251(`route='CODE'`単独絞り込み時) | **×**(会話ログのみヒットし、正解を含むノートチャンクが一切出てこない) |

4問とも`max_distance=0.45`の足切りには掛からず(全て0.21〜0.33の範囲)、目標を満たした。ただし4問目は
「CODEルート絞り込みの設計上の衝突」がそのまま再現し、`route='CODE'`のみに絞ると過去の会話ログ
(役に立たない断片)しか返らず、正解を含むノートチャンク(distance 0.2101〜0.2284)は一切候補に入らなかった。

#### 結果:CODE+NOTE絞り込み実装後の再検証(4問目)

`route=("CODE", "NOTE")`に変更した`retrieve()`で同じ質問を再実行したところ、正解を含むノートチャンク
(「⑤で見つかった『バグも直して』『実装しといて』のような自然な口語表現がCODEトリガーの正規表現に
一致しない問題を修正した」等、distance 0.2101〜0.2284)が上位5件すべてを占めるようになった。
設計上の衝突は解消したと判断する。

#### 結果:Pipe経由(実際のRouter分類+実モデル応答)で4問実行

| # | 質問 | 実際のroute分類 | 応答に正解が含まれるか |
|---|---|---|---|
| 1 | チャンク分割の上限値は何字に決めた? | FAST | ○(「MAX_CHARS=400字」と明確に回答) |
| 2 | Ruri v2で意味が合致したときのdistanceはいくつだった? | FAST | **△**(架空のcosine類似度表を先に提示し「0.13」と誤答した後、文末近くで「関連語程度なら0.20〜0.30」と正解に近い数値にも言及。ノート記載の正確な値0.20〜0.25そのままの引用はできていない) |
| 3 | マツコ問題の最終的な原因は何だった? | FAST | ○(⑧-3のセッション汚染、⑧-4の想起質問汎化問題の両方を正しく要約) |
| 4 | CODE_TRIGGERSを語幹で区切るようにした理由は? | FAST | ○(「バグも直して」等の口語の活用揺れを拾うため、という趣旨で正しく回答) |

4問とも想定route(FAST/FAST/DEEP/CODE)とは異なり**すべてPhi-4-mini(`gemma4-e4b-cpu`)にFASTと分類された**。
質問4はCODEを想定していたが実際はFASTだったため、上記のCODEルート絞り込み問題は**この検証では
発火しなかった**(FASTはroute絞り込みなしで検索するため、CODE/NOTE云々に関係なく正解に到達できた)。
ただし①`retrieve()`単体検証で問題自体は実データで再現していること、②将来的にCODE分類される質問
(実際のコード修正依頼等)でノートを参照する場面は起こり得ることから、対応自体は先んじて実装しておく
価値があると判断した。

#### 分析

1. **「登録はできているが参照できない」という6日目⑧-1型の事故は、ノート取り込み後も発生していない**。
   4問とも`retrieve()`単体・Pipe経由の両方で正解に到達しており、完了条件
   「ノートにしか書かれていない情報を、Pipe経由の質問で引き出せる」は満たしたと判断する。
2. **CODEルートのroute絞り込み問題は実データで確定的に再現し、対応を実装した**。`route IN ('CODE', 'NOTE')`
   への変更で解消を確認済み(上記)。この変更にともないユニットテストも更新し、`pytest tests/ -q`は
   80件全件合格を維持している。
3. **`max_distance=0.45`は「関係ない質問を弾く」役割をほぼ果たせていないことが判明した**(P2の再評価結果)。
   「今日の晩ご飯は何がいい?」「宇宙の年齢は何歳ですか?」「おすすめの映画を教えて」という
   **このプロジェクトと明確に無関係な質問**でも、上位ヒットのdistanceは0.32〜0.44と、正解質問群
   (0.21〜0.33)と大きく重ならない帯域には収まらなかった(0.32〜0.33付近で重複)。特に「宇宙の年齢」
   は0.4252〜0.4415と0.45にかなり近い値まで出ており、**別の話題のノートがたまたま「無関係な参考情報」
   としてプロンプトに混入するリスクが、ノート取り込み前より明確に高まっている**。
   これは6日目④で「サンプル数が少なく判断材料が乏しかった」ため`max_distance=0.45`を暫定値として
   据え置いていたものだが、ノートが777チャンク追加された今、**0.45は緩すぎる可能性が高い**。
4. **FASTモデル(ルーターと同一の`gemma4-e4b-cpu`)が、正しい文脈を検索で拾えていても数値を
   ハルシネートするケースが確認された**(質問2)。検索結果(`format_context`)には「400字、
   オーバーラップ80字」という記述チャンクは含まれていたが、「Ruri v2のdistance実測値」そのものを
   明記したチャンクが上位に来ていなかった可能性があり、モデルが一般知識のcosine類似度の相場観で
   補完してしまったと考えられる。**検索精度(どのチャンクが上位に来るか)の問題と、モデルの応答生成
   (検索結果を正確に引用できるか)の問題は別軸であり、後者は今回はじめて実害として観測できた**。

#### 結果:Open WebUI実機での確認(ユーザー実施)

「C.L.A.I.R.E. (Auto)」を選び、質問1「チャンク分割の上限値は何字に決めた?」を実際にOpen WebUIの
チャット画面から送信して確認した。

- **応答時間**: 20秒以内
- **応答内容**: 「チャンク分割の上限値は 400字 に決めています。`rag_memory/scripts/chunker.py` では
  `MAX_CHARS = 400` を定数として使い、見出し付きでもほぼこの長さを超えないようにしています。」
  → 数値(400字)・変数名(`MAX_CHARS`)ともに正確で、ハルシネーションなし。
- 本セッションの`smoke_test_pipe.py`相当の直接呼び出しで得た応答(「MAX_CHARS=400字」)と実質同内容であり、
  **Open WebUIを経由しても「Pipe単体呼び出しでの検証結果」と同じ挙動が再現する**ことを確認した。

これにより⑤の完了条件・作業内容チェックリストの最後の1項目(Open WebUI実機確認)を満たした。
**⑤は全項目実施完了**。

#### 改善策

- **`max_distance`を0.45→0.35前後に厳格化することを次回以降で検討する**。ただし0.32〜0.33付近で
  正解/無関係が重なっているため、単純な閾値変更だけでは完全に分離できない。実運用でノイズが
  問題になった際に、閾値変更・システムプロンプトでの「関係ない場合は無視してよい」の強調・
  検索結果の再ランキングなど複数の対策候補から選ぶ(残課題へ記録)。
- **検索結果の直接引用を促すプロンプト改善**(質問2のハルシネーション対策)。`format_context()`の
  冒頭指示文(「関連する場合のみ利用してください」)に加えて、数値・固有名詞は検索結果の記載を
  そのまま使うよう指示を強化する余地がある(残課題へ記録)。

---

## ⑥ 課題4:Cloudflare Tunnelのセットアップ

### 背景/目的

ロードマップ「次のステップ 5.」。現状C.L.A.I.R.E.は自宅PCの`localhost:8080`でしか使えず、外出先やスマホから使えない。Cloudflare Tunnelを使えば**ルーターのポート開放なしに**外部からアクセスできる。

> [!danger] 認証なしの公開は絶対にしないこと(本セクション最重要)
> C.L.A.I.R.E.のCODEルートには、**任意のPythonファイルを作成してその場で実行する機能**
> (4日目⑩のACTIONブロック / `code_executor.py`)がある。これを認証なしでインターネットに
> 公開することは、**自宅PCに対する任意コード実行の踏み台を全世界に開放する**ことと同義である。
> URLが推測されにくいから大丈夫、という考えは通用しない(クローラーは常時探索している)。
> **Cloudflare Accessによる認証を先に設定し、認証なしで到達できないことを検証してから
> 実際に使い始める**。この順序を逆にしない。

### 選択肢の比較

| 方式                                | ポート開放  | 固定URL       | 認証               | 費用  | 判定                          |
| --------------------------------- | ------ | ----------- | ---------------- | --- | --------------------------- |
| A. ルーターのポート開放                     | **必要** | IP次第        | 自前               | 無料  | ✗ 自宅IPが晒され、ルーターの設定ミスが致命傷になる |
| B. ngrok(無料)                      | 不要     | ✗(起動ごとに変わる) | 有料機能             | 無料〜 | △ URLが毎回変わり実用しづらい           |
| C. **Cloudflare Tunnel + Access** | 不要     | ○(独自ドメイン)   | **○(Google認証等)** | 無料枠 | **◎ 採用**                    |

Cloudflare Tunnelは**アウトバウンド接続のみ**でトンネルを張るためルーター側の穴あけが不要で、かつCloudflare Access(Zero Trust)で**Cloudflare側で認証を弾いてから**自宅に転送できる。認証を通らないリクエストはそもそもPCに到達しない、という点がセキュリティ上で決定的に有利。

### 前提条件(着手前に必要なもの)

- [ ] Cloudflareアカウント(無料)
- [ ] Cloudflareで管理しているドメイン(独自ドメインが必要。未所持なら取得から)
- [ ] Open WebUIが`localhost:8080`(実際のポートは要確認)で起動していること

### `cloudflared`が「認識されません」エラーの原因と対応(着手時に発生した不具合)

**症状**: `winget install --id Cloudflare.cloudflared`実行後、続けて`cloudflared tunnel login`を実行すると

```
cloudflared : 用語 'cloudflared' は、コマンドレット、関数、スクリプト ファイル、または操作可能な
プログラムの名前として認識されません。
```

で失敗する。

**原因調査**:
- `winget list --id Cloudflare.cloudflared` → **インストール自体は成功していた**(`2026.7.3`)
- 実体を検索 → `C:\Program Files (x86)\cloudflared\cloudflared.exe`に存在
- レジストリ上のMachine PATH環境変数 → `C:\Program Files (x86)\cloudflared\`が**既に追加済み**
- つまり「インストールもPATH追加も正常に完了しているが、`winget install`実行前から開いていた
  PowerShellウィンドウは、起動時に読み込んだ古いPATH(cloudflaredを含まない)をプロセス内に
  保持し続けている」ことが原因。Windowsの環境変数はプロセス起動時にコピーされるため、
  レジストリを更新しても**既に開いているウィンドウには自動反映されない**(一般的なWindowsの仕様)。

**対応**: 該当PowerShellウィンドウを閉じて**新しいPowerShellウィンドウを開き直す**(または`$env:Path`を
手動で再読み込みする)ことで解消することを確認済み。以降のコマンドは**新しいウィンドウ**で実行すること。

```powershell
# その場しのぎで今のウィンドウのまま続けたい場合(次回以降ウィンドウを開き直せば不要):
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")
cloudflared --version   # 動作確認
```

### 実施手順(予定)

```powershell
# 1. cloudflaredのインストール(winget)
winget install --id Cloudflare.cloudflared
# ⚠️ インストール後は必ずPowerShellウィンドウを開き直してから以降を実行すること(上記参照)

# 2. Cloudflareアカウントへログイン(ブラウザが開く)
cloudflared tunnel login

# 3. トンネルを作成(名前は任意。ここではclaire)
cloudflared tunnel create claire

# 4. DNSレコードを作成(claire.<自分のドメイン> で到達できるようにする)
cloudflared tunnel route dns claire claire.example.com

# 5. 設定ファイル(config.yml)を作成
#    %USERPROFILE%\.cloudflared\config.yml
#    ---
#    tunnel: <トンネルID>
#    credentials-file: C:\Users\gakuh\.cloudflared\<トンネルID>.json
#    ingress:
#      - hostname: claire.example.com
#        service: http://localhost:8080
#      - service: http_status:404
#    ---

# 6. トンネルを起動(動作確認)
cloudflared tunnel run claire

# 7. 常駐化(Windowsサービスとして登録。PC起動時に自動で繋がる)
cloudflared service install
```

**Cloudflare Access(認証)の設定**(ダッシュボード操作のため手順は結果欄に記録する):

1. Cloudflare Zero Trust ダッシュボード → Access → Applications → Add an application
2. Self-hosted を選び、ドメインに`claire.example.com`を指定
3. Policy: Action=Allow、Include=**Emails →`gakuhari555@gmail.com`**(自分だけに限定)
4. 保存後、**シークレットウィンドウ(未認証状態)でアクセスし、Googleログイン画面が出て弾かれることを確認**

### 作業内容

- [x] cloudflaredをインストールする(`winget install --id Cloudflare.cloudflared`で成功。PATH未反映エラーの詳細は上記参照) / [ ] トンネルを作成する
- [ ] `config.yml`を作成し、`localhost:8080`へのingressを設定する
- [ ] トンネルを起動し、外部から到達できることを確認する
- [ ] **Cloudflare Accessを設定し、自分のGoogleアカウントのみ許可する**
- [ ] **未認証(シークレットウィンドウ)でアクセスし、弾かれることを検証する** ← 最重要
- [ ] スマホのモバイル回線(自宅Wi-Fiを切った状態)から実際に会話できることを確認する
- [ ] Windowsサービスとして常駐化する
- [ ] (任意)Open WebUIの「タスクモデル」を`phi4-mini-cpu`等へ固定する(6日目⑨の最適化課題)

### セキュリティ上の残論点(実施時に必ず判断する)

- **CODEルートの外部からの利用可否**:認証を掛けても、スマホから誤って「ファイルを作って実行して」と頼めば自宅PCでコードが動く。`code_execution_mode`(Valves)を外出時はOFFにする運用にするか、そもそも外部からはCODEを無効化するか、方針を決める
- **Open WebUI自体のアカウント**:Cloudflare Accessを通った先で、Open WebUIのログインも有効になっているか確認する(二重の防御)
- **トンネルの常時起動**:サービス常駐させると、PCが起動している限り外部から到達可能な状態が続く。意図的にOFFにする手順も記録しておく

### 結果 / 分析 / 改善策

**中断:Cloudflare Tunnelの固定URLは「Cloudflareで管理する独自ドメイン」が前提であり、ドメイン取得には別途費用(年数百〜千数百円)が発生することが判明した。** 本セクション冒頭の比較表「費用:無料枠」はCloudflare Tunnel/Access**サービス自体**の利用料のみを指しており、ドメイン代を含んでいなかった(表の不備)。今回は**完全0円**を制約とするため、Cloudflare Tunnel採用を見送り、代替案を調査した。

#### 代替案調査:無料・セキュア・ポート開放不要・固定URLの4条件

| 方式                                     | 費用                          | ポート開放 | 固定URL                               | セキュリティ                                                                                   | 備考                                                              |
| -------------------------------------- | --------------------------- | ----- | ----------------------------------- | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| A. Cloudflare Tunnel(独自ドメイン)           | 有償(ドメイン代)                   | 不要    | ○                                   | ○(Access認証)                                                                              | **不採用**。0円制約に反する                                                |
| B. **Tailscale Serve**(非公開・tailnet内のみ) | **0円**(無料枠:6ユーザーまで、デバイス無制限) | 不要    | ○(`https://端末名.tailXXXX.ts.net`、固定) | **◎ 最も安全**。インターネットに一切公開されず、Tailscaleアプリでログインした自分の端末からしか到達不可。CODEルート公開という最重要リスクが構造的に発生しない | スマホ側に**Tailscaleアプリのインストールとログインが必要**(ブラウザ単体では不可)                |
| C. Tailscale **Funnel**(公開)            | 0円                          | 不要    | ○(同上のURLで公開)                        | △。インターネット全体に公開されるため、アプリ側(Open WebUI)の認証が別途必須                                             | ブラウザのみでアクセス可(アプリ不要)。対応ポートは443/8443/10000のみ                      |
| D. ngrok(無料static domain + OAuth)      | 0円                          | 不要    | ○(`任意名.ngrok-free.app`、固定)          | ○(無料枠でもGoogle/GitHub OAuthが利用可)                                                          | 帯域1GB/月・20,000リクエスト/月・同時3エンドポイントの制限あり。チャット用途(画像添付等)で超過する可能性は要検証 |

#### 結論・推奨

**Tailscale Serve**を次の採用候補とする。

- 費用0円(個人利用なら永久無料)
- ポート開放不要(WireGuardベースのメッシュVPN、アウトバウンド接続のみ)
- 固定URL(`https://<端末名>.<tailnet名>.ts.net`は変わらない)
- セキュリティはCloudflare Tunnel+Access以上:そもそも公開ゾーン(trycloudflare/ngrok-free.app等)にすら露出しないため、CODEルート踏み台化のリスクが構造的にゼロに近い
- 代償:スマホに「ブラウザだけ」ではなく「Tailscaleアプリを入れてログイン」という一手間が増える。ただし外出先利用は自分のスマホからのみが前提のため実運用上の影響は小さい

次点は**ngrokの無料static domain + Google OAuth**(ブラウザのみでアクセスしたい場合)。ただし帯域上限(1GB/月)がチャット用途で足りるか未検証。

#### 残課題(次回実施)

- [ ] Tailscaleアカウント作成・自宅PC/スマホへのインストール
- [ ] `tailscale serve https / http://localhost:8080` で`localhost:8080`を公開し、スマホから`https://<端末名>.<tailnet名>.ts.net`で到達できるか確認
- [ ] Tailscale側のACL/デバイス承認設定を確認し、想定外の端末が入り込まない構成になっているか検証
- [ ] 本セクション(⑥)の「実施手順」「作業内容」チェックリストをTailscale版に書き換える(Cloudflare Tunnel前提のまま残っているため)

Sources:
- [tailscale funnel command · Tailscale Docs](https://tailscale.com/kb/1311/tailscale-funnel)
- [Free pricing plans and discounts · Tailscale Docs](https://tailscale.com/docs/account/manage-plans/free-plans-discounts)
- [Static domains for all ngrok users - ngrok](https://webflow.ngrok.com/blog-post/free-static-domains-ngrok-users)
- [OAuth Action - ngrok documentation](https://ngrok.com/docs/traffic-policy/actions/oauth)

---

## ⑦ 実装の成果物一覧(実行後に確定)

### プログラムファイル

| ファイル | 役割 | 実行方法 | 出力先 |
|---|---|---|---|
| `rag_memory/scripts/chunker.py`(修正) | ③:機械分割時に見出し行を再付与。チューニング値を`config.yaml`から読む | 単体実行はしない(`memory_store.py`/`ingest_notes.py`からimportして使う部品) | なし(戻り値のみ) |
| `rag_memory/scripts/ingest_notes.py`(新規) | ④:Obsidianノートを記憶DBへ取り込む(冪等・upsert相当) | `python ingest_notes.py [--dry-run] [--path <vault相対パス。ファイル/ディレクトリどちらも可>]` | LanceDB(`config.yaml`の`db_path`) |
| `rag_memory/scripts/memory_store.py`(修正) | ⑤:CODEルートの`route`絞り込みを`NOTE`も含むよう変更(検証結果次第) | 単体実行はしない | LanceDB |
| [[サポートAI作製計画/scripts/openwebui_pipe/support_ai_auto_pipe.py]](修正) | ⑤:CODEルートの検索絞り込み変更に伴う`_recall()`の調整(必要な場合のみ) | Open WebUIのFunctionsへ貼り付け | Open WebUIのチャット画面/LanceDB |
| `tests/test_chunker.py`(新規または修正) | ③:見出し再付与のユニットテスト | `python -m pytest tests/ -q` | 標準出力のみ |
| `tests/test_ingest_notes.py`(新規) | ④:frontmatter除去・除外ディレクトリ判定のユニットテスト | `python -m pytest tests/ -q` | 標準出力のみ |
| (⑤の検証用スクリプト・実行後に確定) | ノートにしか無い情報を引き出せるかの自動検証 | (実行後に記入) | 標準出力のみ |

### 設定ファイル

| ファイル | 役割 | 備考 |
|---|---|---|
| `rag_memory/config.yaml`(修正) | ②③:`max_chars`/`overlap`/`max_distance`を実際に追加する(6日目⑥では「追加した」と書かれていたが未実施だった)。**④:`vault_root`/`ingest_default_path`を追加**(`ingest_notes.py`がD:側実行からC:側vaultのノートを見つけるために必要になった。設計時点では未定義だった論点) | C:側バックアップとD:側実体の**両方**を更新すること |
| `%USERPROFILE%\.cloudflared\config.yml`(新規) | ⑥:Cloudflare Tunnelのingress設定 | **vault外**。認証情報(`<トンネルID>.json`)は**絶対にvaultへコピーしない**(Obsidian同期でクラウドに載るため) |

---

## ⑧ 動作確認手順(実施予定)

- [x] ②:`config.yaml`の食い違い解消・取り込み前の行数記録
- [x] ③:`python -m pytest tests/ -q`が全件合格(6日目時点で72件。追加分を含めて増えているはず)→ **80 passed**
- [x] ④:`python ingest_notes.py --dry-run` → 対象ファイル数・チャンク数を確認 → **11ファイル / 777チャンク**
- [x] ④:`python ingest_notes.py` 本実行 → 件数・所要時間を記録 → **29→806件(+777) / 27.5秒**
- [x] ④:**もう一度**`python ingest_notes.py`を実行 → 行数が変わらない(冪等)ことを確認 → **806件のまま**
- [x] ⑤:検証用4問を`retrieve()`単体で検索 → 正しいチャンクがヒットし、distanceが0.45以内 → **全問0.21〜0.33で足切りされず**(CODEのみ絞り込みだと4問目は不通過。CODE+NOTE対応で解消)
- [x] ⑤:Pipe経由で4問 → 応答にノート由来の正解が含まれる → **3問○・1問△(数値のハルシネーションあり、詳細は⑤結果参照)**
- [x] ⑤:Open WebUI実機で1問 → 実機で成立 → **ユーザー実機確認で成功**(質問1「チャンク分割の上限値は何字に決めた?」、20秒以内に「400字(`MAX_CHARS = 400`)」と正確に回答)
- [ ] ⑥:`cloudflared tunnel run claire` → 外部から到達できる
- [ ] ⑥:**シークレットウィンドウで未認証アクセス → 弾かれる**(最重要)
- [ ] ⑥:スマホのモバイル回線から会話できる

---

## ⑨ 残課題・次回への持ち越し(記入用)

- **`max_distance=0.45`の厳格化**(⑤で判明):ノート取り込み後、明確に無関係な質問(「宇宙の年齢は?」等)でも
  distance 0.32〜0.44でヒットしてしまい、正解質問群(0.21〜0.33)との分離が甘い。0.35前後への変更を
  次回検討する(単純な閾値変更だけでは0.32〜0.33付近の重複を解消しきれない点に注意)
- **検索結果のハルシネーション対策**(⑤で判明):FASTルート(`gemma4-e4b-cpu`)が、検索で正しい文脈を
  拾えていても数値を答える際に一般知識で補完してしまうケースを確認(Ruri v2のdistance実測値を
  「0.13」と誤答)。`format_context()`の指示文強化などで、検索結果の直接引用を促す改善を検討する
- CLARIFYの「粘着」対策:`build_user_prompt`が`last_route="CLARIFY"`を渡すとPhi-4-miniがCLARIFYを維持し続ける(6日目⑧-2で発見・本日は保留と判断)
- Ruri v2 → v3-310m 差し替え検証(768次元で同一。接頭辞の変更を忘れないこと。本日は保留と判断)
- RECALL_TRIGGERSの網羅性(6日目⑧-4):実運用で誤判定を見つけ次第、正規表現を追加する運用課題
- 記憶の肥大化対策(要約圧縮・削除ポリシー)の実装:**ノート取り込みで件数が一気に増えるため、6日目時点より現実味が増す**。取り込み後の行数を見て着手時期を判断する
- Profile層(`user_profile`テーブル)の実装(5日目⑩・6日目⑦の設計のみ済み)
- コードブロックを分断しないチャンク分割(6日目③からの持ち越し。④の実データで「関数定義の途中で
  チャンクが切れる」ケースが再現することを確認済み。⑤の検索精度検証で実害が出るか見てから対応要否を判断)
- **クロスOS対応**(②で書き換え):`db_path`のWindows固有表記をやめ、環境変数やOS判定で解決する。着手条件=実際に別OSから使う必要が生じたとき
- 外出時のCODEルート(コード実行)の扱い方針(⑥のセキュリティ残論点)
- ノート再取り込みの運用方針:現状は`ingest_notes.py`を毎回全量再実行する前提(冪等なので害はないが、
  ノート数が増えると所要時間も伸びる)。ファイル更新日時を見て差分だけ取り込む等の最適化は、
  実運用で所要時間が気になり始めてから着手する運用課題として残す
- (実行後に追記)

---

## 📌 次のステップ

1. ~~4モデルをOllamaで実際にダウンロード・動作検証~~
2. ~~Phi-4-miniの振り分けロジック(プロンプト設計・実装)~~
3. ~~RAGの記憶DB構築(土台)~~ → [[サポートAI作製計画/5日目RAG記憶DB構築.md|5日目]]
4. ~~C.L.A.I.R.E. (Auto) Pipeへの記憶レイヤー組み込み(検索+書き戻し)~~ → [[サポートAI作製計画/6日目RAG記憶レイヤーのPipe組み込み.md|6日目]]
5. Obsidianノートの取り込み(ingest) ← 今回
6. Cloudflare Tunnelのセットアップ ← 今回
7. STT/TTSパイプラインの組み立て
