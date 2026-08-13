---
project: C.L.A.I.R.E.(さぽーとAI)
date: 2026-08-12
tags: [Web検索, UIデザイン, マルチモーダル, 画像, PDF, Word, Excel, PowerPoint, エンドツーエンド遅延, 作業ログ]
status: 進行中(①はステップ0〜6完了・実機テストでgpt-oss:20bのCUDAクラッシュとCLIベンチとの乖離を発見し継続調査中。②③は未着手。④はPDF/Word/Excel/PowerPoint対応を実装済み。④-1で画像もgemma4:26b(DEEP)がvision対応済みと実測確認、専用モデル追加は不要と判明。④-2で「画像添付時はDEEPへ強制ルーティング」を実装完了(router.py/ollama_client.py/support_ai_auto_pipe.py/voice_gateway.py/static/index.html、単体テスト205件成功)、実機確認は未実施)
---

[[サポートAI作製計画/10日目ウェイクワード・キーボード入力対応.md|10日目]]で①③③'⑦(常時プレビュー+手動送信方式への設計変更)まで実機確認が完了し、[[サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md|9日目]]⑧「エンドツーエンド遅延の実測」が積み残しのまま残っている。11日目の今日は、①9日目⑧の遅延実測を消化しつつ、②クレアがWeb検索できるようにする、③自作UI(OpenWebUI相当の機能を備えたデザイン)の方向性を確定する、④現状のシステムがマルチモーダル(画像・PDF)に対応できているかを調査し対策を講じる、の4本立てで進める。

> [!note] このノートの位置づけ
> [[サポートAI作製計画/ノート作成規則.md]]に従い、**着手前に作業内容を列挙した計画ノート**として作成した。実行後に「結果」「分析」「改善策」を各セクションへ追記していく。
> ただし④(マルチモーダル対応可否)は、本ノート作成時点でコードベースを実際に調査済みのため、**結果・分析まで先行して記載**している。対策(改善策)の実装はこれから。

---

## ① 9日目⑧の持ち越し:エンドツーエンド遅延の実測とCLARIFY / RAGの挙動確認

### 背景/目的

[[サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md|9日目]]⑧で「UIができて初めて実測できる」として計画したまま未着手だった項目。8日目では「TTS工程だけで約1分」という支配的な遅延を観測したが、①〜⑦の改善(ストリーミング化・文単位パイプライン化・自前UI完成)が実際に効いたかを**工程別に分解した数値で証明する**必要がある。10日目で自前音声UIの主要機能(常時プレビュー+手動送信・マイクミュート延長・停止ボタン)の実機確認が一通り終わったため、着手条件が揃った。

### 作業内容

- [x] 発話終了 → STT確定 → ルーター判定 → RAG検索 → LLM初トークン(TTFT) → 最初の文の確定 → 最初のwav完成 → 再生開始、の**工程別所要時間をログに出す**(`bench_e2e_latency.py` + `ws_e2e_bench.py`)
- [x] ルート別(FAST / DEEP / CODE)に測り、**表でファイルに保存する**(`results/e2e_2026-08-12.json`。実機での`ws_e2e_bench.py`計測は現時点でFASTルートのみ、残課題参照)
- [ ] 8日目の「約1分」と比較し、**改善幅を数値で示す**(最重要)(CLIベンチ上は改善しているが、結果3で実機の方が大幅に遅い・不安定なことが判明したため「改善した」と断定するのはまだ早い。分析8参照)
- [ ] どの工程が支配的かを特定し、次に手を入れるべき箇所を決める(CLIベンチではルーターが支配的だったが、結果3でLLM生成側の異常な遅さ・CUDAクラッシュも見つかり再検討が必要)
- [ ] 音声でCLARIFYルートを踏んだときの挙動を確認する([[サポートAI作製計画/4日目Phi4ロジック設計.md]]の「続けて応答」ボタンの懸念)
- [ ] CLARIFYの「粘着」問題([[サポートAI作製計画/6日目RAG記憶レイヤーのPipe組み込み.md|6日目]]⑧-2)が音声で実害を出さないか確認する
- [ ] 音声入力時にRAG記憶レイヤーが正常に働くか確認する(STT誤変換が検索ヒット率を落とさないか。[[サポートAI作製計画/7日目Obsidianノート取り込みとCloudflareTunnel.md|7日目]]⑤の`max_distance=0.45`問題とあわせて見る)
- [ ] `RouterSession`がプロセス別になる問題を実機で確認する
- [x] VRAM競合の3者調整を実測する(LLM / STT / TTSを同時に動かして16GBに収まるか)(結果1のVRAM監視。ピーク15843MiB、OOMなし)

### 実施手順(予定)

9日目⑧に記載した想定計測表をそのまま使う。

| 工程                     | FAST(秒) | DEEP(秒) | 備考                |
| ---------------------- | ------- | ------- | ----------------- |
| 発話終了 → VAD検出           |         |         |                   |
| VAD検出 → STT確定          |         |         |                   |
| STT確定 → ルーター判定完了       |         |         | ルーターはCPU固定        |
| ルーター判定 → RAG検索完了       |         |         |                   |
| RAG検索 → LLM初トークン(TTFT) |         |         |                   |
| 初トークン → 最初の文が確定        |         |         |                   |
| 文確定 → wav完成            |         |         |                   |
| **合計(発話終了 → 再生開始)**    |         |         | **8日目は約1分。目標は数秒** |

### 作業フロー(2026-08-12改訂: ステップ2のエラーを修正、8行の表を誰がどう埋めるかを明確化)

既存 `voice_gateway.run_turn()` を再利用する形で AI 側パイプラインの遅延を工程別に測る。STT は省略してテキスト入力で直接 `run_turn()` を駆動する(STT 単体の遅延は `stt_bench.py` で別途測る)。

> [!warning] 旧版からの変更点(このステップ2のエラーで判明した事実)
> - `monitor_ollama.py` は `--model`/`--swap-models` と `--prompt`/`--prompt-file` が必須で、引数なしでは `parser.error()` で即終了する(旧ステップ2で踏んだエラーの原因)。加えて、そもそもこのスクリプトは「`ollama run <model>` を1回実行しながらVRAMを監視する」専用ツールで、**3モデル+ルーターを同時に走らせて監視する用途には作られていない**。VRAM競合の確認は、後述のとおり `nvidia-smi` を直接ループさせる方式に変更した。
> - `bench_e2e_latency.py` は `voice_gateway.run_turn()` を**プロセス内で直接import**して呼ぶ(WebSocket/FastAPI経由ではない)ため、**voice_gatewayサーバ(port 5055)の起動はステップ4では不要**。5055番が要るのはステップ5(ブラウザ実機クロスチェック)だけ。
> - `ollama serve` を打って `bind: Only one usage of each socket address...` が出た場合は「Ollamaは既に起動済み」という意味であり、それ以上の対応は不要(Windows版はインストール直後からタスクトレイに常駐している)。
> - 11日目時点で「未実装」としていたルーター判定/RAG検索の個別計測は、`bench_e2e_latency.py` に `router.RouterSession.get_route()` / `memory_store.retrieve()` をmonkeypatchする形で実装済み(下記ステップ0参照)。

- [x] **ステップ0: 新規スクリプト作成・改修**
  - `scripts/bench_e2e_latency.py` を新規作成(既存 `voice_gateway.run_turn()` を再利用)
  - 計測区間: T_total / T_first_token / T_first_sentence / T_first_audio
  - 派生区間: `seg_router`(ルーター判定) / `seg_rag`(RAG検索) / `seg_ttft_only`(純粋なLLM初トークン) / `seg_sentence_buffer`(文バッファリング) / `seg_first_tts`(最初のTTS) / `seg_rest`(残り)
  - 起動前チェック(`preflight_check()`): Ollama(`/api/tags`)・VOICEVOX(`/speakers`)に軽くHTTPを叩き、落ちていれば分かりやすいメッセージで停止する(`--skip-preflight`で無効化可)
- [ ] **ステップ1: 必要なサーバの起動確認(このステップ4では2つだけでよい)**
  1. Ollama: `curl http://127.0.0.1:11434/api/tags` が200を返せばOK。返らない場合だけ `ollama serve`(通常は既にタスクトレイで起動済みのはず)
  2. VOICEVOX ENGINE: `run.exe` 等でエンジンを起動 → `curl http://127.0.0.1:50021/speakers` で確認
  3. `python bench_e2e_latency.py` 自体が起動時に上記2つを自動チェックするので、手動curlは省略してスクリプトのpreflightメッセージに任せてもよい
  - ※ voice_gatewayサーバ(port 5055)はここでは起動しない(ステップ5で使う)
- [ ] **ステップ2: VRAM競合の監視を開始する(ステップ4と並行して回す)**
  - 別のターミナルを開き、以下でnvidia-smiを1秒間隔でCSVに追記し続ける(`monitor_ollama.py`は使わない):
    ```
    nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv,noheader -l 1 > results\vram_watch_2026-08-12.csv
    ```
  - このターミナルは**ステップ4が終わるまで起動したまま**にしておく(FAST/DEEP/CODEでモデルスワップが起きるタイミングのVRAMピークを漏れなく拾うため)
  - ステップ4完了後に Ctrl+C で止め、`memory.used` の最大値が `memory.total`(16GB=16384MiB付近)を超えていないか確認する
- [ ] **ステップ3: 単体基準値を取る**
  - `python tts_latency_bench.py --engine-url http://127.0.0.1:50021 --speaker 107`(TTS 単独の遅延。`--engine-url`/`--speaker`は必須引数のため省略不可。話者107=東北ずん子ノーマル)
  - `python stt_bench.py --audio results/stt_bench/sample01.wav`(STT 単独の遅延。実際のサンプル音声が必要)
- [ ] **ステップ4: エンドツーエンド計測(ステップ2のnvidia-smiループを回したまま実行)**
  - `python bench_e2e_latency.py --out "results/e2e_2026-08-12.json"` を実行
  - ルート別(FAST/DEEP/CODE/CLARIFY)× 各2問を順次実行し、`seg_router`/`seg_rag`/`seg_ttft_only`/`seg_sentence_buffer`/`seg_first_tts`が標準出力とJSONの両方に出る
- [ ] **ステップ4.5: VRAM監視を停止し、ピークを確認**
  - ステップ2のターミナルで Ctrl+C → `results/vram_watch_2026-08-12.csv` を開き `memory.used` の最大値を確認(16GB超なら競合あり)
