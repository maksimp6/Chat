# webframe

The web framework of Logos. It replaced Flask and Werkzeug in November 2026 and
depends on the standard library only. It is a library, not a unit: every unit with
HTTP routes imports it the way it imported Flask, and `pyproject.toml` lists it among
the third-party packages of the independence check.

## What it provides

| Module | Purpose |
| --- | --- |
| [`webframe.app`](app.py) | `Application` (the WSGI callable: rules, blueprints, hooks, error handlers, `run`, `test_client`) and `Blueprint`. |
| [`webframe.wrappers`](wrappers.py) | `Request` (headers, `args`, `get_json`, `form`, `files`, `stream`) and `Response` (status, headers, bytes or a stream, `make_conditional` for 304 and byte ranges). |
| [`webframe.globals`](globals.py) | `request`, `current_app`, `has_request_context`, `stream_with_context`, `copy_current_request_context`. |
| [`webframe.helpers`](helpers.py) | `jsonify`, `make_response`, `redirect`, `send_file`, `send_from_directory`, `render_template`. |
| [`webframe.routing`](routing.py) | URL rules with `<name>`, `<int:name>`, `<path:name>`; methods, automatic HEAD and OPTIONS, 405 with `Allow`. |
| [`webframe.templating`](templating.py) | The template language of `templates/index.html`: `{{ }}` with filters (`tojson`, `safe`, `default`, …), `{% set %}`, `{% if %}`, `{% for %}`, HTML autoescape. |
| [`webframe.formparser`](formparser.py) | URL-encoded and streaming multipart forms; file parts spool to disk past 512 KiB. |
| [`webframe.json`](json.py) | `JSONProvider`: how `jsonify` serializes; an application replaces `app.json` to change it. |
| [`webframe.serving`](serving.py) | The HTTP/1.1 server: a thread per connection, keep-alive, chunked streaming flushed per piece, `Range`-friendly bodies. |
| [`webframe.testing`](testing.py) | `Client`: in-process requests through the application (the environment gateway). |
| [`webframe.exceptions`](exceptions.py) | `HTTPError` and the status-code errors (`NotFound`, `MethodNotAllowed`, …), each with its own response. |
| [`webframe.datastructures`](datastructures.py) | `Headers`, `MultiDict`, `FileStorage`, `Accept`. |
| [`webframe.http`](http.py) | Dates, entity tags, ranges, media types, `Content-Disposition`. |

## How the application uses it

```python
from webframe import Application, Blueprint, jsonify, request

app = Application(__name__)          # serves static/ under /static/, templates from templates/
bp = Blueprint("files", __name__, url_prefix="/api/files")

@bp.get("/<file_id>")
def read(file_id):
    return jsonify({"id": file_id, "after": request.args.get("after")})

app.register_blueprint(bp)
app.run(host="0.0.0.0", port=8080, threaded=True)
```

A view returns a `Response`, text, bytes, a dict or list (JSON), a generator (a
stream), or a tuple `(body, status)`, `(body, headers)`, `(body, status, headers)`.
`stream_with_context(generator)` keeps `request` readable while the stream is sent;
`copy_current_request_context(func)` does the same for a worker thread.

## Behaviour kept from Flask

- Endpoints are `<blueprint>.<view>`; a second view with the same endpoint is an error.
- `GET` rules answer `HEAD`; every rule answers `OPTIONS` with `Allow` unless it
  defines `OPTIONS` itself; a path that matches a rule only with its trailing slash
  is redirected (308).
- `request.get_json(silent=True)` is `None` for a missing or unreadable body; without
  `silent` a non-JSON body is a 415 and unreadable JSON a 400.
- `jsonify` sorts keys; text responses carry `charset=utf-8`; `application/json` does not.
- After-request hooks run in reverse order of registration; an error handler for
  `Exception` also receives HTTP errors (`exc.get_response()` answers them).
- `send_file` of a path sets `Content-Length`, `Last-Modified`, an ETag and
  `Cache-Control: no-cache`, and answers `If-None-Match` (304) and `Range` (206).

## Server

`app.run(host, port, threaded=True)` starts `webframe.serving.serve`. Each connection
has its own thread; a response without `Content-Length` is chunked and flushed after
every piece, so server-sent events arrive at once. An idle keep-alive connection is
closed after 120 s. A client that disconnects closes the response iterator, which
stops the generator producing it. `LOGOS_DEBUG=1` (read by `app.py`) answers an
unhandled error with its traceback as text and logs at DEBUG.
