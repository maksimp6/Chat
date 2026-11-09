"""The request and application of the current thread: ``request``, ``current_app``.

A request context is a context variable: the thread handling a request sees its
own request, a worker started with ``copy_current_request_context`` sees the one
it was created in, and a generator wrapped by ``stream_with_context`` keeps it
while the response streams.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from webframe.app import Application
    from webframe.wrappers import Request

_request_ctx: ContextVar[RequestContext | None] = ContextVar(
    "webframe.request_context", default=None
)
_app_ctx: ContextVar[Application | None] = ContextVar(
    "webframe.app_context", default=None
)


class RequestContext:
    """The application and request a thread works in while it serves one request."""

    __slots__ = ("_tokens", "app", "request")

    def __init__(self, app: Application, request: Request) -> None:
        self.app = app
        self.request = request
        self._tokens: list[Any] = []

    def push(self) -> None:
        self._tokens.append(_request_ctx.set(self))

    def pop(self) -> None:
        token = self._tokens.pop()
        try:
            _request_ctx.reset(token)
        except ValueError:
            # Pushed and popped in different contexts (a stream read by another
            # thread): the thread simply leaves the request.
            _request_ctx.set(None)

    def __enter__(self) -> Self:
        self.push()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.pop()


class AppContext:
    """``with app.app_context():`` makes ``current_app`` available without a request."""

    __slots__ = ("_tokens", "app")

    def __init__(self, app: Application) -> None:
        self.app = app
        self._tokens: list[Any] = []

    def push(self) -> None:
        self._tokens.append(_app_ctx.set(self.app))

    def pop(self) -> None:
        token = self._tokens.pop()
        try:
            _app_ctx.reset(token)
        except ValueError:
            _app_ctx.set(None)

    def __enter__(self) -> Self:
        self.push()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.pop()


def has_request_context() -> bool:
    return _request_ctx.get() is not None


def has_app_context() -> bool:
    return _request_ctx.get() is not None or _app_ctx.get() is not None


def _current_context() -> RequestContext:
    ctx = _request_ctx.get()
    if ctx is None:
        raise RuntimeError(
            "Working outside of request context. This typically means that you "
            "attempted to use functionality that needed an active HTTP request."
        )
    return ctx


def _current_request() -> Request:
    return _current_context().request


def _current_app() -> Application:
    ctx = _request_ctx.get()
    if ctx is not None:
        return ctx.app
    app = _app_ctx.get()
    if app is None:
        raise RuntimeError(
            "Working outside of application context. This typically means that you "
            "attempted to use functionality that needed the current application."
        )
    return app


class _Proxy:
    """Forwards every attribute to the object a resolver returns at access time."""

    __slots__ = ("__weakref__", "_resolve")

    def __init__(self, resolve: Callable[[], Any]) -> None:
        object.__setattr__(self, "_resolve", resolve)

    def _get_current_object(self) -> Any:
        return object.__getattribute__(self, "_resolve")()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_current_object(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._get_current_object(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._get_current_object(), name)

    def __getitem__(self, key: Any) -> Any:
        return self._get_current_object()[key]

    def __contains__(self, key: Any) -> bool:
        return key in self._get_current_object()

    def __iter__(self) -> Iterator[Any]:
        return iter(self._get_current_object())

    def __len__(self) -> int:
        return len(self._get_current_object())

    def __bool__(self) -> bool:
        try:
            return bool(self._get_current_object())
        except RuntimeError:
            return False

    def __eq__(self, other: object) -> bool:
        return self._get_current_object() == other

    def __hash__(self) -> int:
        return hash(self._get_current_object())

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._get_current_object()(*args, **kwargs)

    def __repr__(self) -> str:
        try:
            target = self._get_current_object()
        except RuntimeError:
            return f"<{type(self).__name__} unbound>"
        return repr(target)


request: Any = _Proxy(_current_request)
current_app: Any = _Proxy(_current_app)


def copy_current_request_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Run ``func`` later, in another thread, inside the request it was decorated in."""
    ctx = _current_context()

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _request_ctx.set(ctx)
        try:
            return func(*args, **kwargs)
        finally:
            try:
                _request_ctx.reset(token)
            except ValueError:
                _request_ctx.set(None)

    return wrapper


def stream_with_context(generator_or_function: Any) -> Any:
    """Keep the request available while a response generator runs.

    Accepts a generator (``Response(stream_with_context(gen()))``) or decorates a
    generator function (``@stream_with_context``). The request context is set in
    the thread that reads the stream and removed when the stream ends or is closed.
    """
    try:
        generator = iter(generator_or_function)
    except TypeError:

        def decorator(*args: Any, **kwargs: Any) -> Any:
            return stream_with_context(generator_or_function(*args, **kwargs))

        return wraps(generator_or_function)(decorator)

    ctx = _current_context()

    def wrapped() -> Iterator[Any]:
        token = _request_ctx.set(ctx)
        try:
            yield from generator
        finally:
            try:
                close = getattr(generator, "close", None)
                if callable(close):
                    close()
            finally:
                try:
                    _request_ctx.reset(token)
                except ValueError:
                    _request_ctx.set(None)

    return wrapped()


__all__ = [
    "AppContext",
    "RequestContext",
    "copy_current_request_context",
    "current_app",
    "has_app_context",
    "has_request_context",
    "request",
    "stream_with_context",
]