- [x] **ステップ5: ブラウザ実機でのクロスチェック(ここで初めてvoice_gatewayサーバが必要)**
  - `python voice_gateway.py --host 127.0.0.1 --port 5055` を起動(このステップでのみ必要)
  - Chromeで `http://127.0.0.1:5055/` を開き、DevTools の Network タブで WS `/ws` の送受信タイムスタンプを読む
  - 「発話終了→VAD検出」「VAD検出→STT確定」の2区間(表の1・2行目)は、`bench_e2e_latency.py`がテキスト入力を使う設計上ここでしか測れないため、**このステップの実機計測が唯一の取得手段**
  - CLI版(ステップ4の結果)と突合
  - > [!note] 2026-08-12追記: このステップを自動化する `ws_e2e_bench.py` を追加した
    > 「Chromeで実際に喋ってDevTools Networkタブを目視で読む」手作業の代わりに、
    > `python ws_e2e_bench.py --audio results/stt_bench/sample01.wav`(サーバは事前に
    > `voice_gateway.py --port 5055` で起動しておく)で、録音済みwavを実際のマイクと
    > 同じペース(100msチャンク)でWebSocketへ流し込み、STT確定タイミング
    > (`{"type":"partial_transcript","final":true}`。この`"final"`フラグも同日
    > `voice_gateway.py`に追加した)を自動検出して`seg_speech_end_to_stt_final`
    > (=「発話終了→VAD検出」+「VAD検出→STT確定」の合算値。VAD検出単独のタイムスタンプは
    > 存在しないため2区間へは分解できない)を出力する。STT確定後は自動で`text_input`を
    > 送ってrun_turn()完了まで追跡し、CLI版(ステップ4)との突合値も同時に得られる。
    > `--rounds N`で繰り返し平均も取れる。ただし実際のマイクデバイス初期化・
    > ブラウザJS実行コスト・スピーカー再生は含まないため、**厳密な「実機」計測の
    > 完全な代替ではない**(DevTools目視読み取りより高精度・再現性がある代替手段、
    > という位置づけ)。詳細は`ws_e2e_bench.py`冒頭のdocstring参照。テストは
    > `tests/test_ws_e2e_bench.py`(実際にuvicornでテスト用サーバを立て、フェイクの
    > STT/Pipe/TTSに対して疎通確認する統合テスト)。
  - > [!warning] 2026-08-12実機テストで発見した不具合: gpt-oss:20b初回ロード時のCUDAクラッシュ
    > 実際にChromeで「こんにちは」と発話してステップ5を実施したところ、1回目の発話で
    > FASTルート(`gpt-oss:20b`)が **HTTP 500** を返してエラーになった
    > (`[route: FAST] [error] gpt-oss:20b の呼び出しに失敗しました: ... HTTP Error 500`)。
    > `bench_e2e_latency.py`(ステップ4)ではこのエラーが一度も出ていなかった、
    > **実機でしか踏めなかった不具合**。原因調査の詳細と再発防止策は下の「結果3」参照。
    > また、この過程で「DevTools NetworkタブのMessages/Framesを目視で読む」という
    > 当初のステップ5の手順は、ページ全体の読み込み統計(画面下部のFinish/Load等)と
    > 個々のWSメッセージの受信時刻を混同しやすく、**実務上は精度・再現性に乏しい**
    > ことも判明した。そのため表1・2行目の数値は、目視ではなく`ws_e2e_bench.py`の
    > 自動計測値を採用する(「結果3」参照)。
- [x] **ステップ6: 結果の分析と次の一手の決定**
  - `results/e2e_2026-08-12.json` を本ノートの「結果 / 分析 / 改善策」セクションへ転記(下の表の埋め方を参照)
  - 8日目の「約1分」との改善幅をパーセンテージで算出
  - 支配的工程を特定し、本ノートの「残課題」へ次の一手を1〜2行で書く

#### 表(38〜47行目)の8行を誰が埋めるか

| 表の行 | 埋める値の出どころ |
|---|---|
| 発話終了 → VAD検出 | ステップ5(ブラウザ実機・DevTools)でのみ取得可。`bench_e2e_latency.py`は非対応 |
| VAD検出 → STT確定 | 同上(ステップ5)。`stt_bench.py`はモデル比較用のバッチ転写ベンチであり、この区間そのものの計測ではない点に注意 |
| STT確定 → ルーター判定完了 | `bench_e2e_latency.py`の結果JSONの`seg_router` |
| ルーター判定 → RAG検索完了 | 同`seg_rag`(memory_store未接続・対象外routeではNaN) |
| RAG検索 → LLM初トークン(TTFT) | 同`seg_ttft_only`(CODE/CLARIFYはトークンストリーミングしないためNaN) |
| 初トークン → 最初の文が確定 | 同`seg_sentence_buffer` |
| 文確定 → wav完成 | 同`seg_first_tts` |
| **合計(発話終了 → 再生開始)** | ステップ5実機の(VAD検出+STT確定)実測値 + `seg_router`+`seg_rag`+`seg_ttft_only`+`seg_sentence_buffer`+`seg_first_tts`の合算値、または近似として`t_first_audio`(最初の音声チャンク受信時刻)を使う。JSON中の`t_end`(state: idle)は全文生成・全TTS完了までの時間であり「再生開始」とは意味が異なるので混同しないこと |

### 結果 / 分析 / 改善策

#### 結果1: 単体基準値(ステップ3)

**TTS単体**(`tts_latency_bench.py`、話者107、`results/tts_latency/latency_20260812_110505.md`)

| 文字数 | 合成時間(秒) | 再生時間(秒) | 実時間比 | パイプライン成立 |
|---|---|---|---|---|
| 12(warm) | 0.375 | 2.112 | 0.18 | OK |
| 32 | 0.844 | 5.237 | 0.16 | OK |
| 47 | 1.281 | 8.800 | 0.15 | OK |
| 85 | 2.078 | 14.133 | 0.15 | OK |

計測範囲(最大85文字)では実時間比が全て0.15〜0.18で、文単位パイプラインは余裕をもって成立している。

**STT単体**(`stt_bench.py --audio sample01.wav`(7.712秒の発話)、`results/stt_bench/bench_20260812_110001.md`)

| モデル | 所要時間(秒) | 実時間比 |
|---|---|---|
| faster-whisper small | 0.765 | 0.10 |
| faster-whisper medium | 0.625 | 0.08 |
| Kotoba-Whisper v2.0(日本語特化) | 0.250 | 0.03 |

#### 結果2: エンドツーエンド計測(ステップ4、`results/e2e_2026-08-12.json`)

各ルート2問ずつ(round0のみ)実行。行の意味は94〜105行目の対応表のとおり。**「発話終了→VAD検出」「VAD検出→STT確定」の2行はこのCLIベンチでは非対応**(STTを意図的に省略しテキスト入力で直接`run_turn()`を駆動する設計のため)。実機での値は下の「結果3」参照。「合計」は本来「発話終了→再生開始」だが、STT区間を含めない**AI側パイプラインのみの`t_first_audio`(実行開始→最初の音声チャンク受信)**をここでは記載している。

| 工程 | FAST(秒・2問平均) | DEEP(秒・2問平均) | CODE(秒・2問平均) | CLARIFY(秒・2問平均) | 備考 |
| --- | --- | --- | --- | --- | --- |
| 発話終了 → VAD検出 | (結果3参照) | - | - | - | CLIベンチ非対応。ステップ5実機のみで取得可 |
| VAD検出 → STT確定 | (結果3参照) | - | - | - | 同上。参考: STT単体は上表参照 |
| STT確定 → ルーター判定完了(`seg_router`) | **26.31** | **3.09** | **15.11** | **3.27** | ルーターはCPU固定。FAST/CODEでばらつきが大きい(下記分析参照) |
| ルーター判定 → RAG検索完了(`seg_rag`) | 1.67 | 0.06 | 0.17 | NaN(未接続) | CLARIFYは記憶検索(`_recall`)を呼ばないルートのためNaN |
| RAG検索 → LLM初トークン(TTFT)(`seg_ttft_only`) | 11.44 | 6.61 | NaN | NaN | CODE/CLARIFYはトークンストリーミングしないため計測不可 |
| 初トークン → 最初の文が確定(`seg_sentence_buffer`) | 0.10 | 0.13 | NaN | NaN | 同上 |
| 文確定 → wav完成(`seg_first_tts`) | 0.00 | 0.00 | 0.00 | 0.00 | ほぼ0秒。TTS自体は速い(単体基準値と整合) |
| **合計(暫定・`t_first_audio`)** | **39.52** | **9.89** | **45.29** | **53.09** | **8日目は約60秒(TTS単体分だけで)。STT区間抜きでも一部ルートはまだ60秒級。※結果3の通り実機ではさらに悪化する** |
| 参考: 全文完了(`t_end`) | 41.21 | 39.97 | 53.50 | 64.89 | 音声再生「開始」ではなく生成・TTS全完了までの時間 |

VRAM監視(`vram_watch_2026-08-12.csv`、ステップ2〜4.5、10:58:58〜11:15:14の約16分間・968サンプル)の`memory.used`最大値は **15843 MiB**(総VRAM 16303 MiB中、使用率97.2%)。ピークは11:09:02付近(DEEP/CODEのモデル入れ替わりのタイミングと一致)で観測され、**16GBを超えるOOMは発生しなかった**が、残り余裕は約450 MiBとかなり少ない。

#### 結果3: ステップ5実機テストの結果(2026-08-12、`ws_e2e_bench.py`+実際にChromeで発話)

**(a) 実際にChromeで「こんにちは」と発話 → gpt-oss:20bのCUDAクラッシュを発見**

FASTルートへ1回目の発話をした際、`gpt-oss:20b`の呼び出しが**HTTP 500**で失敗した(UIには`[route: FAST] [error] gpt-oss:20b の呼び出しに失敗しました: ... HTTP Error 500`と表示)。Ollamaのサーバーログ(`%LOCALAPPDATA%\Ollama\server.log`)を直接確認したところ、原因はVRAM不足(OOM)ではなく、**CUDAカーネル(`MUL_MAT`)の異常終了によるllama-serverプロセスのクラッシュ**だった:

```
WARN: llama-server discovery: could not determine compute capability for CUDA device
      — architecture filtering disabled for this device. device="NVIDIA GeForce RTX 5070 Ti"
...
ggml_cuda_compute_forward: MUL_MAT failed
CUDA error: shared object initialization failed
...
llama-server terminated: exit status 0xc0000409
  "The system detected an overrun of a stack-based buffer..."
[GIN] 500 | 28.55s | POST "/api/generate"
```

RTX 5070 Ti(Blackwell世代、compute capability 12.0)をOllama(v0.32.9時点)が正しく認識できず「architecture filtering disabled」という警告付きでロードしていることが根本原因と推測される。**VRAM残量には余裕があった**(15037MiB free に対しモデルは12036MiB)ため、97.2%使用という結果1のVRAM逼迫とは別の問題。直後の再ロード試行は成功しており、**初回ロード時のみ不安定になる再現性の低い不具合**という性質が確認できた。

**(b) `ws_e2e_bench.py`による自動計測(本物のVosk/faster-whisper/Ollama/VOICEVOXに対して実施、N=1)**

```
python ws_e2e_bench.py --audio results/stt_bench/sample01.wav --url ws://127.0.0.1:5055/ws \
  --rounds 1 --stt-timeout 20 --turn-timeout 200 --out results/ws_e2e_bench/ws_e2e_2026-08-12_real.json
```

| 区間 | 実測値 | 備考 |
|---|---|---|
| 発話終了(`t_speech_end`) | 8.49s | サンプル音声(7.712秒)の送信完了時刻 |
| STT確定(`t_stt_final`) | 23.76s | `{"final": true}`を検出した時刻 |
| **発話終了→VAD検出→STT確定(合算)** | **15.27s** | ノート表1・2行目に相当する合算値(N=1、単発計測) |
| STT確定後の`run_turn()`完了 | **未完了(`turn_timeout`)** | 200秒待っても`state: idle`が届かなかった |

`voice_gateway.py`に追加した`"final"`フラグは、実際のVosk/faster-whisperスタックに対しても正しく機能していることを確認できた(受信メッセージ14件中、`final: true`が付いたのは1件のみで、そこに正しい確定テキスト「クレアデスC.L.A.I.R.E.ご呼んでください」が乗っていた)。

一方で2つの想定外の事象が見つかった:

1. **暫定認識(Vosk partial)の更新に約11秒の空白があった**(受信ログ上、t=7.98sの次がt=19.06s)。この間サーバー側で何が起きていたかは未特定。GPU競合(Ollamaはこの時点ではまだ呼ばれていないため考えにくい)以外の原因を調べる必要がある。
2. **STT確定後、`text_input`を自動送信してから200秒待ってもrun_turn()が完了しなかった**(`turn_timeout`)。(a)のCUDAクラッシュ後の再ロードでgpt-oss:20bは一応動くようになっていたが、それでも200秒に収まらない極端な遅さが実機経路では再現した。CLIベンチ(結果2)ではFASTルート合計39.52秒だったのと大きく乖離しており、**CLIベンチが実際のブラウザ経由の遅さを過小評価している可能性**がある。

### 分析

1. **TTS工程はもはや支配的ボトルネックではない。** 8日目に観測した「TTS工程だけで約1分」は、今回の単体計測で最大85文字でも2.08秒(実時間比0.15)まで改善しており、9〜10日目のストリーミング化・文単位パイプライン化の効果が数値で裏付けられた。
2. **代わりにルーター判定(`seg_router`)が新たな支配的工程になっている、特にFASTルート。** FASTの`seg_router`平均26.31秒は、そのルートの合計39.52秒の**約67%**を占める。ノート表の注記どおり「ルーターはCPU固定」であるため、CPU推論のフォワードパス自体に時間がかかっていると考えられるが、DEEP(3.09秒)・CLARIFY(3.27秒)は同じCPU固定のはずなのに大幅に短く、CODEはQ1(3.02秒)とQ2(27.20秒)で10倍近いばらつきがある。**「CPU固定だから遅い」だけでは説明がつかない不規則さがあり、原因調査が必要**(ルーターが質問内容によって異なる分岐・追加のLLM呼び出しを行っている可能性、あるいはOllama側のモデルスワップ待ちが`get_route`の計測区間に混入している可能性)。
3. **DEEPルートが実質最速。** DEEPの合計(9.89秒)はFAST(39.52秒)・CODE(45.29秒)・CLARIFY(53.09秒)より大幅に短い。これは`seg_router`が正常な値(3秒程度)に収まっているためで、逆に言えば**ルーター判定さえ正常な速度なら、全体は10秒未満に収まる**ことを示している。次に手を入れるべきは「RAG検索→LLM初トークン」ではなくルーター側。
4. **CODE/CLARIFYはトークンストリーミングしないため、TTFT・文バッファ区間が計測不能(NaN)なまま。** 現状は`t_first_sentence`(≒`t_first_audio`)が「ルーター+RAG+全文生成」を一塊にした値になっており、11日目ノート表の設計(工程別分解)を満たせていない。CODE/CLARIFYも文単位でストリーミングする改修をしない限り、この2ルートの内訳は今後も見えない。
5. **VRAM(97.2%使用)は限界に近いが破綻はしていない。** 16GB環境で3モデル(FAST/DEEP/CODE)+ルーター+TTS/STTを回してもピーク15843 MiBに収まった。ただし余裕が約450 MiBしかなく、今後モデルを追加(④のvision対応モデル等)する場合は同時常駐が困難になる可能性が高い。
6. **8日目「約1分」との比較。** 8日目はTTS単体で約60秒が支配的だった。今回はTTS単体が2秒未満まで改善した一方、STT区間を除いたAI側合計だけで見てもFAST(39.52秒)・CODE(45.29秒)・CLARIFY(53.09秒)はまだ40〜60秒台に留まっている。ただし支配工程がTTSからルーター判定へ完全に入れ替わっており、**「TTSを速くする」という8日目の課題は解決済み、次はルーターが新しいボトルネック**という結論になる。
7. **既知の不具合:`seg_route_rag_ttft`フィールドが壊れている。** JSON中の`seg_route_rag_ttft`は全件が大きな負の値(例: -4267.58)になっている。`measure_one()`内で絶対時刻の`t0`と相対時刻の`t_first_token`をそのまま引き算しているバグで、後方互換のため残されているフィールドだが**この値自体は使い物にならない**。本ノートの分析では代わりに`seg_ttft_only`を使用した。
8. **実機(結果3)はCLIベンチより悪い状態を示している。** CLIベンチ(結果2)のFASTルート合計39.52秒に対し、実機経由では「発話終了→STT確定」だけで15.27秒かかり、さらにSTT確定後のLLM生成が200秒でも完了しなかった。8日目の「約1分」からの改善を主張するにはまだ早く、**「CLIベンチでは速く見えるが実機では遅い(あるいは不安定)」というギャップの原因究明が①の最優先課題**になった。有力な仮説は次の2つ:
   - gpt-oss:20bがこのGPU(RTX 5070 Ti / Blackwell)でCUDAカーネルの互換性問題を抱えており(結果3(a))、初回クラッシュ後の再ロードでは`architecture filtering disabled`のまま動いているため、警告なしに大幅な性能劣化(部分的CPUフォールバック等)を起こしている可能性
   - `bench_e2e_latency.py`が`Pipe`を1プロセス内で使い回すのに対し、`voice_gateway.py`は`WebSocket`接続ごとに`pipe_factory()`を呼んで**新しい`Pipe`インスタンス**を作る(247〜251行目)ため、CLIベンチとは異なるウォームアップ状態・モデルスワップが発生している可能性(9日目ノートで既知の「`RouterSession`がプロセス別になる問題」とも関連しうる)
9. **Vosk暫定認識の約11秒の空白(結果3-(b)-1)も未解明。** GPU競合では説明できないタイミング(Ollamaの呼び出し前)で発生しており、`stt_engine.py`/`vad.py`側の別の問題(CPU側のスケジューリング、非同期処理のブロッキング等)を疑う必要がある。

### 残課題

- **最優先: 実機(結果3)とCLIベンチ(結果2)の乖離の原因を特定する**(分析8参照。Ollamaのアップデート確認、`pipe_factory()`の呼び出し方式の違いの検証)
- **gpt-oss:20bのCUDAクラッシュ・低速化(結果3-(a))への対処**: Ollamaのアップデート確認、`ollama_client.generate()`への自動リトライ追加、対応が難しければFASTルートのモデル変更も検討
- Vosk暫定認識の約11秒の空白(結果3-(b)-1)の原因調査
- **FASTルートの`seg_router`がなぜ26秒級とばらつくのか原因を特定する**(CPU固定という説明だけでは不十分)
- CODE/CLARIFYルートも文単位ストリーミング化し、TTFT・文バッファ区間を計測可能にする
- `bench_e2e_latency.py`の`seg_route_rag_ttft`フィールドの計算バグを修正する(または後方互換目的を捨てて削除する)
- VRAM残り約450 MiBという状況を踏まえ、④のvision対応モデル追加時にVRAM超過しないか事前検証する
- round=1以降の複数ラウンド実行(`--rounds`)で平均を取り、今回のround0単発計測のばらつき(特にFAST/CODEの`seg_router`)、および結果3(N=1)が測定誤差か再現する事象かを確認する
- DEEP/CODE/CLARIFYルートについても`ws_e2e_bench.py`で実機計測し、FASTルートで見つかった問題がルート固有かシステム共通かを切り分ける

---

## ①-1: FAST出力の時間が遅い対策

### 背景/目的

①結果2で、FASTルートの`seg_router`(STT確定→ルーター判定完了)だけが**26.31秒**と突出して遅く、FASTルート合計39.52秒の約67%を占めていた(DEEP 3.09秒、CLARIFY 3.27秒とは一桁違う)。「STT確定からルーターに判定させる工程がFAST出力全体を遅くしている主因ではないか」という仮説を受け、`router.py`/`support_ai_auto_pipe.py`のコードを読んで原因を分析し、対策を検討した。

### 分析:原因は1つではなく、2つの別問題が混ざっている

コードを読むと、`support_ai_auto_pipe.py`の`pipe()`は次の順で処理する(367〜461行目):

1. `session.get_route(chat_id, user_text, router.call_phi4)` — **ここが`seg_router`として計測されている区間**。ルーター専用モデル(`gemma4-e4b-cpu`、CPU固定・keep_alive=-1で常駐)を呼ぶだけで、対象ルートのFAST/DEEP/CODEモデルの起動・停止(`ensure_model_ready()`)はまだ呼ばれていない
2. `self._recall(route, user_text)` — `seg_rag`として計測
3. `router.ensure_model_ready(route)`(461行目)— **ここで初めてFAST/DEEP/CODEモデルのロード/スワップが発生する**。この区間は現状どのsegにも独立して計測されておらず、`seg_ttft_only`に埋もれている

つまり**「ルーター判定(gemma4-e4b-cpuの呼び出し)」自体と「対象モデル(gpt-oss:20b等)のロード・スワップ」は別工程で、後者はそもそも`seg_router`に含まれていない**。それでも`seg_router`が26秒もかかっているとすれば、原因はルーター呼び出しそのものの遅延であり、考えられる仮説は次の2つ:

1. **ルーターモデル(gemma4-e4b-cpu)自体のコールドロード**。`ollama list`で見るとgemma4-e4b-cpuは約9.4GBあり、ディスクからシステムRAMへの初回ロードには数十秒かかりうる。`bench_e2e_latency.py`の`QUESTIONS`はFAST→DEEP→CODE→CLARIFYの順で辞書に定義されており(137〜142行目)、**ベンチ実行中で最初に呼ばれるのがFASTの質問**。つまりFASTがたまたま「ルーターモデルの初回ロード費用」を丸ごと引き受けてしまい、DEEP以降は既にロード済みのルーターモデルを使えたため速かった、という**測定順序による見かけ上の偏り**の可能性が高い
2. **Ollama側のリクエスト直列化**。前のターンの`ensure_model_ready()`(GPUモデルの`ollama stop`)がまだ完了しないうちに次のルーター呼び出しが来ると、Ollamaのスケジューラ内で待たされる可能性がある(CODEルートもQ1が3.02秒・Q2が27.20秒とバラついており、単純な「初回ロード」だけでは説明できないため、こちらも無視できない)

なお、**「FASTモデル(`gpt-oss:20b`)自体の生成が遅い・不安定」という問題は、これとは別原因**であることが結果3の実機テストで判明済み(RTX 5070 TiとのCUDA互換性問題によるクラッシュ・低速化)。「STT確定→ルーター判定」の遅さを直しても、FASTモデル自体の生成が数分かかる状態が残っていればFAST出力全体は速くならない。**両方に手を入れる必要がある**。

### 対策

**(A) ルーター判定(`seg_router`)側**

1. **ルーターモデルの起動時ウォームアップ**:`voice_gateway.py`の`create_app()`/`main()`で、サーバ起動直後に1回`router.call_phi4()`相当をダミー文字列(例:「こんにちは」)で叩いておき、`gemma4-e4b-cpu`をOllamaにロード済みの状態にしてからリクエストを受け付け始める。初回リクエストのコールドロード費用を、ユーザーが待つ前に消費できる
2. **`ollama_client.generate()`が`load_duration`/`eval_duration`/`prompt_eval_duration`を捨てている問題を直す**:Ollamaの`/api/generate`レスポンスにはこれらのフィールドが実際に含まれている(手動curlで確認済み)のに、`generate()`(69〜82行目)は`payload.get("response", "")`しか返していない。これらをログに残すよう改修すれば、「ロード待ちなのか、CPU推論自体が遅いのか」を毎回の呼び出しで機械的に切り分けられるようになる(**現状は仮説止まりなので、これが一番効果の高い次の一手**)
3. **`ensure_model_ready()`の`stop_model()`呼び出しが次のターンのルーター呼び出しをブロックしていないか検証する**:`stop_model()`(`ollama stop`をCLI経由で呼ぶ、195〜204行目)の完了を待たずに次のリクエストへ進めているか、Ollama側のログ(`server.log`)のタイムスタンプと突き合わせて確認する
4. **ルールベース事前フィルタ(`router_rules.py`)をFASTの典型パターンにも広げる**:現状`match_rule_based()`はCODE_TRIGGERSと想起質問(RECALL_TRIGGERS→FAST)しかカバーしていない。「こんにちは」等の挨拶、簡単な時刻・計算の質問のような明確にFASTと分かるパターンを追加すれば、該当するケースではルーターモデルの呼び出し自体をスキップでき、`seg_router`をゼロにできる(誤爆リスクは低いパターンに限定する)

