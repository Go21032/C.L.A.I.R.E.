// ---- script block 0 ----
"use strict";

// --- 設定 -------------------------------------------------------------
const WS_URL = `ws://${location.host}/ws`;
const TARGET_SAMPLE_RATE = 16000;   // vad.py / stt_engine.py が前提とするレート(③で確定)
const SEND_CHUNK_MS = 100;          // ③実機比較結果⑥: 100msチャンクが最有利と確定した値

// --- DOM ----------------------------------------------------------------
const logEl = document.getElementById("log");
const stateBadge = document.getElementById("state-badge");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const micIndicator = document.getElementById("mic-indicator");
const footerText = document.getElementById("footer-text");
const textInput = document.getElementById("text-input");
const sendBtn = document.getElementById("send-btn");
const attachBtn = document.getElementById("attach-btn");
const fileInput = document.getElementById("file-input");
const attachChips = document.getElementById("attach-chips");
const pinnedChipArea = document.getElementById("pinned-chip-area");
const knowledgeBtn = document.getElementById("knowledge-btn");
const knowledgePanel = document.getElementById("knowledge-panel");
const knowledgeList = document.getElementById("knowledge-list");
const cancelTurnBtn = document.getElementById("cancel-turn-btn");
const stopSpeechBtn = document.getElementById("stop-speech-btn");
const newSessionBtn = document.getElementById("new-session-btn");
const sessionSearchInput = document.getElementById("session-search-input");
const sessionHistoryEl = document.getElementById("session-history");

// --- 状態 ------------------------------------------------------------
let ws = null;
let audioCtx = null;
let micStream = null;
let micSource = null;
let micProcessor = null;
// 11日目④-1: 送信待ちの画像添付({name, base64, chip}のlist)。📎の文書添付と違い
// DBへ永続登録せず、次に送信するテキストと一緒に1回だけWSへ乗せて送信後にクリアする。
let pendingImages = [];
// 13日目「直近添付ファイルを自動優先」対応: 📎の文書アップロード(uploadDocument)が
// 成功した直後のファイル名を覚えておく変数。pendingImagesと同じ「次の送信1回だけ
// 同梱してクリアする」方式だが、こちらはアップロード自体は即座にDBへ永続登録済みなので
// 覚えておくのはファイル名の文字列だけでよい(base64本体を持ち回る必要が無い)。
// 「このファイルの内容をまとめて」のようなあいまいな依頼でも、直近1ファイルの記憶へ
// 優先的に絞り込んで検索できるようにするため(support_ai_auto_pipe.Pipe._recall()参照)。
let lastAttachedDocument = null;
// 14日目「添付ファイルをそのまま1ターンだけプロンプトへ埋め込む」対応。
// /documentsのレスポンスにtext(抽出全文。DOC_INLINE_MAX_CHARS以下の場合のみ)が
// 含まれていれば、ここへ覚えておく。lastAttachedDocumentと同じ「次の送信1回だけ
// 同梱してクリアする」方式(sticky厳禁)。閾値超え等でtextが無い場合はnullのままとし、
// その場合はattached_document(ファイル名)だけを送って従来どおりのRAGフォールバックに任せる。
let lastAttachedDocumentText = null;
// 14日目①: 📚一覧からのファイル指定(ピン留め)。解除するまで毎ターン対象になる
// (lastAttachedDocument/lastAttachedDocumentTextの「次の1回だけ」方式を置き換える)。
// localStorageにはファイル名のみ保存し、本文はサーバから取り直す(pinDocument参照)。
let pinnedDocument = localStorage.getItem("pinnedDocument") || null;  // ファイル名
let pinnedDocumentText = null;                                        // 全文(閾値超/未取得ならnull)
let lastKnownDocs = [];  // renderKnowledgeList()の再描画(ピン状態反映)用に直近の一覧を覚えておく

// 14日目②: チャット履歴の永続化(New Session・リネーム・削除・検索)。
// chat_id(RAG記憶のキー)とsession_id(画面の会話)を一致させるため、選択中のIDを
// localStorageへ持ち、WS接続のたびに"select_session"メッセージでサーバへ伝える(connect参照)。
let currentSessionId = localStorage.getItem("currentSessionId") || null;
let micMuted = false;           // 読み上げ中/推論中はtrueにしてサーバへ音声を送らない(エコー対策+10日目①)
let currentState = "idle";      // サーバから届いた直近の state 値(10日目①: ミュート判定に使う)
let assistantTurnEl = null;     // 現在組み立て中の応答表示<div>

// --- 13日目②③: ウェイクワード「送信ゲート」+自動送信(VAD) --------------------
// 10日目⑦で撤回した「呼ばなければ発話が消える」問題を、常時プレビュー(上記③⑦)を
// 一切変えずに「自動送信してよいかどうか」の判定にだけウェイクワードを使うことで回避する。
// 判定自体(表記ゆれの正規化・パターン照合)はサーバ側(wake_word.py)が行い、
// クライアントはサーバから届く"wake_detected"を見て状態(armed/disarmed)を管理するだけ。
const WAKE_ARM_TIMEOUT_MS = 15000;  // 呼びかけてから15秒発話が無ければ待機に戻す(呼びっぱなし防止)
const AUTO_SEND_GRACE_MS = 1500;    // 自動送信前の猶予。この間に入力欄を触ると取り消せる
let wakeEnabled = localStorage.getItem("wakeEnabled") === "1";
let wakeArmed = false;
let wakeArmTimer = null;
let autoSendEnabled = localStorage.getItem("autoSendEnabled") === "1";
let autoSendTimer = null;
let lastAutoSentText = "";       // 同じ確定テキストの二重送信を防ぐ
let webSearchEnabled = localStorage.getItem("webSearchEnabled") === "1";

