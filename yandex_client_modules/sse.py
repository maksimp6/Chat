"""Server-sent events of a streaming requests response, parsed byte by byte.

``iter_lines`` is never used: it waits for whole chunks before it yields a line.
Interruption errors (requests.RequestException, urllib3.exceptions.HTTPError,
http.client.HTTPException, OSError) reach the caller unchanged.
"""

import json
import re

# One read never waits for more than what the socket already holds.
_READ_SIZE = 65536
# A line ends at CRLF, at a lone CR or at a lone LF, as the SSE specification says.
_LINE_END = re.compile(rb"\r\n|\r|\n")


def _chunks(resp):
    raw = getattr(resp, "raw", None)
    if getattr(raw, "chunked", False):
        return resp.iter_content(chunk_size=None)
    read1 = getattr(raw, "read1", None)
    if read1 is None:
        # urllib3 without read1: requests' own chunk iterator is the only reader left.
        return resp.iter_content(chunk_size=None)
    # A plain read() of a body without chunked framing blocks until EOF. requests reads the
    # raw body with decode_content=False, so this read decodes it as iter_content does. The
    # streamed requests ask for identity, so a provider that complies sends no encoding.
    # urllib3 documents None as a possible result of read1; it is the end of the body too.
    return iter(lambda: read1(_READ_SIZE, decode_content=True) or b"", b"")


def _event(data):
    if data == "[DONE]":
        return {"type": "[DONE]"}
    try:
        event = json.loads(data)
    except ValueError:
        return {"type": "__invalid__"}
    if not isinstance(event, dict):
        return {"type": "__invalid__"}
    return event


# What a comment line yields when the caller asks for comments: not an event, only a
# sign that the reader is back in control, so it can check its own deadline.
COMMENT_MARKER = "__comment__"


def iter_sse_events(resp, *, comments=False):
    """Yield each dispatched event as a dict.

    Lines end at LF, CR or CRLF. Only ``data:`` lines are used (``event:``, ``id:`` and
    ``retry:`` are ignored); ``[DONE]`` yields {"type": "[DONE]"} and data that is not a
    JSON object yields {"type": "__invalid__"}. An event left unfinished at EOF is
    discarded. With ``comments`` a comment line (a keep-alive such as ``: ping``) and an
    empty line that ends no event yield {"type": COMMENT_MARKER}: a stream of keep-alives
    alone would otherwise never hand control back to a reader that bounds its wait.
    """
    buffer = bytearray()
    data_lines = []
    # The last line ended with a CR that was the last byte read. A LF that opens the
    # next read completes that same line end, so it is not an empty line.
    after_cr = False
    for chunk in _chunks(resp):
        if not chunk:
            continue
        if after_cr:
            after_cr = False
            if chunk[:1] == b"\n":
                chunk = chunk[1:]
        buffer.extend(chunk)
        while True:
            match = _LINE_END.search(buffer)
            if match is None:
                break
            raw_line = bytes(buffer[: match.start()])
            if match.group() == b"\r" and match.end() == len(buffer):
                after_cr = True
            del buffer[: match.end()]
            line = raw_line.decode("utf-8", errors="replace")
            if not line:
                if data_lines:
                    yield _event("\n".join(data_lines))
                    data_lines = []
                elif comments:
                    yield {"type": COMMENT_MARKER}
                continue
            if line.startswith(":"):
                if comments:
                    yield {"type": COMMENT_MARKER}
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
