# API errors (RFC 9457)

Alice API errors use [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457)
with media type `application/problem+json`. Build them with
`invocation.problems.problem()`:

```python
from invocation.problems import problem

return problem(404, "conversation_not_found", "Диалог не найден")
```

## Body

| Member | Meaning |
|---|---|
| `type` | `https://alice.pro/problems/<code>`; identifies the problem class |
| `title` | Short Russian summary for people |
| `status` | HTTP status, repeated in the body |
| `detail` | Optional fixed explanation of what to do next |
| `error` | The same `<code>`; kept so existing clients that read `data.error` keep working |

Extension members (for example `code`, `conversation_id`) are allowed and may
not override the members above.

## Rules

- Codes are `snake_case`, at most 64 characters, and stable once shipped.
- `title` and `detail` are written by us. Never put `str(exc)`, a traceback, a
  path, SQL or a credential in a response. Log the exception and return fixed
  text.
- 4xx means the caller can fix the request; 5xx means we must.

## Ratchet

`tests/test_api_problem_details.py` counts, over production code:

- legacy `jsonify({"error": ...})` bodies;
- responses built inside `except ... as exc` that pass the exception to the
  client.

Both counts may only go down. Convert one module per pull request and lower
the baselines in the same change.