**(B) FASTモデル(`gpt-oss:20b`)自体の生成側**(結果3(a)の対策。①の残課題と重複するためここでは要点のみ)

1. Ollamaのアップデート確認(RTX 5070 Ti/Blackwell対応の改善を期待)
2. `ollama_client.generate()`/`generate_stream()`への自動リトライ追加(初回クラッシュ後の再ロードで成功する再現性の低い不具合のため)
3. 改善しない場合はFASTルートの割当モデルを`gpt-oss:20b`から別モデルへ変更することも検討

### 検証手順(次にやること)

1. 上記(A)-2(`load_duration`等のログ出力)を先に実装する。ログを取らずに(A)-1や(A)-4を先にやると、効果測定ができず「直った気がする」で終わってしまうため
2. ログを仕込んだ状態で`bench_e2e_latency.py --rounds 3`を再実行し、`seg_router`の内訳(ロード待ち vs 推論時間)とラウンド間の再現性を確認する
3. 原因が「測定順序による見かけ上の偏り」(仮説1)だけなら、`QUESTIONS`の順序をDEEP→FAST→CODE→CLARIFYに変えて実行し、`seg_router`の遅さがFASTではなく新しい「1番目の質問」に移動するかを確認する(移動すれば仮説1が濃厚、FASTに残り続ければ別要因)

### 残課題

- 上記「検証手順」を実施し、(A)の対策1〜4のうちどれが効果があったかを本ノートに追記する
- (B)は①の「残課題」(gpt-oss:20bのCUDAクラッシュ・低速化への対処)と統合管理する

> [!note] 2026-08-12追記: 「FAST専用の欠陥ではない」ことを確認
> ユーザーから「結果3(a)のクラッシュは手動で`ollama stop`したせいでは」との指摘があったが、
> `server.log`のクラッシュ前後(12:51:38〜12:52:21)には`/api/generate`以外のリクエストが
> 記録されておらず、手動stopの形跡は確認できなかった(裏付けは取れなかったが、再現性の低い
> 一過性の不具合という結論自体は変わらないため深追いはしない)。
> また「`seg_router`が26秒だったのはFASTがベンチの1番目だから」という点は分析どおり
> 支持されるが、**これはFASTというルート固有の欠陥ではなく「サーバー起動後、最初に来た
> リクエストが何であれ遅くなる」という一般的な問題**であることを明確化した。実運用では
> 起動後最初の1回だけ発生するため、(A)-1の起動時ウォームアップは引き続き
> 「やる価値はあるが今すぐ必須ではない」対応として残課題に置いたまま先送りする。

---

## ② Web検索対応(クレアがWeb検索できるようにする)

### 背景/目的

[[サポートAI作製計画/4日目Phi4ロジック設計.md]]のルーター設計では、FASTルートの担当領域に「検索・雑談・簡易QA・単純計算」と書かれているが、これは**ルーター(振り分けロジック)が「検索的な質問」をFASTに分類するというラベルにすぎず、実際にWeb上を検索する機能はまだ実装されていない**(`router.py`・`router_rules.py`・`support_ai_auto_pipe.py`のいずれにも外部検索APIやSearXNG等への呼び出しコードは無いことをコード検索で確認済み)。RAG記憶レイヤー([[サポートAI作製計画/5日目RAG記憶DB構築.md|5日目]])が検索するのは**自分のObsidianノート/過去の会話ログ(LanceDB)のみ**で、インターネット上の情報は対象外。「最新情報を聞かれても答えられない」状態のため、実際にWeb検索を叩ける経路を追加する。

### 作業内容

- [x] Web検索バックエンドの選定(以下から比較して決める)
  - 案1:**SearXNG**をローカル(Docker)で立てて自前運用する(APIキー不要・プライバシー面で安心。運用コストとメンテがかかる)
  - 案2:**Brave Search API** / **Tavily API**等の外部検索APIを使う(実装が速い。APIキー管理・従量課金が発生)
  - 案3:DuckDuckGoの非公式HTML/インスタントアンサーをスクレイピングする(無料だが規約・安定性の懸念)
  - > [!note] 2026-08-12決定: **案1(SearXNG)を採用**。APIキー不要・プライバシー重視で、
    > C.L.A.I.R.E.の「自分のPC内で完結させる」方針(RAG記憶もローカルLanceDB)と一致するため。
    > ただしこのマシンにはDocker(Docker Desktop)が未インストールであることが判明。
    > `winget install -e --id Docker.DockerDesktop`でインストール可能(WSL2バックエンド。
    > 管理者権限・インストール後の再起動が必要な場合あり)。ユーザー自身でインストール予定のため、
    > **インストール完了・Docker起動確認まで本項目はブロック中**。
  - > [!warning] 2026-08-13追記: **Docker導入を断念し、SearXNGをDockerなし(ネイティブ)で
    > 動かす方式に方針変更**。原因は以下の通り:
    > 1. `winget install -e --id Docker.DockerDesktop`が失敗。原因調査の結果、Windows 11 Home
    >    エディションでは**Hyper-Vバックエンドが使えず、Docker DesktopはWSL2必須**と判明
    > 2. 以前から`wsl --install`を管理者PowerShellで実行しても**14.6%で進行が止まる**現象が
    >    未解決のまま残っており(BIOS側の仮想化支援機能(VT-x)有効化や
    >    `Microsoft-Windows-Subsystem-Linux`/`VirtualMachinePlatform`機能の個別有効化、
    >    `wsl --update`等を試したが今回は深追いせず)、WSL2復旧を待たずに進める方針とした
    > 3. SearXNG自体はPython(Flask)製アプリであり、公式のuwsgi+systemd前提の手順を使わず
    >    **Flask組み込みの開発用サーバー(`python searx/webapp.py`)で直接起動する**方式なら
    >    Docker/WSL2どちらも不要と判断した(個人・単一PC用途のため実用上問題なし)
- [ ] 選定したバックエンドを叩く`web_search.py`(新規)を書く:クエリ→検索結果(タイトル・URL・スニペット)を返す部品として、既存の`memory_store.py`の検索I/Fに寄せた形にする
  - > [!note] 2026-08-13時点: 未着手。SearXNG本体のネイティブ起動(下記実施手順)を先に完了させてから着手する
- [ ] ルーターに**「Web検索が必要か」の判定**を追加する(現状の「検索」というFASTラベルを、a) RAG記憶検索で足りる、b) Web検索が要る、に細分化する)
- [ ] 検索結果をLLMのプロンプトに**RAGのcontextと同じ枠組みで**注入する(`format_context()`の拡張、または別関数として新設)
- [ ] 7日目⑤で残課題になっている「検索結果のハルシネーション対策」(直接引用を促す指示文強化)を、Web検索結果にも同様に適用する
- [ ] 音声UI(`voice_gateway.py`)経由でも動くことを確認する(検索中は`state: thinking`が伸びるため、UIの「考え中」表示が長時間化しても不自然にならないか確認する)
- [ ] 外出時のCODEルート方針(9日目⑦で保留中)と同様、**Web検索を無制限に許可してよいか**(意図しない個人情報の送信・レイテンシ増)を軽く整理しておく

### 実施手順(2026-08-13: Dockerなしネイティブ実行版に更新)

ヴォールト外の別ディレクトリ(`C:\Users\gakuh\dev\searxng`)にSearXNG本体をクローンし、`.venv`上でFlask組み込みサーバーとして起動する。ヴォールト(`サポートAI作製計画/scripts/`)には置かない(SearXNG本体は数百ファイルの別リポジトリのため)。`web_search.py`からは`http://127.0.0.1:8888`宛にHTTPで叩くだけなので、どこで動いていても影響しない。

- [x] **手順1: クローン**
  ```powershell
  mkdir C:\Users\gakuh\dev
  cd C:\Users\gakuh\dev
  git clone https://github.com/searxng/searxng.git
  cd searxng
  ```
  > [!warning] 2026-08-13にハマった点: クローン直後、`pip install -e .`が
  > 「'setup.py'/'pyproject.toml'が無い」で失敗。さらに調査すると、リポジトリ内に
  > **コロン(`:`)を含むファイル名**(`utils/templates/etc/httpd/sites-available/searxng.conf:socket`等、
  > Linuxのsystemdソケットユニット向けファイル4つ)があり、**NTFS(Windows)はファイル名に
  > コロンを使えない**ためクローンが不完全になっていた(working treeに`.git`と`.venv`しか
  > 残らず、他の全ファイルがgit上「削除」としてステージングされた状態になっていた)。
  > `git restore --staged .` → `git restore .` で大半のファイルは復元できたが、上記4ファイルは
  > `git rm --cached`しても「pathspec did not match」となった(既にindexから外れていたため
  > 実質無害)。この4ファイルはLinux専用の設定テンプレートで**Windowsネイティブ実行には不要**
  > なため、無いまま進めることにした。
