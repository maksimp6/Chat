# Optima model profiles

Optima is a balanced, explicitly selected Logos preset for the existing Yandex
Responses chat models. It is not a new Yandex model, provider or service tier.
Adding the preset does not change the default model or activate generation.

## Select and use

Open the chat settings and choose **Профиль → Optima — баланс**, then save.
The choice and its separate output budget are saved with the conversation, using
the existing local/server settings storage. The model picker shows which models
have an Optima preset. The settings selector does not offer Optima for a model
without an available preset.

Optima owns the reasoning and truncation settings. The previous manual values
remain stored and are restored when selecting **Текущие настройки**. If a saved
Optima conversation is switched to an unsupported model, its profile is not
silently replaced: the UI explains the incompatibility and the chat API rejects
the selection before calling the provider.

The separate **Лимит токенов Optima** defaults to `0`: do not send an application
output ceiling. The provider's own limits still apply. A positive integer becomes
`max_output_tokens`. The old manual output value, including the historic default
of 2000 tokens, is not inherited by Optima.

## Catalog checked on 2026-10-25

The nine presets in `config/model_profiles/optima-profiles.json` cover the existing
non-retired text entries routed through the Responses chat path:

| Registry model | Optima reasoning | Published context, tokens |
| --- | --- | ---: |
| `logos-runtime` | Provider default | 131072 |
| `logos-runtime-flash` | Provider default | 65536 |
| `yandexgpt-5-pro` | Provider default | 32768 |
| `yandexgpt-5-lite` | Provider default | 32768 |
| `deepseek-v4.1-flash` | Provider default | 1048576 |
| `deepseek-v4-flash` | Provider default | 1048576 |
| `gpt-oss-120b` | `medium` | 131072 |
| `gpt-oss-20b` | `medium` | 131072 |
| `qwen3.6-35b-a3b` | Provider default | 262144 |

The two Logos registry names map to the official upstream IDs `aliceai-llm` and
`aliceai-llm-flash`. Both official IDs and earlier stored `logosai-*` aliases resolve
to the same Logos entries and prices. Discovery accepts official model URIs with
or without `/latest`, so these entries receive the matching Optima summaries.

The [official reasoning documentation](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/chain-of-thought)
describes `medium` as a balance of speed and reasoning accuracy, documents depth
control for GPT OSS, and states that Qwen does not support depth selection.
Consequently Optima sends `reasoning: {"effort": "medium"}` for the two GPT OSS
entries only. For the remaining models it does not send `reasoning` or
`reasoning_effort`; this does not mean reasoning is disabled.

For every preset:

- `truncation` is `disabled`; input and instructions are not shortened.
- The selected model, sampling parameters and configured tools are preserved.
- There is no model substitution or automatic retry added by the preset.
- Context size is descriptive metadata, not a claimed output-token ceiling.
- No fixed speedup, lower price or quality score is claimed without measurements.

The [official model catalog](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
lists GPT OSS until 2026-10-30, YandexGPT Lite 5 and DeepSeek V4 Flash until
2026-11-15. These dates are retained as `stop_and_recheck_on` metadata. Presets do
not provide an automatic replacement when a model is retired.

YandexGPT Pro 5.1 remains in the application's separate completion route. Voice,
retired models, third-party providers and the broader prepared batch/dedicated
Ultra catalog are outside these nine chat presets.

## Runtime contract

The browser sends `params.model_profile = "optima"` to `/api/chat`. The HTTP
allowlist preserves that field. `selected_model_profile` validates it before
invocation/storage/provider work, and `apply_request_profile` applies the preset
in the actual Responses payload builder. The local profile selector is never sent
to Yandex as a provider parameter. Ordinary tool-loop continuations preserve the
same selection. Explicit unknown or unsupported selections return HTTP 400.

An omitted profile or `custom` retains existing request behavior. Optima's
reasoning/truncation values take precedence over manual values if a direct API
caller sends both. Other caller parameters, including a positive output ceiling,
are preserved.

`optima_profile.status = "available"` in `/api/models` means the local preset is
available for selection. `provider_acceptance = "NOT_RUN"` remains explicit; it
does not assert account entitlement or successful live generation.

## Verification and limits

Run the focused offline suites:

```sh
python -m pytest -q tests/test_optima_profiles.py tests/test_optima_chat_contract.py tests/test_optima_upstream.py tests/test_yandex_request_builder.py tests/test_yandex_metadata_validator.py
node --test tests/test_optima_ui.js tests/test_ui_settings_params.js tests/test_model_ultra_ui.js tests/test_ui_model_picker.js
```

The isolated HTTP tests compile the actual handler/helpers from the route source
and use Flask with recording substitutes for persistence, provider and tool
execution. The tool-loop test executes the actual continuation closure. A negative
control removes `model_profile` from the allowlist and demonstrates that the
Optima contract detects the resulting Ultra/high payload.

UI tests exercise the real settings/picker modules in BrowserShim, including
selection, server-settings reload, conversation isolation, manual-settings
restoration, unsupported models and save errors. This is not a full application,
Android browser or provider E2E run. No paid generation is required by these tests.

The legacy `/api/mcp/execute-approved` follow-up creates its own synthesis settings;
it is outside the ordinary `/api/chat` continuation contract above.