// --- 16日目 修正2 → 17日目 修正: 停止ボタン(応答中断/読み上げ停止) -------------
// 16日目時点ではvoice_gateway.pyのWS受信ループが応答生成中(run_turn()のfor文)は
// ブロックされ、ブラウザから送った「止めて」メッセージをサーバーが受け取れない
// 構造上の制約があった。17日目でrun_turn()の消費をサーバー側の別スレッド+
// asyncio.Queueへ切り出し、受信ループが生成中も動き続けるよう修正したため、
// 今はcancel_turnメッセージが実際にOllamaへのストリーム接続を閉じ、応答生成
// そのもの(GPU計算)を打ち切れる(voice_gateway.py参照)。
// クライアント側では従来どおり「そのターンに関する残りのイベントを即座に無視する」
// ローカルの無視フラグも引き続き使う(サーバーからの中断応答を待たず、体感を
// 即座にする=WSの往復遅延を待たせないため)。裏でサーバーが送ってくる古いターンの
// 末尾のstate:idleを受け取った時点で無視フラグを解除し、次のターンを正しく扱える
// ようにする。
let stopRequested = false;       // trueの間、token/sentence/audio/state(idle以外)/errorを無視
let muteAudioForTurn = false;    // trueの間、audioイベントだけを無視(テキストは表示を続ける)

function cancelTurn() {
  stopRequested = true;
  muteAudioForTurn = false; // 全停止に包含されるので個別フラグは不要
  playbackQueue.length = 0;
  if (currentAudio) { try { currentAudio.pause(); } catch (e) { /* noop */ } }
  isPlaying = false;
  currentAudio = null;
  // 17日目: サーバー側の生成そのものを打ち切る要求を送る(上のコメント参照)。
  // WS未接続時は何もできないので送らない(ローカルの無視フラグだけで見た目は止まる)。
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "cancel_turn" }));
  }
  console.log(`[cancel-turn] ${new Date().toISOString()}: 応答を中断`);
  setState("idle");
}

function stopSpeechOnly() {
  muteAudioForTurn = true;
  playbackQueue.length = 0;
  if (currentAudio) { try { currentAudio.pause(); } catch (e) { /* noop */ } }
  isPlaying = false;
  currentAudio = null;
  console.log(`[stop-speech] ${new Date().toISOString()}: 読み上げを停止`);
  updateMicMute();
  updateTurnControlButtons();
}

function updateTurnControlButtons() {
  cancelTurnBtn.disabled = stopRequested || !(currentState === "thinking" || currentState === "speaking");
  stopSpeechBtn.disabled = muteAudioForTurn || !isPlaying;
}

cancelTurnBtn.addEventListener("click", cancelTurn);
stopSpeechBtn.addEventListener("click", stopSpeechOnly);

function armWake() {
  wakeArmed = true;
  micIndicator.classList.add("wake-armed");
  clearTimeout(wakeArmTimer);
  wakeArmTimer = setTimeout(disarmWake, WAKE_ARM_TIMEOUT_MS);
}

function disarmWake() {
  wakeArmed = false;
  micIndicator.classList.remove("wake-armed");
  clearTimeout(wakeArmTimer);
  wakeArmTimer = null;
}

// STTが発話を確定させる(partial_transcriptのfinal:true)たびに呼ぶ。
// 自動送信ON・入力欄が空でない・応答中でない、の3条件を満たしたときだけ、
// AUTO_SEND_GRACE_MS後に送信する。
// 猶予中に入力欄をクリック/編集するとtextInputのfocus/inputリスナーが
// autoSendTimerをclearするため、その回の自動送信は取り消される(誤送信の逃げ道)。
//
// 16日目 案1(15日目②からの再度の方針転換): 15日目②で「ウェイクワードON かつ
// 未受付」のときのブロックを撤去し、自動送信をウェイクワードの状態に関係なく常に
// 動かす方針にしていた。しかしこれは「自動送信ONにしていると、聞き取れた発話が
// ウェイクワードなしでも3秒黙るたびに勝手に送信される」という誤送信の実害があった。
// 相談の結果、誤送信防止を優先し、ウェイクワードONのときだけ「呼びかけて受付中
// (wakeArmed)でなければ自動送信しない」ゲートを再度入れることにした
// (15日目②で問題視されたUXの分かりにくさは、受付中バッジ(wake-armedクラス)による
// 視覚フィードバックが既にあるため許容する)。ウェイクワードOFF時は従来どおり
// 自動送信のみで動作する(退行なし)。
function scheduleAutoSend() {
  if (!autoSendEnabled) return;
  if (wakeEnabled && !wakeArmed) return; // 呼びかけられていないので送らない(案1)
  clearTimeout(autoSendTimer);
  autoSendTimer = setTimeout(() => {
    const text = textInput.value.trim();
    if (!text || text === lastAutoSentText) return;
    // 15日目③(指示2で発見・修正): 以前は`currentState !== "idle"`で判定していたが、
    // サーバは"listening"状態を一度も送ってこないため(voice_gateway.pyが送るのは
    // thinking/speaking/idleのみ)、接続直後〜最初の1ターンが終わるまではクライアント
    // ローカルのcurrentStateが"listening"のまま("idle"にならない)になり、実際には
    // 応答中でも何でもないのに自動送信が毎回黙って握りつぶされていた(再接続直後も同様)。
    // canSendText()と同じ「thinking/speaking中だけブロックする」判定に揃えて、
    // 意図どおり"応答中は割り込まない・それ以外は送る"にする。
    if (!canSendText()) return; // 応答中(thinking/speaking)・WS未接続時は割り込まない
    lastAutoSentText = text;
    console.log(`[auto-send] ${new Date().toISOString()}: "${text}"`); // いつ何を勝手に送ったか後から追えるように
    sendTextInput();
    disarmWake(); // 送信したら待機へ戻す(次のターンはまた呼びかけが必要)
  }, AUTO_SEND_GRACE_MS);
}

// --- 音声再生キュー(⑥「前の再生が終わってから次を再生する」の実装) -----------
const playbackQueue = [];
let isPlaying = false;
// 15日目(指示3): 読み上げ速度バーの値。VOICEVOX側のspeedScaleを都度作り直すのではなく、
// 再生中の<audio>のplaybackRateへ直接反映する(サーバ往復なしで即座に効く/生成済みの
// wavをそのまま使い回せる)。範囲はスライダーと同じ0.5〜2.0倍。
let playbackRate = 1.0;
let currentAudio = null; // 再生速度スライダーを動かした瞬間に「今流れている音声」へも反映するため保持

function enqueueAudio(wavBase64) {
  playbackQueue.push(wavBase64);
  if (!isPlaying) playNext();
}

