(function () {
    var micBtn = null;
    var voiceStatus = null;
    var waveformCanvas = null;
    var waveCtx = null;

    var sessionId = null;
    var audioContext = null;
    var mediaStream = null;
    var source = null;
    var processor = null;
    var mediaRecorder = null;
    var mediaChunks = [];
    var pcmChunks = [];
    var isRecording = false;
    var isConnecting = false;
    var eventSource = null;
    var currentTranscript = "";
    var currentReply = "";
    var userMsgSent = false;
    var botMsgSent = false;
    var audioPlayer = null;

    function log(msg) { console.log("[VOICE]", msg); }

    function setStatus(text) {
        log("Status: " + text);
        if (voiceStatus) voiceStatus.textContent = text;
    }

    function clearWaveform() {
        if (!waveCtx || !waveformCanvas) return;
        var w = waveformCanvas.width = waveformCanvas.offsetWidth || 320;
        var h = waveformCanvas.height = waveformCanvas.offsetHeight || 48;
        waveCtx.clearRect(0, 0, w, h);
    }

    function drawWaveform(level) {
        if (!waveCtx || !waveformCanvas) return;
        var w = waveformCanvas.width;
        var h = waveformCanvas.height;
        waveCtx.clearRect(0, 0, w, h);
        waveCtx.beginPath();
        for (var x = 0; x < w; x += 2) {
            var y = h / 2 + Math.sin(x / 9) * level * h / 2;
            if (x === 0) waveCtx.moveTo(x, y); else waveCtx.lineTo(x, y);
        }
        waveCtx.strokeStyle = "#4a90d9";
        waveCtx.lineWidth = 2;
        waveCtx.stroke();
    }

    function wavFromPcm(chunks, sampleRate) {
        var length = chunks.reduce(function (n, c) { return n + c.length; }, 0);
        var buffer = new ArrayBuffer(44 + length * 2);
        var view = new DataView(buffer);
        function str(offset, value) {
            for (var i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
        }
        str(0, "RIFF"); view.setUint32(4, 36 + length * 2, true);
        str(8, "WAVE"); str(12, "fmt "); view.setUint32(16, 16, true);
        view.setUint16(20, 1, true); view.setUint16(22, 1, true);
        view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
        view.setUint16(32, 2, true); view.setUint16(34, 16, true);
        str(36, "data"); view.setUint32(40, length * 2, true);
        var offset = 44;
        chunks.forEach(function (chunk) {
            for (var i = 0; i < chunk.length; i++, offset += 2) {
                var sample = Math.max(-1, Math.min(1, chunk[i]));
                view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
            }
        });
        return new Blob([buffer], {type: "audio/wav"});
    }

    async function uploadAudio(blob) {
        if (!sessionId) return;
        var body = await blob.arrayBuffer();
        var res = await fetch("/api/voice/audio?session_id=" + encodeURIComponent(sessionId), {
            method: "POST",
            headers: {"Content-Type": blob.type || "application/octet-stream"},
            body: body
        });
        if (!res.ok) throw new Error("Не удалось передать аудио: HTTP " + res.status);
    }

    function playAudio(url) {
        if (audioPlayer) {
            audioPlayer.pause();
            audioPlayer.src = "";
        }
        audioPlayer = new Audio(url);
        audioPlayer.play().catch(function (err) {
            log("Audio playback requires user gesture: " + err.message);
        });
    }

    async function finishRecording() {
        var blob = null;
        if (mediaRecorder) {
            blob = await new Promise(function (resolve) {
                mediaRecorder.addEventListener("stop", function () {
                    resolve(new Blob(mediaChunks, {type: mediaRecorder.mimeType || "audio/ogg"}));
                }, {once: true});
                mediaRecorder.stop();
            });
        } else if (pcmChunks.length && audioContext) {
            blob = wavFromPcm(pcmChunks, audioContext.sampleRate);
        }
        if (!blob || !blob.size) throw new Error("Запись голоса пуста");
        await uploadAudio(blob);
        var response = await fetch("/api/voice/close", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({session_id: sessionId})
        });
        if (!response.ok) throw new Error("Не удалось завершить голосовую сессию: HTTP " + response.status);
    }

    async function startRecording() {
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}
            });
        } catch (err) {
            setStatus("Ошибка микрофона: " + err.message);
            isConnecting = false;
            return;
        }

        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        source = audioContext.createMediaStreamSource(mediaStream);
        pcmChunks = [];
        mediaChunks = [];

        var mime = "";
        if (window.MediaRecorder) {
            if (MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")) mime = "audio/ogg;codecs=opus";
            else if (MediaRecorder.isTypeSupported("audio/ogg")) mime = "audio/ogg";
        }
        if (mime) {
            mediaRecorder = new MediaRecorder(mediaStream, {mimeType: mime});
            mediaRecorder.ondataavailable = function (e) {
                if (e.data && e.data.size) mediaChunks.push(e.data);
            };
            mediaRecorder.start();
        } else {
            processor = audioContext.createScriptProcessor(4096, 1, 1);
            source.connect(processor);
            processor.connect(audioContext.destination);
            processor.onaudioprocess = function (e) {
                if (!isRecording) return;
                var input = e.inputBuffer.getChannelData(0);
                var copy = new Float32Array(input.length);
                copy.set(input);
                pcmChunks.push(copy);
                var sum = 0;
                for (var i = 0; i < input.length; i++) sum += input[i] * input[i];
                drawWaveform(Math.min(1, Math.sqrt(sum / input.length) * 5));
            };
        }

        isRecording = true;
        isConnecting = false;
        if (micBtn) micBtn.classList.add("recording");
        setStatus("Говорите...");

        eventSource = new EventSource("/api/voice/events?session_id=" + encodeURIComponent(sessionId));
        eventSource.onmessage = function (e) {
            try { handleServerEvent(JSON.parse(e.data)); }
            catch (err) { log("SSE parse: " + err.message); }
        };
        eventSource.onerror = function () { log("SSE error"); };
    }

    function handleServerEvent(data) {
        var type = data.type;
        if (type === "input_audio_buffer.speech_stopped") {
            setStatus("Распознаю...");
        } else if (type === "conversation.item.input_audio_transcription.completed") {
            currentTranscript = data.transcript || "";
            if (currentTranscript && !userMsgSent && typeof addMessage === "function") {
                addMessage(currentTranscript, "user", true, 0);
                userMsgSent = true;
            }
            setStatus("Вы: " + currentTranscript);
        } else if (type === "response.output_text.done") {
            currentReply = data.text || "";
            if (currentReply && !botMsgSent && typeof addMessage === "function") {
                addMessage(currentReply, "bot", true, 0);
                botMsgSent = true;
            }
            setStatus("Ответ готов");
        } else if (type === "response.output_audio.ready") {
            playAudio(data.audio_url);
        } else if (type === "error") {
            setStatus("Ошибка: " + ((data.error && data.error.message) || "неизвестно"));
        } else if (type === "response.done") {
            setStatus("");
            if (eventSource) { eventSource.close(); eventSource = null; }
            sessionId = null;
            currentTranscript = "";
            currentReply = "";
            userMsgSent = false;
            botMsgSent = false;
        }
    }

    async function toggleVoice() {
        if (isRecording) {
            isRecording = false;
            if (micBtn) micBtn.classList.remove("recording");
            setStatus("Обрабатываю...");
            try {
                if (processor) { processor.disconnect(); processor = null; }
                if (source) { source.disconnect(); source = null; }
                if (mediaStream) {
                    mediaStream.getTracks().forEach(function (t) { t.stop(); });
                    mediaStream = null;
                }
                await finishRecording();
            } catch (err) {
                setStatus("Ошибка: " + err.message);
                if (eventSource) { eventSource.close(); eventSource = null; }
                sessionId = null;
            } finally {
                if (audioContext) { audioContext.close(); audioContext = null; }
                mediaRecorder = null;
                pcmChunks = [];
                mediaChunks = [];
            }
            return;
        }
        if (isConnecting) return;
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus("Микрофон не поддерживается браузером");
            return;
        }

        var modelKey = (typeof currentModel !== "undefined" ? currentModel : "speech-realtime-260528") || "speech-realtime-260528";
        isConnecting = true;
        setStatus("Подключение...");
        try {
            var res = await fetch("/api/voice/session", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    model: modelKey,
                    voice: "filipp",
                    conversation_id: (typeof currentConvId !== "undefined" ? currentConvId : null),
                    response_mode: (document.getElementById("response-mode-select") || {}).value || "audio"
                })
            });
            var data = await res.json();
            if (!res.ok || data.error) throw new Error(data.error || ("HTTP " + res.status));
            sessionId = data.session_id;
            currentTranscript = "";
            currentReply = "";
            userMsgSent = false;
            botMsgSent = false;
            await startRecording();
        } catch (err) {
            setStatus("Ошибка: " + err.message);
            isConnecting = false;
            sessionId = null;
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        micBtn = document.getElementById("mic-btn");
        voiceStatus = document.getElementById("voice-status");
        waveformCanvas = document.getElementById("waveform");
        waveCtx = waveformCanvas ? waveformCanvas.getContext("2d") : null;
        if (micBtn) micBtn.addEventListener("click", toggleVoice);
        else log("WARNING: #mic-btn not found in DOM");
        clearWaveform();
    });
})();