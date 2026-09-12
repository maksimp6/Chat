(function() {
    var micBtn = null;
    var voiceStatus = null;
    var waveformCanvas = null;
    var waveCtx = null;

    var sessionId = null;
    var audioContext = null;
    var mediaStream = null;
    var processor = null;
    var isRecording = false;
    var isConnecting = false;
    var eventSource = null;
    var audioOutCtx = null;
    var nextPlayTime = 0;
    var currentTranscript = "";
    var currentReply = "";
    var audioLevelPeaks = [];
    
    var userMsgSent = false;
    var botMsgSent = false;

    function log(msg) { console.log("[VOICE]", msg); }

    function setStatus(text) {
        log("Status: " + text);
        if (voiceStatus) voiceStatus.textContent = text;
    }

    function clearWaveform() {
        if (!waveCtx || !waveformCanvas) return;
        var w = waveformCanvas.width = waveformCanvas.offsetWidth;
        var h = waveformCanvas.height = waveformCanvas.offsetHeight;
        waveCtx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--bg-input') || "#f0f0f5";
        waveCtx.fillRect(0, 0, w, h);
    }

    function drawWaveform(level) {
        if (!waveCtx || !waveformCanvas) return;
        var w = waveformCanvas.width;
        var h = waveformCanvas.height;
        audioLevelPeaks.push(level);
        if (audioLevelPeaks.length > w) audioLevelPeaks.shift();
        
        waveCtx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--bg-input') || "#f0f0f5";
        waveCtx.fillRect(0, 0, w, h);
        
        waveCtx.strokeStyle = "#4a90d9";
        waveCtx.lineWidth = 2;
        waveCtx.beginPath();
        for (var i = 0; i < audioLevelPeaks.length; i++) {
            var x = i;
            var y = h / 2 - audioLevelPeaks[i] * h / 2;
            if (i === 0) waveCtx.moveTo(x, y);
            else waveCtx.lineTo(x, y);
        }
        waveCtx.stroke();
    }

    async function startRecording() {
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {channelCount: 1, sampleRate: 24000, echoCancellation: true, noiseSuppression: true}
            });
        } catch(err) {
            setStatus("Ошибка микрофона: " + err.message);
            isConnecting = false;
            return;
        }

        audioContext = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 24000});
        var source = audioContext.createMediaStreamSource(mediaStream);
        processor = audioContext.createScriptProcessor(4096, 1, 1);
        source.connect(processor);
        processor.connect(audioContext.destination);

        processor.onaudioprocess = function(e) {
            if (!sessionId || !isRecording) return;
            var input = e.inputBuffer.getChannelData(0);

            var sum = 0;
            for (var i = 0; i < input.length; i++) sum += input[i] * input[i];
            var rms = Math.sqrt(sum / input.length);
            drawWaveform(rms);

            var pcm = new Int16Array(input.length);
            for (var i = 0; i < input.length; i++) {
                var s = Math.max(-1, Math.min(1, input[i]));
                pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            fetch("/api/voice/audio?session_id=" + sessionId, {
                method: "POST",
                headers: {"Content-Type": "application/octet-stream"},
                body: pcm.buffer
            }).catch(function(err) { log("Send error: " + err.message); });
        };

        isRecording = true;
        isConnecting = false;
        if (micBtn) micBtn.classList.add("recording");
        setStatus("Говорите...");

        if (eventSource) eventSource.close();
        eventSource = new EventSource("/api/voice/events?session_id=" + sessionId);
        eventSource.onmessage = function(e) {
            try { handleServerEvent(JSON.parse(e.data)); } catch(err) { log("SSE parse: " + err.message); }
        };
        eventSource.onerror = function() { log("SSE error"); eventSource.close(); };
    }

    function handleServerEvent(data) {
        var type = data.type;

        if (type === "input_audio_buffer.speech_started") {
            setStatus("Слушаю...");
        }
        else if (type === "input_audio_buffer.speech_stopped") {
            setStatus("Обрабатываю...");
        }
        else if (type === "conversation.item.input_audio_transcription.completed") {
            currentTranscript = data.transcript || "";
            setStatus("Вы: " + currentTranscript);
            if (currentTranscript && !userMsgSent && typeof addMessage === "function") {
                addMessage(currentTranscript, "user", true, 0);
                userMsgSent = true;
            }
        }
        else if (type === "response.output_text.delta" || type === "response.audio_transcript.delta") {
            currentReply += (data.delta || "");
            if (voiceStatus) voiceStatus.textContent = "Ответ: " + currentReply.substring(0, 50) + "...";
        }
        else if (type === "response.output_text.done" || type === "response.audio_transcript.done") {
            if (data.text && !currentReply) currentReply = data.text;
        }
        else if (type === "response.done") {
            if (currentReply && !botMsgSent && typeof addMessage === "function") {
                addMessage(currentReply, "bot", true, 0);
                botMsgSent = true;
            }
            currentReply = "";
            currentTranscript = "";
            userMsgSent = false;
            botMsgSent = false;
            setStatus("Говорите...");
        }
        else if (type === "response.output_audio.delta") {
            if (data.delta) playAudioChunk(data.delta);
        }
        else if (type === "billing_update") {
            var chatbox = document.getElementById("chatbox");
            if (chatbox) {
                var lastMsg = chatbox.querySelector(".msg.bot:last-child");
                if (lastMsg) {
                    var bubble = lastMsg.querySelector(".billing-bubble");
                    if (!bubble) {
                        bubble = document.createElement("div");
                        bubble.className = "billing-bubble";
                        lastMsg.appendChild(bubble);
                    }
                    bubble.textContent = "\u2248 " + (data.cost || 0).toFixed(2) + " \u20bd";
                }
            }
        }
        else if (type === "error") {
            setStatus("Ошибка: " + ((data.error && data.error.message) || "неизвестно"));
            stopRecording();
        }
        else if (type === "connection_closed") {
            log("WebSocket closed by server");
            setStatus("Соединение закрыто");
        }
    }

    function playAudioChunk(b64) {
        var binary = atob(b64);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        var int16 = new Int16Array(bytes.buffer);
        var float32 = new Float32Array(int16.length);
        for (var i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;

        if (!audioOutCtx) audioOutCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 24000});
        var buf = audioOutCtx.createBuffer(1, float32.length, 24000);
        buf.getChannelData(0).set(float32);
        var src = audioOutCtx.createBufferSource();
        src.buffer = buf;
        src.connect(audioOutCtx.destination);
        var t = Math.max(audioOutCtx.currentTime, nextPlayTime);
        src.start(t);
        nextPlayTime = t + buf.duration;
    }

    function stopRecording() {
        isRecording = false;
        isConnecting = false;
        if (micBtn) micBtn.classList.remove("recording");
        clearWaveform();
        if (processor) { processor.disconnect(); processor = null; }
        if (mediaStream) { mediaStream.getTracks().forEach(function(t) { t.stop(); }); mediaStream = null; }
        if (audioContext) { audioContext.close(); audioContext = null; }
        if (eventSource) { eventSource.close(); eventSource = null; }
        if (sessionId) {
            fetch("/api/voice/close", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({session_id: sessionId})
            }).catch(function() {});
            sessionId = null;
        }
        userMsgSent = false;
        botMsgSent = false;
        currentReply = "";
        currentTranscript = "";
        setStatus("");
    }

    async function toggleVoice() {
        if (isRecording) { stopRecording(); return; }
        if (isConnecting) return;

        var modelKey = (typeof currentModel !== "undefined" ? currentModel : "speech-realtime-260528") || "speech-realtime-260528";
        isConnecting = true;
        setStatus("Подключение...");

        try {
            var res = await fetch("/api/voice/session", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    model: modelKey,
                    voice: "kirill",
                    conversation_id: (typeof currentConvId !== "undefined" ? currentConvId : null),
                    response_mode: (document.getElementById("response-mode-select") ? document.getElementById("response-mode-select").value : "audio")
                })
            });
            var data = await res.json();
            if (data.error) {
                setStatus("Ошибка: " + data.error);
                isConnecting = false;
                return;
            }
            sessionId = data.session_id;
            currentTranscript = "";
            currentReply = "";
            userMsgSent = false;
            botMsgSent = false;
            nextPlayTime = 0;
            audioLevelPeaks = [];
            await startRecording();
        } catch(err) {
            setStatus("Ошибка: " + err.message);
            isConnecting = false;
        }
    }

    document.addEventListener("DOMContentLoaded", function() {
        micBtn = document.getElementById("mic-btn");
        voiceStatus = document.getElementById("voice-status");
        waveformCanvas = document.getElementById("waveform");
        waveCtx = waveformCanvas ? waveformCanvas.getContext("2d") : null;

        if (micBtn) {
            micBtn.addEventListener("click", toggleVoice);
            log("Mic button initialized");
        } else {
            log("WARNING: #mic-btn not found in DOM");
        }

        clearWaveform();
    });
})();