function playNext() {
  const next = playbackQueue.shift();
  if (next === undefined) {
    isPlaying = false;
    currentAudio = null;
    updateMicMute(); // 再生キューが空になったので、state次第で聞き取りを再開する
    updateTurnControlButtons(); // 16日目 修正2: 再生が無くなったら読み上げ停止ボタンを無効化
    return;
  }
  isPlaying = true;
  updateMicMute(); // 読み上げ中はマイクOFF(9日目⑥仕様。エコー対策)
  updateTurnControlButtons(); // 16日目 修正2: 再生中は読み上げ停止ボタンを有効化

  const audio = new Audio(`data:audio/wav;base64,${next}`);
  audio.playbackRate = playbackRate; // 15日目(指示3): 読み上げ速度バーの現在値を毎回適用する
  currentAudio = audio;
  audio.addEventListener("ended", playNext);
  audio.addEventListener("error", (e) => {
    logError(`audio再生に失敗: ${e?.message || e}`);
    playNext();
  });
  audio.play().catch((e) => {
    logError(`audio.play()に失敗(自動再生ブロックの可能性): ${e}`);
    playNext();
  });
}

function setMicMuted(muted) {
  micMuted = muted;
  micIndicator.classList.toggle("active", !muted && !!micStream);
}

// 10日目①:⓪で特定した根本原因の直接修正。以前は「音声再生中(isPlaying)」しか
// ミュートしておらず、state:thinking(LLM推論中、まだ音声が1つも届いていない区間)は
// 無防備でマイクが開いたままだった。ここで「再生中」と「state:thinking/speaking」の
// 両方を見て一元的にミュート判定する(playNext()・setState()の両方から呼ぶ)。
function updateMicMute() {
  const shouldMute = isPlaying || currentState === "thinking" || currentState === "speaking";
  setMicMuted(shouldMute);
}

// --- ログ表示 ----------------------------------------------------------
function appendTurn(text, cls) {
  const div = document.createElement("div");
  div.className = `turn ${cls}`;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function logError(message) {
  appendTurn(`[error] ${message}`, "assistant error");
  setState("error");
}

function setState(state) {
  currentState = state;
  stateBadge.textContent = state;
  stateBadge.dataset.state = state;
  updateMicMute();       // 10日目①: thinking/speaking突入・離脱のたびにミュート判定をやり直す
  updateTextInputEnabled(); // 10日目③: 音声と同じ排他制御をテキスト入力欄にも効かせる
  updateTurnControlButtons(); // 16日目 修正2: thinking/speaking突入・離脱で応答中断ボタンも切り替える
}

// --- WebSocketメッセージ処理 ---------------------------------------------
// voice_gateway.py のWSメッセージ仕様(5種類+error):
//   partial_transcript / final_transcript / token / sentence / audio / state / error
//
// voice_gateway.py側の10日目⑦の設計により、この2種類は役割がはっきり分かれている:
//   - partial_transcript: Voskの暫定認識 と faster-whisperの確定認識の両方がここに乗る
//     (サーバはAIへ渡すかどうかをまだ決めていない、単なる書き起こしのプレビュー)。
//     テキスト入力欄(#text-input)へリアルタイムに反映するだけ。
//   - final_transcript: ユーザーが送信ボタン/Enterでtext_inputを送り、サーバが実際に
//     AIへの処理を開始したことの通知。画面上部のログへ「ユーザー発言」として表示するのは
//     このケースだけ(sendTextInput()参照)。
function handleMessage(msg) {
  // 16日目 修正2: 応答中断(cancelTurn)を押した後も、voice_gateway.pyのWS受信ループが
  // 応答生成中はブロックされる構造上、サーバーは裏でその古いターンの処理を最後まで
  // 続けてイベントを送ってくる。ここでそれを画面/音声へ反映せず捨てる。
  // final_transcript(新しいターンが実際に始まった合図)か、そのターンの末尾の
  // state:idle(古いターンの後始末が終わった合図)が届いたら無視フラグを解除する。
  if (msg.type === "final_transcript") {
    stopRequested = false;
    muteAudioForTurn = false;
  } else if (msg.type === "state" && msg.value === "idle") {
    stopRequested = false;
    muteAudioForTurn = false;
  } else if (stopRequested && msg.type !== "partial_transcript" && msg.type !== "wake_detected") {
    return; // token/sentence/audio/state(thinking/speaking)/errorは無視(mic由来の2種は継続)
  }

  switch (msg.type) {
    case "partial_transcript":
      // 話している最中(暫定)〜発話の区切りが確定した直後まで、常にここが呼ばれる。
      // 話し始めたら常にリアルタイムで入力欄へ反映する(ブラウザ側での編集途中に
      // 次の発話で上書きされうる点は既知のトレードオフ。10日目⑦で意図的に選んだ挙動)。
      textInput.value = msg.text;
      // 13日目③: final(=①の3秒無音でVAD+faster-whisperが確定させた瞬間)になったら
      // 自動送信の候補として扱う(実際に送るかどうかはscheduleAutoSend内の条件次第)。
      if (msg.final) scheduleAutoSend();
      break;

    case "wake_detected":
      // 13日目②: サーバ(wake_word.py)が「クレア/ねえクレア」を検出した通知。
      // ウェイクワードトグルがONのときだけ受付中(armed)にする(OFFなら無視。
      // 常時プレビューはこのイベントの有無に関わらず既に上のcaseで反映済み)。
      if (wakeEnabled) {
        armWake();
        // 17日目「ウェイクワードの文字が次のコマンドに混入する」対応:
        // 直前のpartial_transcriptケースで入力欄には「クレア 明日の天気は」のように
        // ウェイクワード込みの生テキストが入っている。ここでサーバが計算済みの
        // text_after(ウェイクワードより後ろの本文。wake_word.py参照)へ置き換えることで、
        // 呼びかけの文字自体を入力欄の表示・以降の手動送信/自動送信の両方から除く。
        // partial_transcriptとwake_detectedは同じfeed_audio()呼び出し内でこの順に
        // 送られてくる(voice_gateway.pyの_check_wake_word呼び出し順)ため、
        // 上書きの順序は保証されている。
        textInput.value = msg.text_after;
      }
      break;

    case "final_transcript":
      // サーバが実際にAIへの処理を開始した(=送信が受理された)ことの通知。
      appendTurn(msg.text, "user");
      assistantTurnEl = null; // 新しいターンなので応答表示をリセット
      break;

    case "token":
      if (!assistantTurnEl) assistantTurnEl = appendTurn("", "assistant");
      assistantTurnEl.textContent += msg.text;
      break;

    case "sentence":
      // トークン単位表示が無い経路(CODE/CLARIFY等、strで返るルート)のための保険。
      if (!assistantTurnEl) assistantTurnEl = appendTurn("", "assistant");
      if (!assistantTurnEl.textContent.includes(msg.text)) {
        assistantTurnEl.textContent += msg.text;
      }
      break;

    case "audio":
      // 16日目 修正2: 読み上げ停止ボタン(stopSpeechOnly)で止めたターンの残り音声は
      // 再生キューへ積まない(テキストのログ表示・状態遷移は止めずに継続させる)。
      if (muteAudioForTurn) break;
      enqueueAudio(msg.wav_b64);
      break;

    case "state":
      setState(msg.value);
      break;

    case "error":
      logError(`[${msg.stage}] ${msg.message}`);
      break;

    default:
      console.warn("unknown message type:", msg);
  }
}

// --- マイク取得・PCM16変換・WS送信 ---------------------------------------
// getUserMediaはセキュアコンテキスト(https または localhost)でしか動かない
// (⑥ノート注意点。スマホからは⑦のTailscale Serve HTTPS経由でアクセスすること)。
async function startMic() {
  if (!navigator.mediaDevices?.getUserMedia) {
    logError("このブラウザ/接続ではマイクが使えません(https または localhost が必要)");
    return;
  }

  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });

  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  micSource = audioCtx.createMediaStreamSource(micStream);

  // ScriptProcessorNodeは非推奨だが、追加のワークレットファイル無しで
  // 依存を増やさずに実装できるため初版はこちらを使う(YAGNI)。
  const bufferSize = 4096;
  micProcessor = audioCtx.createScriptProcessor(bufferSize, 1, 1);

  let pcmAccumulator = new Float32Array(0);
  const samplesPerChunk = Math.round((audioCtx.sampleRate * SEND_CHUNK_MS) / 1000);

  micProcessor.onaudioprocess = (event) => {
    if (micMuted || !ws || ws.readyState !== WebSocket.OPEN) return;

    const input = event.inputBuffer.getChannelData(0);
    const merged = new Float32Array(pcmAccumulator.length + input.length);
    merged.set(pcmAccumulator);
    merged.set(input, pcmAccumulator.length);
    pcmAccumulator = merged;

    while (pcmAccumulator.length >= samplesPerChunk) {
      const chunk = pcmAccumulator.slice(0, samplesPerChunk);
      pcmAccumulator = pcmAccumulator.slice(samplesPerChunk);
      const pcm16 = downsampleAndEncode(chunk, audioCtx.sampleRate, TARGET_SAMPLE_RATE);
      ws.send(pcm16.buffer);
    }

    // マイクレベル表示(ざっくりRMS)
    let sumSq = 0;
    for (let i = 0; i < input.length; i++) sumSq += input[i] * input[i];
    const rms = Math.sqrt(sumSq / input.length);
    micIndicator.classList.toggle("active", rms > 0.01);
  };

  micSource.connect(micProcessor);
  micProcessor.connect(audioCtx.destination);

  updateMicMute();
  footerText.textContent = "聞き取り中...";
}

