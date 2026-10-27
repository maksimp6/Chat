(function () {
  "use strict";

  var micBtn = null;
  var voiceStatus = null;
  var waveformCanvas = null;
  var waveCtx = null;
  var sessionId = null;
  var audioContext = null;
  var mediaStream = null;
  var source = null;
  var processor = null;
  var pcmChunks = [];
  var recordedSamples = 0;
  var maxAudioBytes = 1024 * 1024;
  var recordingRate = 16000;
  var isRecording = false;
  var isConnecting = false;
  var isProcessing = false;
  var eventSource = null;
  var currentTranscript = "";
  var currentReply = "";
  var userMsgSent = false;
  var botMsgSent = false;
  var sessionError = "";
  var micConversationId = null;
  var micModel = null;
  var micTransport = "realtime";
  var micTimeout = null;
  var micGeneration = 0;
  var audioPlayer = null;
  var textSessions = new Map();

  function request(url, init, timeoutMs) {
    return window.LogosDispatcher.request(url, init, { timeoutMs: timeoutMs || 30000 });
  }

  async function jsonRequest(url, body) {
    var res = await request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    var data = await res.json();
    if (!res.ok || data.error) throw new Error(data.message || data.error || "HTTP " + res.status);
    return data;
  }

  function setStatus(text) {
    if (voiceStatus) voiceStatus.textContent = text;
  }

  function clearWaveform() {
    if (!waveCtx || !waveformCanvas) return;
    waveformCanvas.width = waveformCanvas.offsetWidth || 320;
    waveformCanvas.height = waveformCanvas.offsetHeight || 48;
    waveCtx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
  }

  function drawWaveform(level) {
    if (!waveCtx || !waveformCanvas) return;
    var w = waveformCanvas.width;
    var h = waveformCanvas.height;
    waveCtx.clearRect(0, 0, w, h);
    waveCtx.beginPath();
    for (var x = 0; x < w; x += 2) {
      var y = h / 2 + (Math.sin(x / 9) * level * h) / 2;
      if (x === 0) waveCtx.moveTo(x, y);
      else waveCtx.lineTo(x, y);
    }
    waveCtx.strokeStyle = "#4a90d9";
    waveCtx.lineWidth = 2;
    waveCtx.stroke();
  }

  async function wavFromPcm(chunks, inputRate) {
    var length = chunks.reduce(function (n, chunk) { return n + chunk.length; }, 0);
    var input = new Float32Array(length);
    var position = 0;
    chunks.forEach(function (chunk) { input.set(chunk, position); position += chunk.length; });
    // Realtime accepts PCM, not a browser-specific Ogg/WebM recorder container.
    // Resample explicitly because mobile AudioContext may ignore the requested rate.
    var outputRate = 16000;
    var outputLength = Math.floor(length * outputRate / inputRate);
    var samples = input;
    if (outputLength && inputRate !== outputRate) {
      var OfflineContext = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      if (!OfflineContext) throw new Error("Браузер не поддерживает преобразование частоты записи");
      var renderContext = new OfflineContext(1, outputLength, outputRate);
      var inputBuffer = renderContext.createBuffer(1, length, inputRate);
      inputBuffer.getChannelData(0).set(input);
      var renderSource = renderContext.createBufferSource();
      renderSource.buffer = inputBuffer;
      renderSource.connect(renderContext.destination);
      try {
        renderSource.start(0);
        samples = (await renderContext.startRendering()).getChannelData(0);
      } finally {
        renderSource.disconnect();
        renderSource.buffer = null;
      }
    }
    var buffer = new ArrayBuffer(44 + outputLength * 2);
    var view = new DataView(buffer);
    function str(offset, value) {
      for (var i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
    }
    str(0, "RIFF");
    view.setUint32(4, 36 + outputLength * 2, true);
    str(8, "WAVE");
    str(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, outputRate, true);
    view.setUint32(28, outputRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    str(36, "data");
    view.setUint32(40, outputLength * 2, true);
    for (var n = 0; n < outputLength; n++) {
      var sample = Math.max(-1, Math.min(1, samples[n]));
      view.setInt16(44 + n * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  function playAudio(url) {
    if (audioPlayer) {
      audioPlayer.pause();
      audioPlayer.removeAttribute("src");
      audioPlayer.remove();
    }
    audioPlayer = new Audio(url);
    audioPlayer.controls = true;
    audioPlayer.preload = "auto";
    audioPlayer.style.maxWidth = "100%";
    // Keep controls available when Android requires another gesture for playback.
    if (voiceStatus && voiceStatus.parentNode) voiceStatus.parentNode.appendChild(audioPlayer);
    audioPlayer.play().catch(function () {
      setStatus("Аудио готово. Нажмите ▶ на плеере.");
    });
  }

  function releaseRecording() {
    if (processor) { processor.disconnect(); processor.onaudioprocess = null; processor = null; }
    if (source) { source.disconnect(); source = null; }
    if (mediaStream) {
      mediaStream.getTracks().forEach(function (track) { track.stop(); });
      mediaStream = null;
    }
    if (audioContext) { audioContext.close().catch(function () {}); audioContext = null; }
    clearWaveform();
  }

  function cancelServer(id) {
    if (!id) return Promise.resolve();
    return jsonRequest("/api/voice/close", { session_id: id, cancel: true }).catch(function () {});
  }

  function closeMicEvents() {
    if (eventSource) { eventSource.close(); eventSource = null; }
  }

  function handleServerEvent(data) {
    var viewingSession = typeof currentConvId !== "undefined" && currentConvId === micConversationId;
    if (!viewingSession && data.type !== "response.done") return;
    if (data.type === "input_audio_buffer.speech_stopped") {
      setStatus(micTransport === "speechkit_pipeline" ? "SpeechKit распознаёт речь для выбранной текстовой модели..." : "Realtime обрабатывает запись...");
    } else if (data.type === "conversation.item.input_audio_transcription.completed") {
      currentTranscript = window.LogosWikifunctions.trim(String(data.transcript || ""));
      if (currentTranscript && !userMsgSent && typeof addMessage === "function") {
        addMessage({
          text: currentTranscript,
          role: "user",
          id: data.item_id ? "voice-" + data.item_id : null,
        });
        userMsgSent = true;
      }
      setStatus("Вы: " + currentTranscript);
    } else if (data.type === "response.output_text.done") {
      currentReply = data.text || "";
      if (currentReply && !botMsgSent && typeof addMessage === "function") {
        addMessage({
          text: currentReply,
          role: "bot",
          id: data.response_id ? "voice-reply-" + data.response_id : null,
        });
        botMsgSent = true;
      }
    } else if (data.type === "response.output_audio.ready") {
      playAudio(data.audio_url);
    } else if (data.type === "error") {
      sessionError = (data.error || {}).message || "Ошибка Realtime API";
      setStatus(sessionError);
    } else if (data.type === "response.done") {
      if (viewingSession) setStatus(sessionError || (data.status === "completed" ? "Ответ готов" : "Ответ не завершён"));
      closeMicEvents();
      if (micTimeout) { window.clearTimeout(micTimeout); micTimeout = null; }
      sessionId = null;
      micConversationId = null;
      micModel = null;
      isProcessing = false;
    }
  }

  function micContextMatches() {
    return typeof currentConvId !== "undefined" && currentConvId === micConversationId &&
      typeof currentModel !== "undefined" && currentModel === micModel;
  }

  function micUploadActive(id) {
    if (sessionId !== id || !isProcessing) return false;
    if (micContextMatches()) return true;
    cancelMic();
    setStatus("Голосовая сессия отменена: выбран другой диалог или модель");
    return false;
  }

  async function startRecording(generation) {
    var stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    if (generation !== micGeneration || !micContextMatches()) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      if (generation === micGeneration) cancelMic();
      return;
    }
    var AudioContextClass = window.AudioContext || window.webkitAudioContext;
    var context;
    try {
      context = new AudioContextClass({ sampleRate: 16000 });
      if (context.state === "suspended") await context.resume();
    } catch (error) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      if (context) context.close().catch(function () {});
      throw error;
    }
    if (generation !== micGeneration || !micContextMatches()) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      context.close().catch(function () {});
      if (generation === micGeneration) cancelMic();
      return;
    }
    mediaStream = stream;
    audioContext = context;
    recordingRate = audioContext.sampleRate;
    source = audioContext.createMediaStreamSource(mediaStream);
    processor = audioContext.createScriptProcessor(4096, 1, 1);
    pcmChunks = [];
    recordedSamples = 0;
    processor.onaudioprocess = function (event) {
      if (!isRecording) return;
      if (typeof currentConvId !== "undefined" && (currentConvId !== micConversationId || currentModel !== micModel)) {
        cancelMic();
        return;
      }
      var input = event.inputBuffer.getChannelData(0);
      var remaining = Math.floor((maxAudioBytes - 44) / 2 * recordingRate / 16000) - recordedSamples;
      var copy = new Float32Array(Math.max(0, Math.min(input.length, remaining)));
      copy.set(input.subarray(0, copy.length));
      pcmChunks.push(copy);
      recordedSamples += copy.length;
      var sum = 0;
      for (var i = 0; i < input.length; i++) sum += input[i] * input[i];
      drawWaveform(Math.min(1, Math.sqrt(sum / input.length) * 5));
      if (remaining <= input.length) toggleVoice();
    };
    source.connect(processor);
    processor.connect(audioContext.destination);
    isRecording = true;
    isConnecting = false;
    if (micBtn) micBtn.classList.add("recording");
    setStatus((micTransport === "speechkit_pipeline" ? "SpeechKit → выбранная текстовая модель. " : "Realtime. ") + "Говорите; повторное нажатие отправит запись.");
    eventSource = new EventSource("/api/voice/events?session_id=" + encodeURIComponent(sessionId));
    eventSource.onmessage = function (event) {
      try { handleServerEvent(JSON.parse(event.data)); }
      catch (err) { setStatus("Не удалось прочитать событие голосовой сессии"); }
    };
    eventSource.onerror = function () {
      // EventSource resumes from Last-Event-ID; the backend retains its event log.
      if (sessionId && isProcessing) setStatus("Восстанавливаю события голосового ответа...");
      if (eventSource && eventSource.readyState === EventSource.CLOSED) {
        cancelMic();
        setStatus("События голосовой сессии недоступны");
      }
    };
  }

  async function toggleVoice() {
    if (isRecording) {
      isRecording = false;
      isProcessing = true;
      if (micBtn) micBtn.classList.remove("recording");
      setStatus("Передаю запись в Realtime...");
      var id = sessionId;
      var chunks = pcmChunks;
      var inputRate = recordingRate;
      releaseRecording();
      pcmChunks = [];
      try {
        var blob = await wavFromPcm(chunks, inputRate);
        if (!micUploadActive(id)) return;
        if (!blob.size || blob.size <= 44) throw new Error("Запись голоса пуста");
        var audioBytes = await blob.arrayBuffer();
        if (!micUploadActive(id)) return;
        var res = await request("/api/voice/audio?session_id=" + encodeURIComponent(id), {
          method: "POST", headers: { "Content-Type": "audio/wav" }, body: audioBytes,
        });
        if (!micUploadActive(id)) return;
        if (!res.ok) {
          var error = await res.json();
          throw new Error(error.error || "Не удалось передать запись");
        }
        await jsonRequest("/api/voice/close", { session_id: id });
      } catch (err) {
        if (sessionId !== id) return;
        closeMicEvents();
        if (micTimeout) { window.clearTimeout(micTimeout); micTimeout = null; }
        sessionId = null;
        micConversationId = null;
        micModel = null;
        isProcessing = false;
        setStatus(err.message || "Не удалось передать запись");
        await cancelServer(id);
      }
      return;
    }
    if (isConnecting || isProcessing) return;
    var model = typeof currentModel !== "undefined" ? currentModel : "";
    if (model === "speech-realtime-tts-live") {
      setStatus("Speech TTS Live: введите текст и нажмите отправку для озвучивания.");
      return;
    }
    var textModel = typeof modelsData !== "undefined" && modelsData.text && modelsData.text[model];
    if (!textModel && ["speech-realtime-deepseek-v4-flash", "speech-realtime-max-tts-live"].indexOf(model) === -1) {
      setStatus("Для микрофона выберите текстовую или голосовую модель.");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("Микрофон недоступен: требуется поддерживаемый браузер и безопасное подключение");
      return;
    }
    isConnecting = true;
    var generation = ++micGeneration;
    micConversationId = typeof currentConvId !== "undefined" && currentConvId ? currentConvId : null;
    micModel = model;
    setStatus("Подключение...");
    try {
      if (typeof currentConvId === "undefined" || !currentConvId) {
        if (typeof createConversation !== "function") throw new Error("Создайте диалог для голосового запроса");
        var created = await createConversation(model);
        if (generation !== micGeneration) return;
        if (!created) throw new Error("Не удалось создать диалог");
        if (currentConvId !== created) throw new Error("Выбран другой диалог; начните запись в нём");
      }
      if (currentModel !== model) throw new Error("Модель изменена; начните запись с выбранной моделью");
      micConversationId = currentConvId;
      micModel = model;
      micTransport = textModel ? "speechkit_pipeline" : "realtime";
      var params = typeof window.getResponsesParams === "function" ? window.getResponsesParams() : {};
      var data = await jsonRequest("/api/voice/session", {
        model: model,
        transport: micTransport,
        conversation_id: micConversationId,
        instructions: params.instructions,
        response_mode: (document.getElementById("response-mode-select") || {}).value || "audio",
      });
      if (generation !== micGeneration || !micContextMatches()) {
        if (generation === micGeneration) cancelMic();
        await cancelServer(data.session_id);
        return;
      }
      sessionId = data.session_id;
      var startedSession = sessionId;
      micTimeout = window.setTimeout(function () {
        if (sessionId !== startedSession) return;
        cancelMic();
        setStatus("Истекло время голосовой сессии");
      }, data.ttl_seconds * 1000);
      maxAudioBytes = data.max_audio_bytes;
      currentTranscript = "";
      currentReply = "";
      userMsgSent = false;
      botMsgSent = false;
      sessionError = "";
      await startRecording(generation);
    } catch (err) {
      if (generation !== micGeneration) return;
      cancelMic();
      setStatus(err.message || "Не удалось начать голосовую сессию");
    }
  }

  async function sendText(options) {
    options = options || {};
    var text = String(options.text || "");
    if (!window.LogosWikifunctions.trim(text)) throw new Error("Введите текст для голосовой модели");
    var data = await jsonRequest("/api/voice/session", {
      model: options.model,
      conversation_id: options.conversationId,
      instructions: options.instructions,
      response_mode: options.responseMode || "both",
    });
    var id = data.session_id;
    return new Promise(function (resolve, reject) {
      var reply = "";
      var failure = "";
      var events = new EventSource("/api/voice/events?session_id=" + encodeURIComponent(id));
      var timeout = window.setTimeout(function () {
        finish(new Error("Истекло время голосовой сессии"));
        cancelServer(id);
      }, data.ttl_seconds * 1000);
      function finish(error, result) {
        events.close();
        window.clearTimeout(timeout);
        textSessions.delete(id);
        if (error) reject(error);
        else resolve(result);
      }
      textSessions.set(id, function () {
        finish(new Error("Голосовая сессия отменена"));
        cancelServer(id);
      });
      events.onmessage = function (event) {
        var payload;
        try { payload = JSON.parse(event.data); }
        catch (err) { finish(new Error("Некорректное событие Realtime")); cancelServer(id); return; }
        if (payload.type === "response.output_text.done") reply = payload.text || "";
        else if (payload.type === "response.output_audio.ready") {
          if (typeof currentConvId !== "undefined" && currentConvId === options.conversationId) playAudio(payload.audio_url);
        }
        else if (payload.type === "error") failure = (payload.error || {}).message || "Ошибка Realtime API";
        else if (payload.type === "response.done") {
          if (failure || payload.status !== "completed") {
            finish(new Error(failure || "Realtime не завершил ответ"));
          } else {
            finish(null, { reply: reply, usage: payload.usage || {}, model: payload.model,
                           conversation_id: payload.conversation_id, response_id: payload.response_id });
          }
        }
      };
      events.onerror = function () {
        if (events.readyState === EventSource.CLOSED) {
          finish(new Error("События голосовой сессии недоступны"));
          cancelServer(id);
        }
      };
      jsonRequest("/api/voice/text", { session_id: id, text: text }).catch(function (err) {
        finish(err);
        cancelServer(id);
      });
    });
  }

  function cancelMic() {
    micGeneration += 1;
    isRecording = false;
    isConnecting = false;
    isProcessing = false;
    if (micTimeout) { window.clearTimeout(micTimeout); micTimeout = null; }
    if (micBtn) micBtn.classList.remove("recording");
    releaseRecording();
    closeMicEvents();
    var id = sessionId;
    sessionId = null;
    micConversationId = null;
    micModel = null;
    cancelServer(id);
  }

  function cancel() {
    cancelMic();
    textSessions.forEach(function (abort) { abort(); });
    if (audioPlayer) audioPlayer.pause();
    setStatus("Голосовая сессия отменена");
  }

  function cancelMicOnSelectionChange() {
    if (!(sessionId || isRecording || isConnecting || isProcessing)) return;
    // Creating the microphone's first conversation selects it before createConversation resolves.
    if (isConnecting && !sessionId && micConversationId === null && currentModel === micModel) return;
    if (micContextMatches()) return;
    cancelMic();
    setStatus("Голосовая сессия отменена: выбран другой диалог или модель");
  }

  window.addEventListener("logos:conversation-selected", cancelMicOnSelectionChange);
  window.addEventListener("logos:model-changed", cancelMicOnSelectionChange);

  window.LogosVoice = { sendText: sendText, synthesizeText: sendText, cancel: cancel };
  document.addEventListener("DOMContentLoaded", function () {
    micBtn = document.getElementById("mic-btn");
    voiceStatus = document.getElementById("voice-status");
    waveformCanvas = document.getElementById("waveform");
    waveCtx = waveformCanvas ? waveformCanvas.getContext("2d") : null;
    if (micBtn) micBtn.addEventListener("click", toggleVoice);
    clearWaveform();
  });
})();