- [x] **手順2: 仮想環境作成・依存インストール**
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  python -m pip install -U pip setuptools wheel pyyaml
  pip install -r requirements.txt
  ```
  > [!note] `pip install -e .`ではなく`pip install -r requirements.txt`が正しいコマンド
  > (SearXNGはpipパッケージ化された配布形態を取っていないため)。
- [x] **手順3: 設定ファイル準備**
  ```powershell
  mkdir C:\Users\gakuh\dev\searxng-instance
  copy searx\settings.yml C:\Users\gakuh\dev\searxng-instance\settings.yml
  ```
  - [x] `secret_key`をランダム値に変更(`python -c "import secrets; print(secrets.token_hex(32))"`の出力を貼る。空だと起動拒否される)
  - [x] `search.formats`に`json`を追加(既定はhtmlのみでAPIとして叩けないため)
  - [x] `server.port`が`8888`になっているか確認 → 2026-08-13確認: 既定のまま`8888`になっていたため変更不要だった
- [x] **手順4: 起動・疎通確認**
  ```powershell
  cd C:\Users\gakuh\dev\searxng
  .\.venv\Scripts\Activate.ps1
  $env:SEARXNG_SETTINGS_PATH = "C:\Users\gakuh\dev\searxng-instance\settings.yml"
  python -m searx.webapp
  ```
  別ターミナルで `curl "http://127.0.0.1:8888/search?q=test&format=json"` がJSONを返せば成功。初回はWindows Defenderファイアウォールの許可(プライベートネットワーク)が出る。
  > [!warning] 2026-08-13にハマった点①: `python searx\webapp.py`(スクリプト直接実行)は`ModuleNotFoundError: No module named 'searx'`で失敗する
  > スクリプトを直接実行すると、Pythonは**スクリプト自身のディレクトリ**(`searx\`)を`sys.path[0]`に追加し、リポジトリルートは追加しない。そのため`webapp.py`内の`import searx`系のimportが解決できない。
  > **対策**: リポジトリルート(`C:\Users\gakuh\dev\searxng`)から`python -m searx.webapp`と**モジュールとして**実行する(上記コマンドは修正済み)。こうするとカレントディレクトリがpathに入り解決する。
  > [!warning] 2026-08-13にハマった点②: 上記対策後も`ModuleNotFoundError: No module named 'pwd'`で失敗した
  > `pwd`はUnix専用の標準ライブラリでWindowsには存在しない(pipでの追加インストールも不可)。SearXNG本体はLinux/macOS実行が前提のため、`searx/valkeydb.py`が(Valkey/Redis接続失敗時のログ出力用に)`import pwd`をトップレベルで無条件に書いており、`webapp.py`→`limiter.py`→`valkeydb.py`のimportチェーンで即座に落ちていた。Docker断念とは無関係の**Windowsネイティブ実行特有の非互換バグ**。
  > **対策**: `C:\Users\gakuh\dev\searxng\searx\valkeydb.py`を直接パッチした。
  > - `import pwd`を`try/except ImportError`で囲み、Windowsでは`pwd = None`にフォールバック
  > - `except`節内の`pwd.getpwuid(...)`を使ったログ出力を`pwd is not None`でガードし、Windowsでは簡略化したログメッセージに差し替え
  > この修正で`python -m searx.webapp`が起動に成功し、`curl "http://127.0.0.1:8888/search?q=test&format=json"`がHTTP 200・JSON(Wikipedia等の検索結果)を返すことを確認した。
  > なお起動時に`ahmia`/`bilibili`/`wikidata`/`torch`エンジンの読み込みエラーが出るが、`tzdata`パッケージ不足やWikidata側のHTTP 403など**個別エンジン固有の軽微な問題**で、SearXNG全体の起動やWeb検索自体には影響しない(気になる場合は`pip install tzdata`で`bilibili`のエラーは解消できる)。
  > [!warning] 2026-08-13にハマった点③: ブラウザで開くとCSS/JSが当たらず、SearXNGロゴのSVGだけが画面いっぱいに表示される
  > DevToolsのNetworkタブで確認したところ、`sxng-core.min.js`・`sxng-ltr.min.css`・`favicon.svg`が**404**になっていた。
  > 原因は`searx/webutils.py`の`get_static_file_list()`が`pathlib.Path.relative_to()`の結果をそのまま
  > `str()`化しており、**Windows上ではこれがバックスラッシュ区切り**(`themes\simple\sxng-core.min.js`)になる点。
  > 一方`webapp.py`の`custom_url_for()`はテーマファイルの照合を`f"themes/{theme_name}/{arg_filename}"`と
  > **スラッシュ区切りの文字列**で行っているため、Windows上ではこの2つが一致せず、
  > 実在しない`/static/sxng-core.min.js`(themes/simpleのプレフィックス無し)がHTMLに出力されて404になっていた。
  > `:`ファイル名問題・`pwd`モジュール問題に続く**3つ目のWindowsネイティブ実行特有の非互換バグ**。
  > **対策**: `searx/webutils.py`の`get_static_file_list()`内で、相対パスを`.replace(os.sep, '/')`して
  > フォワードスラッシュに正規化するようパッチした。再起動して確認したところCSS/JSは200で読み込まれ、
  > 画面は正常化した(検証済み)。
  > [!warning] 2026-08-13にハマった点④: 画面は直ったが、検索を実行すると500 Internal Server Error
  > `curl ...&format=json`ではSearXNGのエンジン自体(google cse/duckduckgo等)は正常に結果を返しており、
  > **検索バックエンド自体は機能していた**。問題はHTML表示(`theme=simple`)側のみで発生。
  > `debug: true`に一時変更してトレースバックを確認したところ、`jinja2.exceptions.TemplateNotFound:
  > result_templates/default.html`が原因と判明。`searx/webutils.py`の`get_result_templates()`にも
  > `get_static_file_list()`と**同じ系統のWindowsパス区切り文字バグ**があり(`os.path.join()`の結果が
  > バックスラッシュ区切りになるため、`webapp.py`の`get_result_template()`がスラッシュ区切りで行う
  > 照合に失敗し、テーマ名prefix無しの誤ったテンプレートパスにフォールバックしていた)、同様に
  > `.replace(os.sep, '/')`でパッチして解決した(検証済み: `curl`で検索結果ページが200・ページネーション
  > まで含めて正常描画されることを確認)。`debug`設定はテスト後`false`に戻し、検証用に一時起動した
  > プロセスは停止済み。**次にターミナルで`python -m searx.webapp`を起動し直せば通常運用に戻る**。
- [x] **手順5: 常駐運用の検討**
-　現状はOllama/VOICEVOXと同様、起動用ターミナルを立てっぱなしにする運用で当面回す。将来的にはnssm等でのWindowsサービス化も検討)

### 結果 / 分析 / 改善策

(手順3〜5の実行後に記入)

### 残課題

- `web_search.py`の実装に着手する(手順4の疎通確認は完了したので着手可能)
- WSL2の`wsl --install`が14.6%で止まる問題自体は未解決のまま(今回はDocker断念により回避しただけ)。将来Docker Desktopが必要になった場合はBIOSの仮想化支援機能(VT-x/AMD-V)有効化から見直す必要がある
- `searx/valkeydb.py`へのpwdモジュールパッチ、および`searx/webutils.py`への静的ファイルパス区切り文字パッチはいずれもリポジトリ本体への直接編集(git管理下のファイル)。`git pull`等でSearXNG本体を更新すると**上書きで消える可能性がある**ため、更新時は再適用が必要なことに注意
- `searx/webutils.py`への2箇所のパッチ(`get_static_file_list()`・`get_result_templates()`)は検証済み。次回`git pull`等でSearXNG本体を更新する際は再適用が必要な点に注意(`valkeydb.py`のpwdパッチと合わせて計3箇所)

---

## ③ 自作UIデザインの確定(OpenWebUI相当の機能を備えたデザイン検討)

### 背景/目的

9〜10日目で音声対話専用の自前UI(`voice_gateway.py` + `static/index.html`)を実装したが、現状は**音声対話に特化した最小限のUI**で、これまでOpenWebUIが持っていた「チャット履歴の一覧・切り替え」「モデル/Valve設定のGUI」「ファイル添付」等の機能が無い。[[サポートAI作製計画/scripts/static/index.html]]をこのままOpenWebUIの完全な代替にするのか、機能をどこまで自作UIに寄せるのかの方向性が決まっていない。参考として[[サポートAI作製計画/UIデザイン提案.html]](JARVIS HUD風の「リアルタイム音声対話ダッシュボード」コンセプト、シアン×ネイビーの配色・同心円リングのARC CORE等)を仮で作成済みだが、**見た目のコンセプトは固まった一方、OpenWebUIのような機能一式をこの方向性でどう配置するかで迷っている状態**。

### 作業内容

- [x] [[サポートAI作製計画/UIデザイン提案.html]]を実際にブラウザで開いて表示確認する(スクリーンショット済みの[[サポートAI作製計画/UIデザイン.png]]と差分がないか)
- [ ] OpenWebUIが持つ機能を棚卸しし、**自作UIに必須/あれば良い/不要**に仕分けする(例:チャット履歴一覧、複数チャットの切り替え、モデル選択、Valve設定GUI、ファイル添付、Markdown/コードブロックのシンタックスハイライト、会話のエクスポート等)
- [ ] 音声対話(9〜10日目で実装済みのリアルタイム性重視のHUD的UI)と、テキスト中心の従来型チャットUI(OpenWebUI的な左サイドバー+中央チャットlog)を**同じ画面に同居させるか、モード切り替えにするか**を決める
- [ ] `UIデザイン提案.html`のARC CORE(同心円リング)やスキャンライン背景といった装飾要素が、**長時間の実用(日常的にテキストで使う場面)でも邪魔にならないか**を検討する(装飾を弱めたテキスト作業モードを別途用意するか等)
- [ ] 上記を踏まえて採用するデザイン方針を1つに確定し、`static/index.html`への反映方針(段階的に機能追加していく順序)を決める
- [ ] ④で対策するマルチモーダル対応(ファイル添付UI)を、デザイン確定時にレイアウトへ組み込んでおく(後付けで崩れないようにする)

### 実施手順(予定)

(方針確定後に実装タスクへ分解する)

### 結果 / 分析 / 改善策

2026-08-13、自分の考え(ファイル添付ボタンはマイクボタンの左隣に追加する/チャット履歴にリネーム・削除ボタンが無いので後で足す)を踏まえて、[[サポートAI作製計画/UIデザイン提案.html]](ver1)を再分析し、次の3つの表を作成した上で[[サポートAI作製計画/UIデザイン提案ver2.html]](新規)を作成した。

#### 表1: OpenWebUIの必要項目の棚卸し

| 分類 | 項目 | 内容 |
|---|---|---|
| サイドバー | 新規チャット | 新しい会話セッションを開始するボタン |
| サイドバー | チャット履歴一覧(日付グループ化) | Today/Yesterday等でグループ化された会話リスト |
| サイドバー | 履歴の検索 | タイトル・本文から会話を絞り込む検索欄 |
| サイドバー | 履歴のリネーム | 各履歴アイテムの名前をインライン編集する✎ボタン |
| サイドバー | 履歴の削除 | 各履歴アイテムを削除する🗑ボタン |
| サイドバー | ピン留め/アーカイブ | 使う履歴を固定表示/使わない履歴を隠す |
| トップバー | モデル選択(切替可能) | ドロップダウンで使用モデルをその場で切り替え |
| トップバー | 設定/Valve GUI | Pipeのパラメータ等をGUIで変更 |
| トップバー | ユーザーメニュー | アカウント設定・サインアウト |
| チャット領域 | メッセージ吹き出し(ユーザー/AI) | 発話者ごとに視覚的に区別された会話ログ |
| チャット領域 | Markdown/コードブロック表示 | シンタックスハイライト+コピー ボタン |
| チャット領域 | 引用元(出典)表示 | RAG検索やWeb検索でヒットしたソースの明示 |
| チャット領域 | メッセージ操作 | コピー/再生成/編集/評価(いいね・よくない) |
| 入力欄 | マルチライン入力 | 長文入力に対応するテキストエリア |
| 入力欄 | ファイル添付(画像/PDF等) | ドラッグ&ドロップ or ボタンでのアップロード |
| 入力欄 | 音声入力 | マイクボタンでの発話入力 |
| 入力欄 | Web検索トグル | その発話でWeb検索を使うかのON/OFF |
| その他 | ナレッジベース(RAGドキュメント)管理 | 取り込んだノート/文書の一覧・管理 |
| その他 | チャットのエクスポート/共有 | 会話をファイル出力/リンク共有 |
| その他 | テーマ切替・通知 | ライト/ダーク切替、トースト通知 |

#### 表2: 参考文献のJARVIS開発動画の分析

参考文献に挙げた2本の動画(いずれも「Claude Codeで自分だけのJARVISを作る」系のAIアシスタントHUD開発企画。タイトルは`I Built My Own JARVIS with Claude Code (100% FREE)`/`I Built JARVIS from Iron Man with Claude Fable 5 (INSANE Results!)`)は、YouTube側の制限で本文からの自動書き起こし取得はできなかった(`WebFetch`ではフッターのみ返り、字幕・本文は抽出不可)。そのため、ver1作成時に実際に動画を見て抽出済みだった意匠(ver1冒頭コメント参照)を、Iron Man作品由来のJARVIS HUDの一般的な構成要素と突き合わせて再整理した。

| 要素 | 内容 |
|---|---|
| 配色 | 深いネイビー/黒背景+シアン発光アクセント(ハイコントラストな"機械の眼"感) |
| 中枢ビジュアル | 同心円リングの「ARC CORE」。発話に反応する波形/音声レベル表示を中心に配置 |
| 背景装飾 | スキャンライン/グリッドテクスチャ+放射状グロー(奥行きのあるHUD感) |
| フレーム | 四隅のコーナーブラケット(L字)でHUDの枠を可視化 |
| タイポグラフィ | モノスペース/近未来ディスプレイフォント(Orbitron等)、広いトラッキングの大文字ラベル |
| テレメトリ表示 | 遅延(ms)・tok/s・VRAM・稼働時間などのライブ数値を常時表示 |
| ステータス表示 | 発光ドット付きピル型バッジ(LISTENING/VAD ACTIVE等) |
| 音声操作 | 中央の大型丸型マイクボタン(Push-to-talk) |
| ログ表示 | ターミナル風の逐次イベントログ(タイムスタンプ付き) |
| パイプライン可視化 | STT→ルーター→LLM→TTSの各段階をメーターで表示 |

#### 表3: 自作UI(ver1)に足りない項目(表1・表2の差分+自分の考え)

| 分類 | 項目 | ver1の状態 | ver2での対応 |
|---|---|---|---|
| マルチモーダル | ファイル添付ボタン | 無し | マイクボタンの左隣にクリップアイコンのボタンを追加(自分の考え通りの配置) |
| マルチモーダル | 添付ファイルのプレビュー | 無し | 入力欄の上に添付済みファイルのチップ(サムネイル・ファイル名・削除ボタン)を表示 |
| チャット履歴管理 | リネーム | 無し | hover時に✎ボタンを表示、インライン編集に切り替え |
| チャット履歴管理 | 削除 | 無し | hover時に🗑ボタンを表示 |
| チャット履歴管理 | 検索 | 無し | サイドバー上部に検索入力を追加 |
| テキストチャットログ | メッセージ吹き出し表示 | 無し(ARC CORE中心のHUDのみで会話ログが見えない) | ARC COREを上部の小型ヘッダに縮小し、下に音声/テキスト共通のスクロール可能なメッセージログを追加(③の「音声HUDとテキストUIを同居させるか」の論点への回答=同居させる) |
| テキストチャットログ | Markdownコードブロック | 無し | コードブロック+言語表示+コピー ボタンを追加 |
| RAG/Web検索 | 引用元(出典)表示 | 無し | メッセージ下にObsidianノート/Web検索結果の出典チップを追加 |
| RAG/Web検索 | 検索中インジケータ | 無し | 「Web検索中…」のドットアニメーション付き表示を追加(②の「考え中表示が長時間化しても不自然にならないか」への対応) |
| モデル選択 | 切替可能なドロップダウン | 静的表示のみ | `<details>`によるドロップダウンでその場でモデル切替できるUIに変更 |
| メッセージ操作 | コピー/再生成/編集/評価 | 無し | 各メッセージにhoverで表示されるアクションアイコン行を追加 |
| Web検索 | ON/OFFトグル | 無し(②実装前のため未対応だった) | 下部パッドにピル型トグル、右パネルのControlsにスイッチを追加 |

### 残課題

- ver2はモックアップ(静的HTML)のため、`static/index.html`(実装)への段階反映が未着手。まず①ファイル添付ボタン(受け口はまだ`voice_gateway.py`に無い。④の残課題と連動)、②チャット履歴のリネーム/削除(現状は履歴の永続化自体が未実装の可能性があるため、先に履歴の保存先を確認する)の2点から着手するのが優先度高
- チャット履歴のリネーム/削除を機能させるには、履歴データの保存形式(ファイル名変更で足りるのか、DB/JSON側の更新も要るのか)を先に調査する必要がある
- Web検索ON/OFFトグルの実装は②(Web検索対応)の`web_search.py`実装後に接続する
- 引用元(出典)チップは、RAGの`format_context()`拡張(②の作業内容)が返すソース情報をUI側にどう渡すか(APIレスポンスの形式設計)を別途詰める必要がある

#### 追記(2026-08-13): 読み上げ速度調整バーの追加

上記の表3作成・ver2作成後、追加で「読み上げ速度を変えられるバーを右側のどこかに置きたい」との要望を受けた。これも表1(OpenWebUIには無い独自要望だが、TTSを自前運用しているC.L.A.I.R.E.には必要な項目)・表3の不足項目に相当するため、**[[サポートAI作製計画/UIデザイン提案ver2.html]]の右パネル「// Controls」内**(思考モードスイッチの直下)に追加した。

| 項目 | 内容 |
|---|---|
| 配置 | 右パネル Controls セクション最下部(ウェイクワード/自動送信/RAG/Web検索/思考モードのスイッチ群の下) |
| UI | ラベル+現在値表示(例: `1.0x`)+スライダー(0.5x〜2.0x、0.1刻み)+プリセットの目盛りクリック(0.5/0.75/1.0/1.5/2.0) |
| 見た目 | シアンのグラデーショントラック+発光する丸型つまみで、既存のJARVIS HUDの配色・トークンをそのまま踏襲 |
| 実装時の接続先(未着手) | `voice_gateway.py`経由でTTS(VOICEVOX)呼び出し時の`speedScale`パラメータへ反映する想定。値の送信タイミング(スライダー操作のたびに送るか、次回発話から反映か)は未検討 |

##### 残課題(追記分)

- VOICEVOXの`speedScale`は実際何倍まで許容されるか(0.5〜2.0の範囲設定がVOICEVOX側の実用域と合っているか)を`tts_latency_bench.py`等で確認する
- スライダーの値をどこに保存するか(セッションごとか、ユーザー設定として永続化するか)を決める
- 音声UI(`voice_gateway.py`)側に速度パラメータを受け取るWSメッセージ種別がまだ無いため、②③④の実装と合わせて追加する

---

## ④ マルチモーダル対応の現状調査と対策

### 背景/目的

これまでOpenWebUI標準UIを使っていた際は、画像やPDFといったファイルを添付して質問できていた(OpenWebUI自体が持つファイルアップロード・ドキュメントRAG機能によるマルチモーダル対応)。9〜10日目で音声対話は自前UI(`voice_gateway.py`)経由に切り替わったため、**現段階のシステムがマルチモーダルに対応できているかを確認し、対応できていなければ対策を講じる**必要がある。

### 作業内容(調査。本ノート作成時点で先行実施済み)

- [x] `voice_gateway.py`にimage/base64/PDF等の受信処理があるか確認する
- [x] `ollama_client.py`にvision系(画像をプロンプトに含める)実装があるか確認する
- [x] `support_ai_auto_pipe.py`が画像添付をどう扱っているか確認する
- [x] 対策(下記改善策)の実装(2026-08-13: PDF/Word/Excel/PowerPointのみ。画像は④-1参照で未着手)

### 結果

コードベースを実際に検索した結果、以下が判明した。

| 確認対象 | 結果 |
|---|---|
| `voice_gateway.py` | 画像・PDF・ファイルアップロードを受け取るエンドポイント/WSメッセージ種別は**存在しない**。WebSocketは音声フレーム(バイナリ)と`text_input`(JSON文字列)のみを受け付ける |
| `ollama_client.py` | `images`引数やbase64画像をリクエストボディに含める処理は**存在しない**(`generate()`/`generate_stream()`ともテキストのみ) |
| `support_ai_auto_pipe.py`(`_extract_last_user_text()`, 207〜210行目) | Open WebUI経由で画像添付時に`content`が list形式になるケースを**検知はしているが、`isinstance(content, str)`でない場合は空文字列として扱い、画像自体は読み捨てている**。つまりOpenWebUI経由で使っていた時点でも、**このカスタムPipe自身は画像の中身を一切解釈していなかった** |
| ルーター(`router.py`/`router_rules.py`) | 画像・ファイル種別に応じた振り分けロジックは無し |

### 分析

1. **現段階のシステムはマルチモーダル(画像・PDF)に対応できていない。** 音声UI(`voice_gateway.py`)には添付機能自体が無く、たとえ添付できたとしても`ollama_client.py`が画像をLLMへ渡す実装を持っていない。
2. **これまで「OpenWebUIでできていた」ように見えたマルチモーダル対応は、実はカスタムPipe(クレア自身のロジック)の機能ではなく、OpenWebUI標準UI側の機能(ファイルアップロードUI+OpenWebUI自身のドキュメントRAG、または添付画像をベースモデルへそのまま渡す標準チャット経路)に依存していた**可能性が高い。コード上、Pipeは画像添付を検知した時点でむしろ内容を捨てているため、**Pipe経由の会話では以前から画像の中身は読めていなかった**と考えられる(要:OpenWebUI側のログ/挙動で最終確認)。
3. 現在採用しているLLM(`gpt-oss-20b`、`Phi-4`系、`gemma4-e4b-cpu`等)は基本的に**テキスト専用モデル**であり、画像を直接解釈できるvision対応モデル(例:`qwen2.5vl`、`llama3.2-vision`、`gemma3`のマルチモーダル版等)をOllamaにロードしていない。したがって「受け口を作る」だけでは不十分で、**vision対応モデルの追加導入も必要**。
4. PDFについては画像とは別で、**vision不要でもテキスト抽出(PyPDF2/pdfplumber等)して既存のRAGパイプラインに載せれば実現できる**(5日目のingest_notes.pyと同系統の処理を転用できる)ため、画像対応より着手コストが低い。

### 改善策(実装はこれから)

- **PDF対応(優先度高・着手コスト低)**:テキスト抽出ライブラリ(pdfplumber等)でPDFからテキストを取り出し、`ingest_notes.py`と同様の経路でチャンク化→埋め込み→LanceDBへ一時登録(またはその場でプロンプトに直接注入)する`ingest_pdf.py`(新規)を作る。`static/index.html`にファイル選択UIを追加し、`voice_gateway.py`にアップロード用のHTTPエンドポイント(WebSocketとは別)を追加する。
- **画像対応(優先度中・着手コスト高)**:
  1. Ollamaにvision対応モデル(候補:`qwen2.5vl`、`llama3.2-vision`、`gemma3`のvision版)を追加ダウンロードし、VRAM 16GB制約内で既存3モデル(FAST/DEEP/CODE)と共存できるか確認する
  2. `ollama_client.py`に`images`(base64リスト)を渡せる引数を追加する(既存の`generate()`/`generate_stream()`のシグネチャを壊さない後方互換を維持)
  3. ルーターに「画像添付あり」の分岐を追加し、vision対応モデルへ強制的にルーティングする
  4. `static/index.html`・`voice_gateway.py`に画像アップロード(ドラッグ&ドロップ or ファイル選択)のUIと受信処理を追加する(③のUIデザイン確定時にレイアウトへ組み込む)
- **OpenWebUI経由の運用を当面併用する**という選択肢も残す。自前UIでのマルチモーダル対応が整うまでは、画像やPDFを使いたい場面だけOpenWebUI(8080)を使う運用にすれば、開発中でも実用上の穴を埋められる。

> [!note] 2026-08-13追記: PDF/Word/Excel/PowerPoint対応を実装済み(画像は未着手・下記④-1参照)
> 「まずは実装コストの低いPDF/Word/Excel/PowerPointから対応する」方針のもと、上記改善策の
> PDF対応の範囲をOffice系3形式(Word/Excel/PowerPoint)にも広げる形で実装した。
>
> - **`scripts/rag_memory/doc_ingest.py`(新規)**: PDF(`pdfplumber`)/Word(`python-docx`)/
>   Excel(`openpyxl`)/PowerPoint(`python-pptx`)からテキストを抽出し、`chunker.chunk_markdown()`で
>   チャンク化 → `memory_store`経由でLanceDBへ**永続登録**する(「その場でプロンプト注入」ではなく
>   「RAGへ永続登録」を採用。ユーザーとの相談で、OpenWebUI/Claudeの「プロジェクト機能」相当の
>   ナレッジ化として扱うことに決めた)。`source="doc:<ファイル名>"` / `role="document"` /
>   `route="DOCUMENT"`で登録し、`ingest_notes.py`と同じ「削除してから追加」upsert方式のため、
>   同名ファイルの再アップロードは上書きになる。
> - **`support_ai_auto_pipe.py`**: CODEルートのrecall絞り込みを`route=("CODE", "NOTE")`から
>   `route=("CODE", "NOTE", "DOCUMENT")`に拡張(FAST/DEEPは元々`route=None`でフィルタ無しのため
>   変更不要。7日目⑤で見つかった「NOTEを除外すると設計ノートがヒットしなくなる」問題と同種の
>   衝突を、DOCUMENTでも先回りして防いだ)。
> - **`voice_gateway.py`**: `POST /documents`(アップロード→抽出→登録、20MB上限あり)・
>   `GET /documents`(一覧)・`DELETE /documents/{filename}`(削除)の3エンドポイントを追加。
>   WebSocket(`/ws`)とは独立したHTTP経路(ファイルアップロードはWSより素直に書けるため)。
> - **`static/index.html`**: テキスト入力欄の左に📎添付ボタンを追加(ver2デザイン案どおり
>   マイクボタン付近に配置)。アップロード成功時は「[ナレッジ登録] filename(Nチャンク)」を
>   ログに表示し、チップにも状態を出す。ヘッダーに📚ボタンを追加し、簡易一覧パネル
>   (ファイル名・チャンク数・登録日時・削除ボタン)をユーザーとの相談どおり実装した。
> - 依存関係: `pdfplumber` `python-docx` `openpyxl` `python-pptx` `python-multipart`を
>   このマシンへ追加インストール済み(このプロジェクトはvenvを切らず素のPython 3.12環境を
>   使っているため、リポジトリ側にrequirements.txt等の記録は無い。②のSearXNG用venvとは別)。
> - テスト: `tests/test_doc_ingest.py`(拡張子ディスパッチ+Word/Excel/PowerPointは実ライブラリで
>   書いて読む往復テスト。PDFは書き込み用ライブラリ(reportlab等)を追加するコストを避け、
>   ディスパッチのみモックで確認し実際の読み取りは手動確認とした)、
>   `tests/test_voice_gateway.py`に`/documents`系3エンドポイントのFastAPI TestClientテストを追加。
>   `tests/test_support_ai_auto_pipe_memory.py`の期待値も`("CODE", "NOTE", "DOCUMENT")`へ更新。
>   全191件(既存180件+新規11件)が成功。

### 残課題

- vision対応モデルの選定とVRAM同居可否の実測(未着手。下記④-1「画像対応の検討事項」参照)
- ~~PDF取り込みの実装~~ → 2026-08-13実装済み(Word/Excel/PowerPointも合わせて対応。上記追記参照)
- 画像アップロードUIの実装(③のUIデザイン確定を待つ。④-1の方針確定後に着手)
- OpenWebUI経由でのマルチモーダル動作が実際にPipeを経由していたのか、Open WebUI標準チャット経由だったのかの最終確認(分析②の裏付け)
- PDF実抽出(`_extract_pdf`)の実機確認がまだ自動テスト化されていない(手動でのアップロード確認が必要)
- 大きめのPDF/Excel(数百ページ・数万行)での埋め込み所要時間・チャンク数の実測(20MB上限は決めたが、時間面の上限は未検証)
- ナレッジ一覧パネルはファイル名・チャンク数・削除のみの簡易版。検索・並び替え等は今回スコープ外(YAGNI。必要になったら追加)

---

## ④-1 画像対応の検討事項(2026-08-13追記: 実装は未着手・検討のみ)

### 背景/目的

④の改善策では「画像対応にはvision対応モデルの追加ダウンロードが要る」という前提で書いたが、
ユーザーから「本当に追加インストールが必要か? `gemma3`にvision対応版があるなら、今使っている
`gemma4`系のモデル(ルーター`gemma4-e4b-cpu`・DEEP`gemma4:26b`)でも画像を読めるのではないか」
という指摘があった。これは「新しいモデルを追加でロードせず、既存モデルの入力にimagesを足すだけで
済むかもしれない」という、VRAM(結果1で残り約450MiBしかないと判明済み)の制約上とても重要な論点
のため、実装に入る前にこのノートへ検討事項として整理しておく(**このセクションは調査結果ではなく
「何を確認すべきか」の設計メモ**。実際にOllama側で確認するのは次回作業)。

### 確認すべきこと(未実施)

1. **`gemma4-e4b-cpu`(ルーター用)・`gemma4:26b`(DEEP用)が実際にvision(画像入力)対応の
   チェックポイントかどうか。** Ollamaの`/api/show`(または`ollama show <model>`)には
   `capabilities`フィールドがあり、`["completion", "vision", ...]`のように対応能力が
   列挙される。ここに`vision`が含まれているかを見れば、追加ダウンロード無しで画像を
   渡せるかが機械的に判定できる。`gemma3`系(4b/12b/27b)がマルチモーダル単一チェックポイントで
   配布されているのと同じ構造を`gemma4`系も引き継いでいれば、**コード変更(`ollama_client.py`に
   `images`引数を足すだけ)で対応でき、モデルの追加ダウンロードは不要**になる可能性が高い。
2. **ルーター(`gemma4-e4b-cpu`)とDEEP(`gemma4:26b`)のどちらへ画像を渡すべきか。**
   仮に両方ともvision対応だとしても、①-1の分析で判明したとおりルーターはCPU固定であり、
   テキストだけの判定でも`seg_router`が数秒〜26秒とばらつく状態(残課題「FASTルートの
   `seg_router`がなぜ26秒級とばらつくのか」参照)。画像はテキストよりトークン化コストが重く、
   CPU推論では明らかに不利なため、**画像を扱うなら(GPU上で動く)DEEP側に寄せるほうが現実的**。
   ルーター側で画像の有無だけを見て「画像添付あり→強制的にDEEPへ」という分岐にすれば、
   ルーター自体に画像を読ませる必要はなくなる(添付ファイル種別だけを見る軽い判定で済む)。
3. **vision対応であっても、画像トークン処理でVRAM使用量が増えないか。** 結果1でVRAM残りが
   約450MiBしかないことが判明済み。モデルの重み自体は変わらなくても、画像を埋め込む際の
   中間テンソル(vision encoder出力)がVRAMを追加消費する可能性があるため、実際に画像付き
   リクエストを1回投げてVRAMピークを`nvidia-smi`で確認する必要がある。
4. **もし`gemma4`系がvision非対応だった場合の代替案の優先順位。**
   - 案A: 改善策どおり専用vision対応モデル(`qwen2.5vl`、`llama3.2-vision`等)を追加導入する
     (VRAM圧迫のリスクが最も高い。残り約450MiBでは小型モデルでも同時常駐は厳しい可能性)
   - 案B: DEEPモデル(`gemma4:26b`)を、vision対応版が存在するなら**同サイズ帯のvision版へ
     置き換える**(新規追加ではなく置き換えなのでVRAM純増を避けられる可能性がある)
   - 案C: 画像そのものをLLMに読ませず、**OCR(文字認識)だけ**で対応する(`pytesseract`等)。
     スクリーンショットやスキャン文書のような「文字が主体の画像」であれば、PDFと同じ
     テキスト抽出→ナレッジ登録の経路にそのまま乗せられ、vision対応モデルなしで実装コストを
     大幅に下げられる。ただし写真・図表・グラフの内容理解はできない(用途が限定される)

### 論点整理(まだ結論は出していない)

- 「本当に追加インストールが要るか」への一次的な答えは、**`ollama show gemma4:26b`と
  `ollama show gemma4-e4b-cpu`の`capabilities`を見れば数分で判明する**。次回はまずここから
  着手するのが最も費用対効果が高い(結論を推測で進めず、実際に確認してから設計する)
- vision対応だったとしても、①で判明したCLIベンチと実機の乖離・gpt-oss:20bのCUDAクラッシュ問題が
  未解決のままなので、**①の安定化を優先し、画像対応はその後に着手する**方針は維持する
  (VRAM残り約450MiBという逼迫した状態で新しいモデル呼び出しパスを増やすと、不具合の切り分けが
  さらに難しくなるため)
- 案C(OCRのみ)は「vision対応モデルの有無によらず低コストで実装できる」という点で、
  vision対応モデルの選定・導入が長引く場合の**暫定的な最小対応**として検討する価値がある

### 残課題

- ~~`ollama show gemma4:26b` / `ollama show gemma4-e4b-cpu`で`capabilities`に`vision`が~~
  ~~含まれるか確認する~~ → 2026-08-14確認済み(下記「結果」参照。両モデルともvision対応)
- ~~vision対応と判明した場合、画像1枚を実際に渡してVRAMピークと応答内容の妥当性を確認する~~
  → 2026-08-14実施済み(下記「結果」参照)
- 上記の結論が出たので、④の改善策(画像対応)を本節の結論に沿って書き直す(次回着手)
- ルーター(`gemma4-e4b-cpu`)へ画像を渡すのは精度・速度の両面で不利と判明したため、
  「画像添付あり→強制的にDEEPへ」という分岐(④改善策の案3)を実装する
- `ollama_client.generate_stream()`はまだ`images`引数に対応していない(今回は`generate()`のみ
  拡張した。DEEPルートは通常ストリーミング応答のため、画像対応を本実装する際は
  `generate_stream()`側にも同様の拡張が必要になる)
- テスト画像1枚・N=1の単発計測にとどまる。実運用に近い画像(写真・スクリーンショット等)での
  再現性確認、複数回計測での安定性確認はまだ行っていない

### 結果(2026-08-14: `ollama show`確認 + `vision_bench.py`による実測)

**`capabilities`確認**(`curl http://127.0.0.1:11434/api/show -d '{"model":"..."}'`)

