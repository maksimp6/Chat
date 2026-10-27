# Voice assistant

The selected voice model is sent to Yandex Realtime over a server-side WebSocket.
Voice model IDs are never substituted with a text model or the SpeechKit chain.
The microphone also supports an explicitly selected text model through the
separate `speechkit_pipeline` transport: SpeechKit STT → that exact text model
and its existing Logos tool loop → SpeechKit TTS.

## Models

| UI model key | Provider URI suffix | Input | Voice family |
|---|---|---|---|
| `speech-realtime-deepseek-v4-flash` | `speech-realtime-deepseek-v4-flash` | Audio or text | `general` (default `filipp`) |
| `speech-realtime-max-tts-live` | `speech-realtime-max-tts-live/latest` | Audio or text | LiveTTS (default `sofia`) |
| `speech-realtime-tts-live` | `speech-realtime-tts-live/latest` | Text for synthesis | LiveTTS (default `sofia`) |

The URI prefix is `gpt://<active-credential-project-id>/`. The fixed provider endpoint
is `wss://ai.api.cloud.yandex.net/v1/realtime/openai`. `session.update` uses the
current `session.output_modalities` and `session.audio.input/output` schema.
Voice and optional role are validated against the model's voice family; provider
validation remains authoritative. LiveTTS speech speed is not sent.

## Browser and HTTP facade

The microphone records mono PCM16 WAV, explicitly resampled to 16 kHz. The backend
extracts RIFF data chunks and sends Base64 PCM in `input_audio_buffer.append`
events. Manual commit disables VAD, keeping one recording as one provider turn.

- `POST /api/voice/session` accepts `conversation_id`, `model`, optional `voice`,
  optional `role`, `instructions`, `transport` (`realtime`, or the explicit
  `speechkit_pipeline` for text models), and `response_mode` (`audio`, `both`, or `text`). It reports the
  accepted audio format, sample rate, upload-memory limit and session lifetime.
- `POST /api/voice/audio?session_id=...` uploads PCM16 WAV. A closed session rejects
  additional input. Speech TTS Live rejects audio and asks for text.
- `POST /api/voice/text` accepts `session_id` and `text`, then starts the selected
  Realtime model. Speech TTS Live synthesizes this exact text without an LLM turn.
- `POST /api/voice/close` commits audio and starts processing once. With
  `cancel: true`, it cancels the session instead.
- `GET /api/voice/events?session_id=...` streams SSE with event IDs and supports
  `Last-Event-ID` reconnection. The worker waits for independently delivered input
  transcription even when it arrives after `response.done`. Missing transcription
  is preserved as an audio-input marker and marks the overall result incomplete.
  Provider errors and non-completed status are
  returned explicitly rather than reporting a successful turn.
- `GET /api/voice/output?session_id=...` returns PCM16 WAV at 44.1 kHz, playable by
  Android browsers (48 kHz for SpeechKit synthesis). The browser keeps audio controls when autoplay is blocked.

`window.LogosVoice.sendText({conversationId, model, text, responseMode?})` and
`synthesizeText` return a promise with `reply`, `usage`, `model`, `conversation_id`
and `response_id`. They play received audio and leave message rendering to the
chat UI. `window.LogosVoice.cancel()` cancels local voice sessions and playback.
The microphone UI renders its own events only in the conversation that owns the
session. Text-only TTS is started from the text
send action, without opening the microphone.

This is a single-turn recording facade over the actual Realtime API: microphone
input is uploaded after recording, and playback starts when the returned PCM is
complete. It does not claim continuous bidirectional microphone streaming.

## Credentials, ownership and persistence

The active Yandex credential is resolved through the shared encrypted credential
store, including normal environment migration/rotation behavior. It is refreshed
for the provider connection and never sent to the browser. Realtime requires the
service account role `ai.models.user` and API-key scope
`yc.ai.foundationModels.execute`; a model's presence in the catalog does not prove
these rights.

Realtime turns use the existing configured provider quota admission and account
for returned token usage. Unknown network outcomes do not release a reservation
as though non-execution were proven. The SpeechKit pipeline retains the existing
text-model quota/tool path without another Realtime reservation. Realtime's
single-turn adapter does not currently expose tools; provider support and runtime
tool support are separate. Text-model microphone requests retain the normal
`ask_with_mcp` executor and conversation settings rather than substituting a model.

Each endpoint verifies the session owner and the existing conversation access
policy. The worker checks conversation access before loading history and before
saving results. User transcripts and assistant text/usage are saved to chat
history once; partial provider responses are marked `voice_partial`. Speech TTS
Live receives no prior conversation history. Provider cost remains unknown when
no verified accounting result is available.

Input and output audio remain in session memory and expire after ten minutes.
The upload memory boundary is 1 MiB, and the browser stops capture before that
boundary. It is a facade limit, not a claimed Realtime model limit. The explicit
SpeechKit v1 pipeline also observes its documented 30-second recognition limit.
Its long synthesized replies are sent as multiple requests and concatenated as
PCM instead of silently truncating text. Cancellation/expiry closes the Realtime
provider connection; cancellation of the SpeechKit pipeline stops further stages
and playback after any already-running synchronous HTTP request returns. Events
and output never expose credentials.

## Official references

- [Current model catalog](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
- [Realtime protocol](https://aistudio.yandex.ru/en/docs/ai-studio/realtime/)
- [Voice agent](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/agents/create-voice-agent)
- [Speech TTS Live](https://aistudio.yandex.ru/ru/docs/speechkit/tts/api/tts-realtime)
- [Compatible voices and roles](https://aistudio.yandex.ru/ru/docs/speechkit/tts/voices)
