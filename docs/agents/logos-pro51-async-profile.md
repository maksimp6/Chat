# Logos: owner-selected Pro 5.1 async profile

The owner selected **YandexGPT Pro 5.1 in asynchronous Text Generation API** as
the replacement profile on 23 October 2026 in case #1413. The follow-up correction
requires maximal capability settings, with no arbitrary 8192-token restriction.

[The JSON profile](../../config/model_profiles/logos-pro51-async.json) contains
the request template and the operation-retrieval contract. It is prepared for
review, **not activated**. The existing native Logos Responses client does not
load this file or implement this transport. Production defaults, generation
permission and current workflows are unchanged. Preparation does not authorize
a provider POST, merge or cutover.

## Selected settings and exact limits of evidence

| Setting | Selection | Evidence boundary |
| --- | --- | --- |
| Transport | `POST /foundationModels/v1/completionAsync` | Async Text Generation, not batch or Responses `background` |
| Model | `gpt://<project-id>/yandexgpt-5.1` | Explicit Pro 5.1 identity; no alias/fallback to another model |
| `maxTokens` | `"32768"` | Owner-requested upper budget using the documented 32768-token context window; the separate output ceiling and acceptance of this value are unverified |
| Context | Complete provided task and sources | 32768 is the model window, not 32768 input plus another 32768 output; do not silently truncate or split evidence |
| Reasoning | No reasoning flag | The Pro 5.1 release states reasoning is unsupported; the generic API enum does not prove support by this model |
| Temperature | `0.3` | Explicit sampling choice retained from the existing profile; increasing randomness is not an increase in model capability |
| Streaming | `false` | Receive an Operation and retrieve its result later |
| Tools | Omitted | Async API states `tools` is unsupported and ignored; no hosted search, MCP or Code Interpreter is claimed |
| `service_tier` | Omitted | Not a field of this Text Generation request |
| Retries/fallback | No automatic POST retry; no model/output-limit downgrade | Resume the saved operation rather than create a duplicate paid generation |

The requested output limit must fail visibly if rejected. A future executor must
validate the complete input against the model limit before submission. This
configuration artifact does not implement that validation or enforce the local
execution contract. Live request acceptance and output quality are **NOT_RUN**.

## Render the request without a provider call

The `${...}` strings are template markers, not syntax evaluated by Yandex. The
profile envelope must never be sent as the HTTP body: send only `request.body`
after rendering. Resolve headers privately from the existing authorized
credential at execution time; do not save a resolved Authorization header.

The following local-only command renders the body from a complete UTF-8 input
file. It never reads an API key or makes a network request:

```bash
python3 - /path/to/complete-case.txt /path/to/request.json <<'PY'
import json
import os
import re
import sys
from pathlib import Path

profile = json.loads(Path("config/model_profiles/logos-pro51-async.json").read_text())
project = os.environ["YANDEX_PROJECT_ID"]
if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project):
    raise SystemExit("Invalid project ID")
case_text = Path(sys.argv[1]).read_text(encoding="utf-8")
if not case_text.strip():
    raise SystemExit("The complete case input must not be empty")
body = profile["request"]["body"]
body["modelUri"] = f"gpt://{project}/yandexgpt-5.1"
body["messages"][1]["text"] = case_text
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as output:
    json.dump(body, output, ensure_ascii=False, indent=2)
    output.write("\n")
PY
```

This renders JSON only; it does not prove the case fits the context window or
authorize sending it. JSON serialization preserves quotes and Unicode. Do not
use shell interpolation or `envsubst` on source evidence or credentials.

## Future execution and result retention

After a separately approved submission, save the returned Operation `id` before
polling `GET https://operation.api.cloud.yandex.net/operations/{operation_id}`.
The profile uses a 5-second polling interval. Set the observing job's deadline
explicitly for the approved run; no short adapter timeout should silently cancel
or replace a still-running operation.

An `error` is not success, including when `done=false`. A running operation has
`done=false`; a terminal result has `done=true` and an error or response. Require
the expected terminal shape and each accepted alternative's
`ALTERNATIVE_STATUS_FINAL`, preserving the exact response text rather than
treating `done=true` as proof of a completed Court review. Export all alternatives
with their statuses, `response.modelVersion`, `response.usage`, the operation ID,
and a separately calculated text hash. A partial/refused/empty answer must remain
distinguishable. Polling or persistence failure must not trigger another POST.

Yandex documents server retention of async text results as **3 days**. A future
executor must save results durably within that period through the project's
existing approved persistence path. This profile does not add a second database.

## Price observation, not a billing promise

On 23 October 2026 the published Pro 5.1 rates are **0.41 RUB per 1000 input tokens
and 0.41 RUB per 1000 output tokens**, including VAT, versus 0.80/0.80 in sync.
For identical billed input/output counts this is a 48.75% rate reduction.
Actual usage, quality, capability parity and savings are not measured. The async
profile loses agent capabilities; it is not a same-capability replacement for the
current DeepSeek Responses loop. Refresh prices before an actual budget decision.

## Primary sources checked 23 October 2026

- [Async request and Operation retrieval](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/async-request)
- [Async request schema and ignored tools](https://aistudio.yandex.ru/ru/docs/ai-studio/text-generation/api-ref/TextGenerationAsync/completion)
- [Explicit model URI and 32768-token context](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
- [Pro 5.1 release, 28 August 2025: no reasoning support](https://aistudio.yandex.ru/ru/docs/ai-studio/release-notes/)
- [Token rates](https://aistudio.yandex.ru/ru/docs/ai-studio/pricing)
- [Async result retention](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/limits)