| モデル | capabilities |
|---|---|
| `gemma4-e4b-cpu`(ルーター) | `["completion", "vision", "audio", "tools", "thinking"]` |
| `gemma4:26b`(DEEP) | `["completion", "vision", "tools", "thinking"]` |

ユーザーの指摘どおり、**両モデルとも追加ダウンロード無しでvision対応済み**であることを確認した
(`ollama list`/`/api/tags`のcapabilities表示は古いキャッシュを返すことがあり、`vision`が
含まれていなかった。実際に判定に使うべきは`/api/show`(`ollama show <model>`)の値)。

**画像実測**(`vision_bench.py --models "gemma4-e4b-cpu,gemma4:26b" --image <テスト画像>`。
テスト画像はPillowで生成した「白背景・左上に『TEST BOX』と書かれた青い四角・中央に黒縁の赤い楕円」の
合成画像。結果ファイル: `results/vision_bench/vision_bench_20260814_000811.md`)

| モデル | 所要時間(秒) | VRAM前(MiB) | VRAMピーク(MiB) | VRAM後(MiB) | 応答の妥当性(目視) |
|---|---|---|---|---|---|
| `gemma4-e4b-cpu`(ルーター・CPU固定) | 66.97 | 2616 | 2620 | 2600 | **NG**。「抽象的な煙のようなテクスチャの背景画像」と、画像に実在しない内容を答えた(ハルシネーション) |
| `gemma4:26b`(DEEP) | 39.83 | 2600 | 15705 | 15690 | **OK**。「青い長方形のボックス(『TEST BOX』の文字入り)」「赤い楕円(黒縁)」「明るいグレーの背景」を正確に説明 |

