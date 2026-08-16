---
project: C.L.A.I.R.E.(さぽーとAI)
tags: [起動コマンド, 運用, まとめ]
---

> [!note] このノートについて
> [[サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md|9日目]]⑥・[[サポートAI作製計画/11日目Web検索対応・UIデザイン確定・マルチモーダル対応調査.md|11日目]]・[[サポートAI作製計画/13日目UI機能化(ウェイクワード・自動送信)とWeb検索実装.md|13日目]]・[[サポートAI作製計画/14日目添付ファイルのピン留め指定とチャット履歴の永続化.md|14日目]]に散らばっていた**自作AI(C.L.A.I.R.E.)を起動するためのコマンド**を1箇所にまとめたもの。各コマンドの背景・トラブルシュートは元ノートを参照。

## 起動順序(重要)

依存関係があるため、この順番で起動する(逆順だとエラーになる)。

1. **Ollama**(LLMモデルのロードに時間がかかるため最初)
2. **VOICEVOX ENGINE**(TTS。起動は速い)
3. **Open WebUI**(UIと記憶DB。Ollamaに依存)
4. **SearXNG**(Web検索。任意 — 検索機能を使わないなら省略可)
5. **voice_gateway.py**(自作音声UI。上記すべてに依存)
6. ブラウザで `http://127.0.0.1:5055/` を開く

## 前提条件一覧

| 名前 | ポート | 起動方法 | 確認方法 |
|---|---|---|---|
| **Ollama** | 11434 | `ollama serve`(Windows版は通常インストール直後からタスクトレイに常駐済みなので、基本は手動実行不要) | `curl http://127.0.0.1:11434/api/tags` → モデル一覧が返ればOK |
| **VOICEVOX ENGINE** | 50021 | GUIから起動(またはコンソール: `voicevox.exe` 等) | `curl http://127.0.0.1:50021/version` → バージョン文字列が返ればOK |
| **Open WebUI** | 8080 | 下記「① Open WebUI」参照 | `http://127.0.0.1:8080/` にブラウザアクセス |
| **SearXNG** | 8888 | 下記「② SearXNG(Web検索)」参照 | `curl "http://127.0.0.1:8888/search?q=test&format=json"` → JSONが返ればOK |
| **voice_gateway.py** | 5055 | 下記「③ voice_gateway.py(自作音声UI)」参照 | ブラウザで `http://127.0.0.1:5055/` を開く |

---

## ① Open WebUI

```powershell
# 初回のみ: インストール
pip install open-webui

# 起動(このターミナルは起動したまま維持する)
open-webui serve
```

起動すると `http://localhost:8080` でサーバーが立ち上がる。

> [!note]
> `ollama serve`を打って`bind: Only one usage of each socket address...`が出た場合は「Ollamaは既に起動済み」という意味なので、それ以上の対応は不要。

---

## ② SearXNG(Web検索)

ヴォールト外の別ディレクトリ(`C:\Users\gakuh\dev\searxng`)にネイティブ(Dockerなし)でセットアップ済み。Web検索機能を使わないなら起動しなくてもよい(voice_gateway側は未接続時でも黙って空検索結果を返す)。

```powershell
cd C:\Users\gakuh\dev\searxng
.\.venv\Scripts\Activate.ps1
$env:SEARXNG_SETTINGS_PATH = "C:\Users\gakuh\dev\searxng-instance\settings.yml"
python -m searx.webapp
```

別ターミナルで疎通確認:

```powershell
curl "http://127.0.0.1:8888/search?q=test&format=json"
```

> [!warning] `python searx\webapp.py`のようにスクリプト直接実行しない
> `import searx`が解決できず`ModuleNotFoundError`になる。必ずリポジトリルートから`python -m searx.webapp`と**モジュールとして**実行する。

---

## ③ voice_gateway.py(自作音声UI)

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python voice_gateway.py --host 127.0.0.1 --port 5055
```

ブラウザで開く:

```
http://127.0.0.1:5055/
```

(localhostはセキュアコンテキスト扱いなのでマイクが使える)

> [!note] 初回のみ: 依存インストール
> ```powershell
> pip install fastapi uvicorn[standard]
> ```

---

## 補足: Pipeの単体動作確認(voice_gatewayを介さない)

`support_ai_auto_pipe.Pipe`だけを直接呼んで疎通確認したいとき:

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python -c "import sys; sys.path.insert(0,'openwebui_pipe'); from support_ai_auto_pipe import Pipe; p=Pipe(); print(p.pipe(body={'messages':[{'role':'user','content':'こんにちは'}]}, __metadata__={'chat_id':'voice-001'}))"
```

## 補足: テスト実行

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python -m pytest tests/ -q
node --check static/_index_script_check.js   # index.html内JSの構文チェック
```

---

## 関連ノート

- [[サポートAI作製計画/4日目Phi4ロジック設計.md|4日目]]:Open WebUI導入
- [[サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md|9日目]]:voice_gateway.py新規実装・起動手順
- [[サポートAI作製計画/11日目Web検索対応・UIデザイン確定・マルチモーダル対応調査.md|11日目]]:SearXNGネイティブセットアップ
- [[サポートAI作製計画/13日目UI機能化(ウェイクワード・自動送信)とWeb検索実装.md|13日目]]:web_search.py実装
- [[サポートAI作製計画/14日目添付ファイルのピン留め指定とチャット履歴の永続化.md|14日目]]:voice_gateway.py起動コマンド(再掲)