// マイクの停止ボタン(10日目追加分)。誤動作時・離席時などにマイク入力そのものを
// 止められるようにする。WebSocket接続自体は維持し(サーバ側のセッションは保つ)、
// ローカルのマイクキャプチャだけを止める。再開は「マイクを開始」ボタンから行う。
function stopMic() {
  if (micProcessor) {
    micProcessor.disconnect();
    micProcessor.onaudioprocess = null;
    micProcessor = null;
  }
  if (micSource) {
    micSource.disconnect();
    micSource = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }
  setMicMuted(true);
  micIndicator.classList.remove("active");
  footerText.textContent = "マイクを停止しました";
}

// 線形補間による簡易リサンプル(48kHz等 → 16kHz) + Float32 → Int16 PCM変換。
// 音質より実装のシンプルさを優先(初版。耳障りならFIRフィルタ等へ差し替える)。
function downsampleAndEncode(float32, fromRate, toRate) {
  if (fromRate === toRate) {
    return floatToInt16(float32);
  }
  const ratio = fromRate / toRate;
  const newLength = Math.round(float32.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const srcIndex = i * ratio;
    const i0 = Math.floor(srcIndex);
    const i1 = Math.min(i0 + 1, float32.length - 1);
    const frac = srcIndex - i0;
    result[i] = float32[i0] * (1 - frac) + float32[i1] * frac;
  }
  return floatToInt16(result);
}

function floatToInt16(float32) {
  const int16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return int16;
}

// --- WebSocket接続 -------------------------------------------------------
// サーバ側の想定外の例外(STT/Pipe/TTSの一時的な失敗等)でWebSocketそのものが
// 落ちるケースがまだ完全には防ぎきれない(voice_gateway.py側でも極力catchしているが、
// ネットワーク瞬断等サーバ側の問題ではない切断もありうる)。過去の実機確認で
// 「切断されたらページ再読み込みしないとマイクが使えない」という体験の悪さが
// 見つかったため、意図的な切断でない限り自動的に再接続する。
let reconnectAttempts = 0;
let intentionalClose = false;

function connect() {
  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    reconnectAttempts = 0;
    footerText.textContent = "接続しました";
    setState("listening");
    // 14日目②: 接続(再接続含む)のたびに、選択中のセッションIDをサーバへ伝え、
    // chat_id(RAG記憶のキー)をsession_idへ揃える。未選択(currentSessionId===null)なら
    // 送らず、サーバ側は従来どおりの使い捨てchat_idのままになる(=永続化されない)。
    if (currentSessionId) {
      ws.send(JSON.stringify({ type: "select_session", session_id: currentSessionId }));
    }
  };
  ws.onmessage = (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (e) {
      console.error("WSメッセージの解析に失敗:", e, event.data);
    }
  };
  ws.onerror = () => logError("WebSocket接続でエラーが発生しました");
  ws.onclose = () => {
    updateTextInputEnabled();
    if (intentionalClose) {
      footerText.textContent = "切断しました";
      setState("idle");
      return;
    }
    reconnectAttempts += 1;
    const delayMs = Math.min(1000 * reconnectAttempts, 5000);
    footerText.textContent = `切断されました。${(delayMs / 1000).toFixed(1)}秒後に再接続します...`;
    setState("idle");
    setTimeout(connect, delayMs);
  };
}