### 分析

1. **DEEP(`gemma4:26b`)は画像対応として実用に足る精度がある。** テスト画像に含まれる3要素
   (青い箱・テキスト・赤い楕円・背景色)をすべて正確に言語化できており、④改善策で挙げていた
   「専用vision対応モデルの追加導入」は不要という結論になった。
2. **ルーター(`gemma4-e4b-cpu`)へ画像を渡すのは精度・速度の両面で不利。** capabilities上は
   visionに対応しているにもかかわらず、実際の応答は画像の内容と無関係な文章(ハルシネーション)
   だった。加えて67秒とDEEP(40秒)の1.7倍近く遅い。①-1で判明した「ルーターはCPU固定で
   テキストのみの判定でも数秒〜26秒とばらつく」という既知の弱点が、画像入力(テキストより
   トークン化コストが重い)でさらに悪化したものと考えられる。**④-1で立てた仮説
   (「画像はルーターではなくDEEPに寄せるべき」)が実測でも裏付けられた**。
3. **VRAMは懸念どおり逼迫する。** `gemma4:26b`単体呼び出しでVRAMピーク15705MiB(総量16303MiBに
   対し残り約600MiB)となり、結果1で判明していた「通常運用でピーク15843MiB・残り約450MiB」と
   ほぼ同水準まで逼迫することを確認した。他のモデル(FAST/CODE)と同時にVRAMを取り合う実運用
   シナリオでは、この計測よりさらに厳しくなる可能性が高い。
