# Prompt-based Yandex classifiers

`POST /api/classify` calls Yandex's synchronous Text Classification API. It does
not route classification through a chat model. The two registered models are
`classifier-yandexgpt-lite` and `classifier-yandexgpt`; their provider URIs are
`cls://<active-folder>/yandexgpt-lite/latest` and
`cls://<active-folder>/yandexgpt/latest`.

The request accepts only these fields:

| Field | Contract |
| --- | --- |
| `model` | Optional registry key; defaults to `classifier-yandexgpt-lite`. |
| `text` | Required nonempty string to classify. |
| `taskDescription` | Required nonempty description of the classification task. |
| `labels` | Required list of 1–20 unique nonempty label strings. The upper bound is Yandex's documented limit. |
| `samples` | Optional list of objects containing only `text` and `label`. Each text must be nonempty and its label must belong to `labels`. |

Omit `samples` for zero-shot classification; add examples for few-shot
classification. No guessed character limit or truncation is applied. Yandex's
model token limits still apply; the classification guide limits all examples in
one request to 6000 tokens.

Example:

```json
{
  "model": "classifier-yandexgpt",
  "text": "Когда доставят заказ?",
  "taskDescription": "Определи тему обращения клиента",
  "labels": ["доставка", "оплата", "возврат"],
  "samples": [{"text": "Где мой заказ?", "label": "доставка"}]
}
```

The response contains the exact provider `model` URI, `predictions` with finite
numeric confidence values in `[0, 1]`, and the provider's `modelVersion` and
`inputTokens` when supplied. An incomplete or malformed prediction set is an
upstream error, never a successful classification.

The route resolves the same active deployment credential as chat on every
request and uses the existing provider quota policy. It sends one POST to
`https://ai.api.cloud.yandex.net/foundationModels/v1/fewShotTextClassification`.
It never automatically retries a failed POST. A transport failure has an unknown
provider outcome and keeps its quota reservation; no provider cost is invented.
Upstream error bodies, credentials, input text, labels and samples are not
included in diagnostic events.

Official sources checked 2026-10-27:

- [Classifier models](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/classifier/models)
- [FewShotClassify REST API](https://aistudio.yandex.ru/ru/docs/ai-studio/text-classification/api-ref/TextClassification/fewShotClassify)
- [Zero-shot and few-shot examples](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/classifier/readymade)
- [Platform limits](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/limits)

Tests and live provider calls: `NOT_RUN`.