// --- テキスト入力欄(10日目③⑦) --------------------------------------------
// 音声認識結果のリアルタイムプレビュー(partial_transcript/final_transcript、上の
// handleMessage参照)を表示しつつ、キーボードでの修正・手動送信を受け付ける唯一の
// 入力確定経路。音声由来・キーボード入力由来を問わず、ここで送信ボタン/Enterを
// 押さない限りAIへは一切送られない(10日目⑦でウェイクワード自動送信を撤回したため)。
// state:thinking/speakingの間は送信不可にする(音声と同じターンの排他制御)。
function canSendText() {
  return !!ws && ws.readyState === WebSocket.OPEN && currentState !== "thinking" && currentState !== "speaking";
}

function updateTextInputEnabled() {
  const enabled = canSendText();
  textInput.disabled = !enabled;
  sendBtn.disabled = !enabled;
}

function sendTextInput() {
  const text = textInput.value.trim();
  if (!text || !canSendText()) return;
  const msg = { type: "text_input", text };
  if (pendingImages.length > 0) {
    msg.images = pendingImages.map((img) => img.base64);
  }
  // 13日目「直近添付ファイルを自動優先」対応: 直近📎アップロードしたファイル名があれば
  // このターンだけ同梱する。pendingImagesと同じく一度送ったら使い捨て(sticky厳禁。
  // 古いファイルへ以降の無関係な質問まで引きずられてしまうため)。
  // 14日目①: 📚一覧でピン留め中のファイルがあれば、解除されるまで毎ターン同梱する
  // (以前の「直後の1回だけ」方式はピン留めへ統合し、置き換えた)。
  if (pinnedDocument) {
    msg.attached_document = pinnedDocument;              // RAGのsource絞り込み用
    if (pinnedDocumentText) {
      msg.attached_document_text = pinnedDocumentText;   // 全文をそのままプロンプトへ埋め込む経路
    }
  } else if (lastAttachedDocument) {
    // 後方互換: ピン留めしていない場合でも、直近アップロード分は従来どおり1回だけ同梱する
    // (通常はuploadDocument()が成功時に自動ピン留めするため、この分岐へは来ない想定)。
    msg.attached_document = lastAttachedDocument;
    if (lastAttachedDocumentText) {
      msg.attached_document_text = lastAttachedDocumentText;
    }
  }
  // 13日目④: Web検索トグルON時だけ、このターンのweb_search要求をサーバへ伝える
  // (voice_gateway.py→Pipe側でこのフラグが立っているときだけSearXNGを叩く)。
  if (webSearchEnabled) {
    msg.web_search = true;
  }
  ws.send(JSON.stringify(msg));
  textInput.value = "";
  clearPendingImages();
  // ★ pinnedDocument/pinnedDocumentTextはここでクリアしない(ピンが立っている限り毎ターン有効)
  lastAttachedDocument = null;
  lastAttachedDocumentText = null;
}

sendBtn.addEventListener("click", sendTextInput);
textInput.addEventListener("keydown", (e) => {
  // isComposing: IME変換中のEnter確定でうっかり送信しないようにする
  if (e.key === "Enter" && !e.isComposing) {
    e.preventDefault();
    sendTextInput();
  }
});

// --- ナレッジ添付(11日目④: PDF/Word/Excel/PowerPoint) ---------------------------
// voice_gateway.py の POST/GET /documents・DELETE /documents/{filename} を叩くだけの
// 独立した機能(WebSocket上の会話フローとは無関係。アップロード後は次回以降の
// 会話でRAG検索にヒットする形で反映される)。

function addAttachChip(filename, statusText, isError) {
  const chip = document.createElement("span");
  chip.className = "chip" + (isError ? " error" : "");
  chip.textContent = `📎 ${filename} — ${statusText}`;
  attachChips.appendChild(chip);
  return chip;
}

async function uploadDocument(file) {
  const chip = addAttachChip(file.name, "アップロード中...", false);
  const formData = new FormData();
  formData.append("file", file);
  try {
    const resp = await fetch("/documents", { method: "POST", body: formData });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }
    chip.textContent = `📎 ${file.name} — 登録済み(${body.chunks}チャンク)`;
    appendTurn(`[ナレッジ登録] ${file.name}(${body.chunks}チャンク)`, "assistant");
    // 14日目①: アップロード成功時に自動でピン留めする(従来の「次の1回だけ」の置き換え)。
    // /documentsのレスポンスに抽出全文(閾値以下の場合のみ)が既に含まれているため、
    // pinDocument()のように/documents/{filename}/textを取り直す必要はない。
    pinnedDocument = file.name;
    localStorage.setItem("pinnedDocument", file.name);
    pinnedDocumentText = body.text || null;
    renderPinnedChip();
    refreshKnowledgeList();
  } catch (e) {
    chip.classList.add("error");
    chip.textContent = `📎 ${file.name} — 失敗: ${e.message || e}`;
  }
}

attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  fileInput.value = ""; // 同じファイルを連続選択しても change が発火するようにリセット
  if (!file) return;
  // 12日目追記: 📷ボタン廃止に伴い、同じ📎(file-input)で画像も選べるようにした。
  // MIMEタイプが image/* なら旧📷と同じ「送信待ち」経路(DEEPへ強制ルーティング)、
  // それ以外(PDF/Word/Excel/PowerPoint)は従来どおりナレッジへ永続登録する。
  if (file.type.startsWith("image/")) {
    addPendingImage(file);
  } else {
    uploadDocument(file);
  }
});

