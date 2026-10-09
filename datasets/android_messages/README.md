# Synthetic Android SMS and notification examples

This dataset contains **20 entirely synthetic Russian-language examples** for testing classification and redaction in Alice Pro.

- Source: manually authored generic templates. **No SMS bodies, contacts, phone numbers, real timestamps, device identifiers, screenshots or notification content were extracted from the user's Android.**
- `synthetic: true` on every row. This is **not** an anonymized sample of the user's actual messages and does not reproduce their statistical distribution.
- No real one-time codes, payment details, tokens, credentials or real-world sender identifiers.
- Placeholders such as `[OTP_REDACTED]` and `[URL_REDACTED]` are literal markers, not authentic values.
- Do not use as evidence of financial transactions, legal claims or account activity.
- Not sufficient alone to train a production-grade classifier; use as smoke-test fixtures.
- Never add raw personal device exports to this folder.

## Format
UTF-8 JSON Lines. Required fields: `id`, `source`, `category`, `text`, `synthetic`, `language`.

## Publication gate
Review every added line for names, real addresses, phone numbers, account numbers, URLs, OTPs and unique conversational excerpts. Synthetic fixtures only. Require CI validation and user acceptance before merging.

Related: #1066.
