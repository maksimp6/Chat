"""HTTP errors: raised anywhere in a request, answered as the matching status."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

from webframe.http import status_phrase

if TYPE_CHECKING:
    from webframe.wrappers import Response


class HTTPError(Exception):
    """An HTTP error; ``get_response()`` is the answer a client receives for it."""

    code: int | None = None
    description: str | None = None

    def __init__(self, description: str | None = None, response: Any = None) -> None:
        super().__init__()
        if description is not None:
            self.description = description
        self.response = response

    @property
    def name(self) -> str:
        return status_phrase(self.code or 500)

    def get_description(self) -> str:
        return f"<p>{escape(self.description or '')}</p>"

    def get_body(self) -> str:
        return (
            "<!doctype html>\n<html lang=en>\n"
            f"<title>{self.code} {escape(self.name)}</title>\n"
            f"<h1>{escape(self.name)}</h1>\n"
            f"{self.get_description()}\n"
        )

    def get_headers(self) -> list[tuple[str, str]]:
        return [("Content-Type", "text/html; charset=utf-8")]

    def get_response(self) -> Response:
        if self.response is not None:
            return self.response
        from webframe.wrappers import Response

        return Response(
            self.get_body(), status=self.code or 500, headers=self.get_headers()
        )

    def __str__(self) -> str:
        return f"{self.code} {self.name}: {self.description or ''}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__} '{self.code}: {self.name}'>"


class BadRequest(HTTPError):
    code = 400
    description = (
        "The browser (or proxy) sent a request that this server could not understand."
    )


class ClientDisconnected(BadRequest):
    """The client went away before its request body was fully read."""


class BadRequestKeyError(BadRequest, KeyError):
    """A form or query key that is not there, answered as a bad request."""

    def __init__(self, arg: Any = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if arg is not None:
            self.args = (arg,)

    def __str__(self) -> str:
        return BadRequest.__str__(self)


class Unauthorized(HTTPError):
    code = 401
    description = (
        "The server could not verify that you are authorized to access the URL "
        "requested."
    )


class Forbidden(HTTPError):
    code = 403
    description = "You don't have the permission to access the requested resource."


class NotFound(HTTPError):
    code = 404
    description = (
        "The requested URL was not found on the server. If you entered the URL "
        "manually please check your spelling and try again."
    )


class MethodNotAllowed(HTTPError):
    code = 405
    description = "The method is not allowed for the requested URL."

    def __init__(
        self,
        valid_methods: Any = None,
        description: str | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(description=description, response=response)
        self.valid_methods = sorted(valid_methods or ())

    def get_headers(self) -> list[tuple[str, str]]:
        headers = super().get_headers()
        if self.valid_methods:
            headers.append(("Allow", ", ".join(self.valid_methods)))
        return headers


class NotAcceptable(HTTPError):
    code = 406
    description = (
        "The resource identified by the request is only capable of generating "
        "response entities which have content characteristics not acceptable "
        "according to the accept headers sent in the request."
    )


class RequestTimeout(HTTPError):
    code = 408
    description = (
        "The server closed the network connection because the browser didn't "
        "finish the request within the specified time."
    )


class Conflict(HTTPError):
    code = 409
    description = "A conflict happened while processing the request."


class Gone(HTTPError):
    code = 410
    description = "The requested URL is no longer available on this server."


class LengthRequired(HTTPError):
    code = 411
    description = "A request with this method requires a valid Content-Length header."


class PreconditionFailed(HTTPError):
    code = 412
    description = (
        "The precondition on the request for the URL failed positive evaluation."
    )


class RequestEntityTooLarge(HTTPError):
    code = 413
    description = "The data value transmitted exceeds the capacity limit."


class RequestURITooLarge(HTTPError):
    code = 414
    description = (
        "The length of the requested URL exceeds the capacity limit for this server."
    )


class UnsupportedMediaType(HTTPError):
    code = 415
    description = (
        "The server does not support the media type transmitted in the request."
    )


class RequestedRangeNotSatisfiable(HTTPError):
    code = 416
    description = "The server cannot provide the requested range."

    def __init__(
        self,
        length: int | None = None,
        units: str = "bytes",
        description: str | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(description=description, response=response)
        self.length = length
        self.units = units

    def get_headers(self) -> list[tuple[str, str]]:
        headers = super().get_headers()
        if self.length is not None:
            headers.append(("Content-Range", f"{self.units} */{self.length}"))
        return headers


class UnprocessableEntity(HTTPError):
    code = 422
    description = (
        "The request was well-formed but was unable to be followed due to "
        "semantic errors."
    )


class TooManyRequests(HTTPError):
    code = 429
    description = "This user has exceeded an allotted request count."


class InternalServerError(HTTPError):
    code = 500
    description = (
        "The server encountered an internal error and was unable to complete your "
        "request. Either the server is overloaded or there is an error in the "
        "application."
    )

    def __init__(
        self,
        description: str | None = None,
        response: Any = None,
        original_exception: BaseException | None = None,
    ) -> None:
        super().__init__(description=description, response=response)
        self.original_exception = original_exception


class NotImplementedByServer(HTTPError):
    code = 501
    description = "The server does not support the action requested by the browser."


class BadGateway(HTTPError):
    code = 502
    description = (
        "The proxy server received an invalid response from an upstream server."
    )


class ServiceUnavailable(HTTPError):
    code = 503
    description = (
        "The server is temporarily unable to service your request due to "
        "maintenance downtime or capacity problems."
    )


class GatewayTimeout(HTTPError):
    code = 504
    description = "The connection to an upstream server timed out."


default_exceptions: dict[int, type[HTTPError]] = {
    400: BadRequest,
    401: Unauthorized,
    403: Forbidden,
    404: NotFound,
    405: MethodNotAllowed,
    406: NotAcceptable,
    408: RequestTimeout,
    409: Conflict,
    410: Gone,
    411: LengthRequired,
    412: PreconditionFailed,
    413: RequestEntityTooLarge,
    414: RequestURITooLarge,
    415: UnsupportedMediaType,
    416: RequestedRangeNotSatisfiable,
    422: UnprocessableEntity,
    429: TooManyRequests,
    500: InternalServerError,
    501: NotImplementedByServer,
    502: BadGateway,
    503: ServiceUnavailable,
    504: GatewayTimeout,
}


def abort(status: int, description: str | None = None) -> None:
    """Raise the HTTP error for ``status``."""
    cls = default_exceptions.get(status)
    if cls is None:
        error = HTTPError(description)
        error.code = status
        raise error
    if cls is MethodNotAllowed:
        raise MethodNotAllowed(description=description)
    if cls is RequestedRangeNotSatisfiable:
        raise RequestedRangeNotSatisfiable(description=description)
    raise cls(description=description)


__all__ = [
    "BadGateway",
    "BadRequest",
    "BadRequestKeyError",
    "ClientDisconnected",
    "Conflict",
    "Forbidden",
    "GatewayTimeout",
    "Gone",
    "HTTPError",
    "InternalServerError",
    "LengthRequired",
    "MethodNotAllowed",
    "NotAcceptable",
    "NotFound",
    "NotImplementedByServer",
    "PreconditionFailed",
    "RequestEntityTooLarge",
    "RequestTimeout",
    "RequestURITooLarge",
    "RequestedRangeNotSatisfiable",
    "ServiceUnavailable",
    "TooManyRequests",
    "Unauthorized",
    "UnprocessableEntity",
    "UnsupportedMediaType",
    "abort",
    "default_exceptions",
]