// --- 画像添付(11日目④-1: 送信するとDEEPへ強制ルーティングされる) -----------------
// 📎の文書添付(/documents、DBへ永続登録)とは異なり、選択した画像はブラウザ側に
// 「送信待ち」として保持するだけで、次に送信ボタン/Enterを押したテキストと一緒に
// 1回だけWS(text_input.images)へ乗せて送る(voice_gateway.py→support_ai_auto_pipe.Pipe
// 側で、画像添付があればルーターを経由せず強制的にDEEPへルーティングされる)。

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      // dataURLは "data:image/png;base64,xxxx" の形なので、base64本体だけ取り出す
      // (Ollama /api/generate の images はdata URL prefix無しの生base64を期待する)。
      const dataUrl = reader.result || "";
      const commaIdx = dataUrl.indexOf(",");
      resolve(commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl);
    };
    reader.onerror = () => reject(reader.error || new Error("画像の読み込みに失敗しました"));
    reader.readAsDataURL(file);
  });
}

function renderPendingImageChip(entry) {
  const chip = document.createElement("span");
  chip.className = "chip";
  const label = document.createElement("span");
  label.textContent = `📷 ${entry.name}(送信待ち)`;
  const removeBtn = document.createElement("button");
  removeBtn.className = "remove-btn";
  removeBtn.textContent = "✕";
  removeBtn.title = "この画像を送信対象から外す";
  removeBtn.addEventListener("click", () => removePendingImage(entry));
  chip.appendChild(label);
  chip.appendChild(removeBtn);
  attachChips.appendChild(chip);
  entry.chip = chip;
}

function removePendingImage(entry) {
  pendingImages = pendingImages.filter((img) => img !== entry);
  entry.chip?.remove();
}

function clearPendingImages() {
  for (const entry of pendingImages) {
    entry.chip?.remove();
  }
  pendingImages = [];
}

async function addPendingImage(file) {
  try {
    const base64 = await fileToBase64(file);
    const entry = { name: file.name, base64 };
    pendingImages.push(entry);
    renderPendingImageChip(entry);
  } catch (e) {
    addAttachChip(file.name, `画像の読み込みに失敗: ${e.message || e}`, true);
  }
}

// --- ナレッジ一覧パネル(簡易版: ファイル名・チャンク数・削除ボタンのみ) -----------
async function refreshKnowledgeList() {
  try {
    const resp = await fetch("/documents");
    const docs = await resp.json();
    lastKnownDocs = docs;
    renderKnowledgeList(docs);
  } catch (e) {
    knowledgeList.innerHTML = `<div id="knowledge-empty">一覧の取得に失敗しました: ${e.message || e}</div>`;
  }
}

function renderKnowledgeList(docs) {
  knowledgeList.innerHTML = "";
  if (!docs || docs.length === 0) {
    knowledgeList.innerHTML = `<div id="knowledge-empty">登録済みのナレッジはありません</div>`;
    return;
  }
  for (const doc of docs) {
    const item = document.createElement("div");
    // 14日目①: クリックでピン留め/解除(「このファイルについて答えて」を成立させる中核)。
    item.className = "knowledge-item" + (doc.filename === pinnedDocument ? " pinned" : "");
    const info = document.createElement("div");
    info.className = "info";
    info.innerHTML = `<div class="name">${doc.filename}</div><div class="meta">${doc.chunks}チャンク・${doc.date}</div>`;
    info.addEventListener("click", () => {
      if (doc.filename === pinnedDocument) unpinDocument();  // 再クリックで解除
      else pinDocument(doc.filename);
    });
    const delBtn = document.createElement("button");
    delBtn.textContent = "削除";
    delBtn.addEventListener("click", () => deleteDocument(doc.filename));
    item.appendChild(info);
    item.appendChild(delBtn);
    knowledgeList.appendChild(item);
  }
}

async function deleteDocument(filename) {
  try {
    await fetch(`/documents/${encodeURIComponent(filename)}`, { method: "DELETE" });
    // 14日目①: ピン留め中のファイルが削除されたら、ピンも解除する
    // (存在しないファイルを対象にしたまま質問を続けてしまう事故を防ぐ)。
    if (filename === pinnedDocument) unpinDocument();
    refreshKnowledgeList();
  } catch (e) {
    logError(`ナレッジの削除に失敗: ${e.message || e}`);
  }
}

// --- ピン留め(14日目①: 📚一覧からの「対象ファイルのピン留め」) -----------------
// 「このファイルについて答えて」を成立させる中核。解除するまで毎ターン対象になる
// (sendTextInput参照)。localStorageにはファイル名のみ保存し、本文はサーバから
// 取り直す(リロードで消えると「なぜ効かなくなったか」が分からなくなるため)。
async function pinDocument(filename) {
  pinnedDocument = filename;
  localStorage.setItem("pinnedDocument", filename);
  pinnedDocumentText = null;
  renderPinnedChip();
  renderKnowledgeList(lastKnownDocs);
  try {
    const resp = await fetch(`/documents/${encodeURIComponent(filename)}/text`);
    if (resp.status === 404) { unpinDocument(); return; }  // 既に削除済み
    const body = await resp.json();
    pinnedDocumentText = body.text || null;  // nullなら閾値超え。RAG(source絞り込み)へフォールバック
  } catch (e) {
    logError(`添付全文の取得に失敗(RAG検索にフォールバックします): ${e.message || e}`);
  }
  renderPinnedChip();
}

function unpinDocument() {
  pinnedDocument = null;
  pinnedDocumentText = null;
  localStorage.removeItem("pinnedDocument");
  renderPinnedChip();
  renderKnowledgeList(lastKnownDocs);
}

function renderPinnedChip() {
  pinnedChipArea.innerHTML = "";
  if (!pinnedDocument) return;
  const chip = document.createElement("span");
  chip.className = "chip pinned-chip";
  const label = document.createElement("span");
  const mode = pinnedDocumentText ? "全文" : "検索";
  label.textContent = `📌 ${pinnedDocument}(${mode})について回答中`;
  const removeBtn = document.createElement("button");
  removeBtn.textContent = "✕";
  removeBtn.title = "ピン留めを解除する";
  removeBtn.addEventListener("click", unpinDocument);
  chip.appendChild(label);
  chip.appendChild(removeBtn);
  pinnedChipArea.appendChild(chip);
}

// ページ読み込み時にlocalStorageのピンを復元する(全文はサーバから取り直す)。
// ファイルが既に削除されていたらpinDocument()内でunpinDocument()が呼ばれ、黙って解除される。
if (pinnedDocument) {
  pinDocument(pinnedDocument);
}