4. **`gemma4-e4b-cpu`のVRAM使用量が2600MiB程度と少ないのは想定どおり。** CPU固定
   (`OLLAMA_NUM_GPU=0`相当の設定、4日目〜7日目ノート参照)のため、画像入力時もGPUをほぼ使わない。
   ただし②の分析どおり「GPUを使わない=速い」わけではなく、むしろ画像の場合は精度も速度も
   悪化するため、VRAMが空いているからといってルーターに画像処理をさせる理由にはならない。

### 改善策(結論。実装はこれから)

- **専用vision対応モデルの追加導入は不要**(既存の`gemma4:26b`で足りる。④改善策・案A/Bは不採用)
- ルーターに「画像添付あり」を検知する軽い判定を追加し、**画像添付時は分類ロジックを経由せず
  強制的にDEEPへルーティングする**(ルーター自体に画像を読ませない。④改善策の案3を
  「vision専用モデルへ」から「既存DEEPへ」に読み替えて採用)
- `ollama_client.generate()`は今回`images`引数を追加済み(後方互換を維持。テスト
  `tests/test_ollama_stream.py::TestGenerateImages`で確認済み)。DEEPルートは通常
  ストリーミングのため、本実装時は`generate_stream()`にも同様の`images`対応を追加する必要がある
- VRAM逼迫(残り約450〜600MiB)を踏まえ、画像添付時はFAST/CODEモデルを一時的に解放
  (`ollama stop`)してからDEEPを呼ぶなど、通常のテキスト会話より慎重なVRAM管理を検討する
- `static/index.html`・`voice_gateway.py`への画像アップロードUI・受信処理の実装(③のUI方針と
  合わせて、④で実装済みの📎ボタン・ナレッジ一覧パネルと統一感のある形にする)

---

## ④-2 画像添付時はDEEPへ強制ルーティングの実装(2026-08-14)

### 背景/目的

④-1で「専用vision対応モデルの追加導入は不要(既存の`gemma4:26b`で足りる)」「ルーター(`gemma4-e4b-cpu`)へ画像を渡すのは精度・速度の両面で不利」という結論が出たため、その結論どおり「画像添付時はルーターを経由せず強制的にDEEPへルーティングする」実装(`router.py`の分岐追加・`ollama_client.generate_stream()`への`images`対応・アップロードUI)に着手した。

### 設計(実装前にユーザーへ提示し承認を得た方針)

既存の`router.py`/`ollama_client.py`/`support_ai_auto_pipe.py`/`voice_gateway.py`/`static/index.html`という既存フローへの変更のみで完結する**bounded**な変更として、以下の5点をセットで実装する方針とした。

1. **`router.py`**: `RouterSession.get_route()`に`force_route: str | None = None`引数を追加。指定時はルールベース判定もgemma4-e4b-cpu呼び出しもスキップし、即座にそのrouteを返してセッション状態に記録する(ルーター自体に画像を読ませない土台)。
2. **`ollama_client.py`**: `generate_stream()`に`generate()`と同じ`images: list[str] | None = None`引数を追加。指定時のみリクエストボディに`images`を含める(未指定時は挙動不変)。
3. **`support_ai_auto_pipe.py`**: 新規`_extract_last_user_images(body)`を追加。最後のuserメッセージ辞書の`images`キー(base64文字列のlist、OpenWebUIの`content`list形式とは別の**独自convention**)を読む。`pipe()`内でこれを使い、画像があれば`session.get_route(..., force_route="DEEP")`でルーターを経由せず強制DEEP判定にし、`images`を`_stream_reply()`/`generate()`まで引き回す。
4. **`voice_gateway.py`**: `run_turn()`に`images: list[str] | None = None`引数を追加し、`pipe.pipe(body=...)`の最後のuserメッセージへ`"images"`キーとして載せる。WS `text_input`ハンドラで`payload.get("images")`(base64文字列list、data URL prefixなし)を読み取り、`run_turn()`まで渡す。
5. **`static/index.html`**: 既存の📎ボタン(PDF/Word等をRAGへ永続登録する別経路)とは別に、新規📷ボタン+`accept="image/*"`の隠しfile inputを追加。選択した画像をFileReaderでbase64化(data URL prefix除去)し「送信待ち」チップとして表示、送信時に`text_input`メッセージへ`images`配列として同梱、送信後にクリアする(ドキュメント添付とは異なり、画像はDBに永続登録せずそのターンだけのコンテキストとして扱う)。

### 実装結果(2026-08-14実施)

上記設計どおりに実装した。

- `router.py`: `RouterSession.get_route()`へ`force_route`引数を追加(キーワード専用引数)。`force_route`指定時は`match_rule_based()`も`call_model`も一切呼ばれず、`_last_route[session_id]`へ記録して即返す。次ターンでは通常どおり`last_route`として文脈に渡り、「話題が続いていればDEEPのまま」という既存の継続性ロジックにそのまま乗る。
- `ollama_client.py`: `generate_stream()`へ`images`引数を追加(`generate()`と対称な実装。未指定/空リストならリクエストボディに`images`キー自体が付かない後方互換を維持)。
- `support_ai_auto_pipe.py`: `_extract_last_user_images()`を追加し、`pipe()`冒頭で`images = self._extract_last_user_images(body)` → `force_route = "DEEP" if images else None`として`session.get_route()`に渡すよう変更。ストリーミング経路(`_stream_reply()`)・非ストリーミング経路(`generate()`直接呼び出し)の両方に`images`を引き回した(DEEPが`streaming_mode="off"`等で非ストリーミング呼び出しになるケースも考慮)。
- `voice_gateway.py`: `run_turn()`に`images`引数を追加し、`pipe.pipe()`へ渡す`messages`の最後のuserメッセージへ`"images"`キーとして載せる(未指定なら従来どおり`content`のみの辞書のまま)。WSメッセージ仕様のdocstringも更新し、`text_input`が`images`(任意)を受け取れることを明記した。WS `text_input`ハンドラでは`payload.get("images")`をリスト型・文字列要素のみにサニタイズしてから`run_turn()`へ渡す。
- `static/index.html`: 📎(文書添付)の右隣に📷(画像添付)ボタンと`accept="image/*"`の隠しfile inputを追加。選択した画像は`FileReader.readAsDataURL()`→カンマ以降を切り出す形でdata URL prefixを除去してbase64化し、`pendingImages`配列に保持しつつ「📷 ファイル名(送信待ち)」チップ(✕ボタンで個別に送信対象から外せる)を表示する。`sendTextInput()`は`pendingImages`が空でなければ`text_input`メッセージへ`images`配列を同梱し、送信後に`clearPendingImages()`でチップと状態をクリアする。

### テスト

既存のユニットテスト方式(Ollama呼び出し・記憶レイヤーをフェイクに差し替える方式)を踏襲し、以下を追加した。

| ファイル | 追加したテストの主旨 |
|---|---|
| `tests/test_router.py` | `force_route`指定時に`match_rule_based`相当のルールもcall_modelも一切呼ばれないこと、次ターンへ`last_route`として正しく引き継がれること |
| `tests/test_ollama_stream.py` | `generate_stream()`の`images`引数がリクエストボディに反映されること、未指定/空リストなら`images`キー自体が付かない後方互換 |
| `tests/test_support_ai_auto_pipe.py` | 非ストリーミング経路(`streaming_mode="off"`)で画像添付時に強制DEEP・`generate()`へ`images`が転送されること、ルーターモデル(`call_phi4`)が一切呼ばれないこと |
| `tests/test_support_ai_auto_pipe_stream.py` | ストリーミング経路で画像添付時に強制DEEP・`generate_stream()`へ`images`が転送されること、画像無しの従来呼び出しは`images=None`のまま(後方互換) |
| `tests/test_voice_gateway.py` | `run_turn()`の`images`引数がuserメッセージへ`"images"`キーとして反映されること、画像無しなら`images`キー自体が付かない後方互換 |

`python -m pytest tests/ -q`で既存180件+新規25件(205件全件)が成功することを確認した(2026-08-14実施)。

### 残課題

- 実機確認(ブラウザで実際に📷ボタンから画像を添付して送信し、`[route: DEEP]`で応答が返ること、VRAMピークが④-1の単体計測(15705MiB)と同水準に収まることの確認)はまだ行っていない。次回セッションで実施する。
- ④-1の残課題どおり、画像添付時にFAST/CODEモデルを一時的に解放(`ollama stop`)してからDEEPを呼ぶといったVRAM管理の強化は今回のスコープ外(YAGNI。実機で問題が出てから対応する)。
- OpenWebUI標準の画像添付convention(`content`がlist形式になるケース)への対応は今回もスコープ外のまま(`_extract_last_user_text`は従来どおり非文字列contentを空文字扱いにする)。現状は`voice_gateway.py`経由の独自`images`convention専用。
- 複数枚の画像添付(`pendingImages`は複数保持できる実装だが、UI上でまとめて何枚も選ぶ動線や、DEEP側での複数画像同時解釈の精度は未検証)。

---

## 📌 次のステップ

1. **①CLIベンチと実機(結果3)の乖離原因を特定する(最優先)**: gpt-oss:20bのCUDAクラッシュ・低速化への対処(Ollamaアップデート確認・リトライ追加)、`pipe_factory()`の呼び出し方式の違いの検証。これが解決するまで「8日目約1分からの改善幅」は暫定値にとどめる
2. ②Web検索バックエンドを選定し、`web_search.py`を実装する
3. ③UIデザインの方向性(OpenWebUI機能の取捨選択・HUD要素の扱い)を確定する
4. ~~④PDF対応から着手し(着手コスト低)~~ → 2026-08-13完了(Word/Excel/PowerPointも合わせて実装済み)。
   ~~次は④-1「vision capabilityの有無を確認する」~~ → 2026-08-14完了(両モデルともvision対応・
   `gemma4:26b`が実用精度と確認、専用モデル追加は不要)。
   ~~次は「画像添付時はDEEPへ強制ルーティング」の実装(`router.py`の分岐追加・`generate_stream()`への
   `images`対応・アップロードUI)に着手する~~ → 2026-08-14実装完了(④-2参照。単体テスト205件成功)。
   次は④-2残課題の実機確認(ブラウザで📷ボタンから画像添付→DEEP応答・VRAM実測)に着手する
5. [[サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md|9日目]]⑦(Tailscale Serveへの載せ替え・外出時CODEルート方針)は①⑧完了後に着手する方針を継続