knowledgeBtn.addEventListener("click", () => {
  const willOpen = !knowledgePanel.classList.contains("open");
  knowledgePanel.classList.toggle("open", willOpen);
  if (willOpen) refreshKnowledgeList();
});

// --- チャット履歴(14日目②: New Session・リネーム・削除・検索) --------------------
// voice_gateway.py の GET/POST/PATCH/DELETE /sessions を叩く。WebSocket上の会話フロー
// (text_input等)とは別立てのHTTP経路(ナレッジ一覧と同じ構成)。

function formatSessionMeta(iso) {
  if (!iso) return "—";
  // "2026-08-16T21:03:11" 形式(session_store._now_iso)から日時部分だけ簡潔に表示する
  return iso.replace("T", " ").slice(0, 16);
}

async function refreshSessionList(query = "") {
  try {
    const url = query ? `/sessions?q=${encodeURIComponent(query)}` : "/sessions";
    const resp = await fetch(url);
    const sessions = await resp.json();
    renderSessionList(sessions);
  } catch (e) {
    sessionHistoryEl.innerHTML = `<div class="hist-group">一覧の取得に失敗しました: ${e.message || e}</div>`;
  }
}

function renderSessionList(sessions) {
  sessionHistoryEl.innerHTML = "";
  if (!sessions || sessions.length === 0) {
    sessionHistoryEl.innerHTML = `<div class="hist-group">会話履歴はまだありません</div>`;
    return;
  }
  for (const s of sessions) {
    const item = document.createElement("div");
    item.className = "hist-item" + (s.session_id === currentSessionId ? " active" : "");

    const ti = document.createElement("div");
    ti.className = "ti";
    ti.textContent = s.title;
    item.appendChild(ti);

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `<span>${formatSessionMeta(s.updated_at)}</span>`;
    item.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "hist-actions";
    const renameBtn = document.createElement("button");
    renameBtn.className = "rename";
    renameBtn.title = "名前を変更";
    renameBtn.textContent = "✏";
    renameBtn.addEventListener("click", (e) => { e.stopPropagation(); renameSession(s.session_id, s.title); });
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "delete";
    deleteBtn.title = "削除";
    deleteBtn.textContent = "🗑";
    deleteBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteSession(s.session_id); });
    actions.appendChild(renameBtn);
    actions.appendChild(deleteBtn);
    item.appendChild(actions);

    item.addEventListener("click", () => selectSession(s.session_id));
    sessionHistoryEl.appendChild(item);
  }
}

async function selectSession(sessionId) {
  currentSessionId = sessionId;
  localStorage.setItem("currentSessionId", sessionId);
  // WS接続済みなら即座にサーバのchat_idを切り替える(未接続なら次のconnect() onopenで送られる)。
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "select_session", session_id: sessionId }));
  }
  try {
    const resp = await fetch(`/sessions/${encodeURIComponent(sessionId)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const s = await resp.json();
    logEl.innerHTML = "";  // 過去ログを画面へ復元する前に、今表示中のログをクリアする
    for (const t of s.turns) appendTurn(t.text, t.role === "user" ? "user" : "assistant");
    assistantTurnEl = null;
  } catch (e) {
    logError(`会話の読み込みに失敗: ${e.message || e}`);
  }
  refreshSessionList(sessionSearchInput.value.trim());
}

async function newSession() {
  try {
    const resp = await fetch("/sessions", { method: "POST" });
    const s = await resp.json();
    logEl.innerHTML = "";  // チャット表示をクリアする
    assistantTurnEl = null;
    await selectSession(s.session_id);
  } catch (e) {
    logError(`新規セッションの作成に失敗: ${e.message || e}`);
  }
}

async function renameSession(sessionId, currentTitle) {
  const newTitle = window.prompt("新しいタイトル", currentTitle);
  if (newTitle === null || !newTitle.trim()) return;  // キャンセル/空文字は無視
  try {
    await fetch(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: newTitle.trim() }),
    });
    refreshSessionList(sessionSearchInput.value.trim());
  } catch (e) {
    logError(`リネームに失敗: ${e.message || e}`);
  }
}

async function deleteSession(sessionId) {
  if (!window.confirm("この会話を削除しますか?(元に戻せません)")) return;
  try {
    await fetch(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    if (sessionId === currentSessionId) {
      // 表示中のセッション自体を消した場合は、選択状態も解除する(次のNew Session/選択待ち)。
      currentSessionId = null;
      localStorage.removeItem("currentSessionId");
      logEl.innerHTML = "";
    }
    refreshSessionList(sessionSearchInput.value.trim());
  } catch (e) {
    logError(`削除に失敗: ${e.message || e}`);
  }
}

newSessionBtn.addEventListener("click", newSession);

let sessionSearchTimer = null;
sessionSearchInput.addEventListener("input", () => {
  // 検索のたびに即fetchせず、少し待ってから叩く(連続入力での無駄な通信を減らす)
  clearTimeout(sessionSearchTimer);
  sessionSearchTimer = setTimeout(() => refreshSessionList(sessionSearchInput.value.trim()), 200);
});

// ページ読み込み時にセッション一覧を描画する。リロード時は同じセッションへ復帰する
// (currentSessionIdがlocalStorageに残っていれば、その過去ログも復元する)。
refreshSessionList();
if (currentSessionId) {
  selectSession(currentSessionId);
}

// --- 起動 -----------------------------------------------------------------
startBtn.addEventListener("click", async () => {
  startBtn.disabled = true;
  try {
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      intentionalClose = false;
      connect();
    }
    await startMic();
    stopBtn.disabled = false;
    updateTextInputEnabled();
  } catch (e) {
    logError(`マイクの初期化に失敗: ${e}`);
    startBtn.disabled = false;
  }
});

stopBtn.addEventListener("click", () => {
  stopMic();
  startBtn.disabled = false;
  stopBtn.disabled = true;
});

// ---- script block 1 ----
"use strict";

// === 外周リング 60本(細かいアーク目盛。装飾のみ) ===
(function buildRings(){
  const g = document.getElementById('rings');
  if (!g) return;
  const cx = 160, cy = 160, r = 148;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < 60; i++) {
    const a = (i / 60) * Math.PI * 2;
    const x1 = cx + Math.cos(a) * r;
    const y1 = cy + Math.sin(a) * r;
    const longTick = (i % 5 === 0);
    const inner = longTick ? r - 8 : r - 4;
    const x2 = cx + Math.cos(a) * inner;
    const y2 = cy + Math.sin(a) * inner;
    const line = document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1', x1.toFixed(2));
    line.setAttribute('y1', y1.toFixed(2));
    line.setAttribute('x2', x2.toFixed(2));
    line.setAttribute('y2', y2.toFixed(2));
    line.setAttribute('stroke-opacity', longTick ? 0.55 : 0.18);
    line.setAttribute('stroke-width', longTick ? 1.2 : 0.7);
    frag.appendChild(line);
  }
  g.appendChild(frag);
})();

// モデル選択ドロップダウンのクリック選択(見た目の切り替えのみ。機能未接続)
document.querySelectorAll('.model-opt').forEach(function(opt){
  opt.addEventListener('click', function(){
    document.querySelectorAll('.model-opt').forEach(function(o){ o.classList.remove('sel'); });
    opt.classList.add('sel');
    const details = opt.closest('details');
    const val = details.querySelector('.val');
    val.textContent = opt.querySelector('span').textContent;
    details.removeAttribute('open');
  });
});

// 13日目③: Controlsパネルのスイッチ。data-control属性を持つもの(wake/autosend/web)は
// localStorageへ永続化しつつ実処理へ配線する。それ以外(Obsidian RAG/思考モード等)は
// 12日目時点のまま見た目のトグルのみ(思考モードの機能化は13日目のスコープ外)。
document.querySelectorAll('.rightpanel .switch .toggle').forEach(function(sw){
  const switchEl = sw.closest('.switch');
  const key = switchEl ? switchEl.dataset.control : undefined;
  sw.addEventListener('click', function(){
    const on = sw.classList.toggle('on');
    if (key === 'wake') {
      wakeEnabled = on;
      localStorage.setItem('wakeEnabled', on ? '1' : '0');
      if (!on) disarmWake(); // OFFにしたら受付中も即解除
    } else if (key === 'autosend') {
      autoSendEnabled = on;
      localStorage.setItem('autoSendEnabled', on ? '1' : '0');
      if (!on) clearTimeout(autoSendTimer);
    } else if (key === 'web') {
      webSearchEnabled = on;
      localStorage.setItem('webSearchEnabled', on ? '1' : '0');
    }
  });
});

// 13日目②③: ページ読み込み時にlocalStorageの設定をトグルの見た目へ反映する
// (リロードのたびに設定し直さなくてよいようにする)。
(function restoreControlToggles(){
  const map = { wake: wakeEnabled, autosend: autoSendEnabled, web: webSearchEnabled };
  Object.keys(map).forEach(function(key){
    const el = document.querySelector('.rightpanel .switch[data-control="' + key + '"] .toggle');
    if (el) el.classList.toggle('on', map[key]);
  });
})();

// 13日目③: 自動送信の猶予中にユーザーが入力欄を触ったら、その回の自動送信は取り消す
// (誤送信の逃げ道。10日目⑦残課題「編集中に上書きされる」問題と同種の事故を防ぐ)。
textInput.addEventListener('focus', () => clearTimeout(autoSendTimer));
textInput.addEventListener('input', () => clearTimeout(autoSendTimer));

// === 読み上げ速度スライダー(15日目・指示3で機能化。再生中<audio>のplaybackRateへ反映) ===
(function rateSlider(){
  const range = document.getElementById('rate-range');
  const val = document.getElementById('rate-val');
  const ticks = document.querySelectorAll('.rate-ticks span');
  if (!range || !val) return;

  function apply(rate){
    if (!Number.isFinite(rate)) return; // 15日目②: キーボード入力が数値になっていない間は無視
    const r = Math.min(2.0, Math.max(0.5, rate));
    range.value = r;
    const pct = ((r - range.min) / (range.max - range.min)) * 100;
    range.style.setProperty('--rate-pct', pct.toFixed(1) + '%');
    val.value = r.toFixed(1) + 'x';
    ticks.forEach(function(t){
      t.classList.toggle('sel', Math.abs(parseFloat(t.dataset.rate) - r) < 0.001);
    });
    // 指示3: VOICEVOX側のspeedScaleを都度作り直す(=サーバ往復・再合成)方式ではなく、
    // ブラウザの<audio>.playbackRateへ直接反映する。次に再生される音声はplayNext()内で
    // 参照しているplaybackRateから自動的に効くが、「今まさに再生中」の音声にも
    // 即座に反映されるようcurrentAudioがあればそちらも直接書き換える。
    playbackRate = r;
    if (currentAudio) currentAudio.playbackRate = r;
    localStorage.setItem('playbackRate', String(r));
  }

  range.addEventListener('input', function(){ apply(parseFloat(range.value)); });
  ticks.forEach(function(t){
    t.addEventListener('click', function(){ apply(parseFloat(t.dataset.rate)); });
  });

  // 15日目②(指示3): 「1.0x」欄をキーボードでも編集できるようにする。
  // 入力中(まだ確定していない)は値を書き換えず、Enter/フォーカスアウトで確定させる。
  // "1.2" でも "1.2x" でも受け付ける("x"は末尾に付いていても剥がしてから数値化)。
  function commitTypedRate(){
    const parsed = parseFloat(val.value.replace(/x$/i, '').trim());
    apply(Number.isFinite(parsed) ? parsed : parseFloat(range.value)); // 不正入力時は直前の値へ戻す
  }
  val.addEventListener('keydown', function(e){
    if (e.key === 'Enter') { e.preventDefault(); commitTypedRate(); val.blur(); }
    if (e.key === 'Escape') { e.preventDefault(); apply(parseFloat(range.value)); val.blur(); }
  });
  val.addEventListener('blur', commitTypedRate);
  val.addEventListener('focus', function(){ val.select(); });

  // 指示3: リロードしても前回の速度設定を維持する(トグル類と同じlocalStorage方式)
  const savedRate = parseFloat(localStorage.getItem('playbackRate'));
  apply(Number.isFinite(savedRate) ? savedRate : parseFloat(range.value));
})();

