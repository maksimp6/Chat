"""The application: URL rules, blueprints, request hooks, error handlers, WSGI."""

from __future__ import annotations

import logging
import os
import sys
import traceback
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from webframe.datastructures import Headers
from webframe.exceptions import (
    HTTPError,
    InternalServerError,
    MethodNotAllowed,
    NotFound,
)
from webframe.globals import AppContext, RequestContext
from webframe.helpers import redirect, send_from_directory
from webframe.json import JSONProvider
from webframe.routing import RequestRedirect, Router, Rule
from webframe.templating import TemplateLoader
from webframe.wrappers import Request, Response

ViewFunc = Callable[..., Any]
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def get_root_path(import_name: str) -> str:
    """The folder of the module named ``import_name`` (the working directory as a fallback)."""
    module = sys.modules.get(import_name)
    file = getattr(module, "__file__", None) if module is not None else None
    if file:
        return os.path.dirname(os.path.abspath(file))
    try:
        import importlib.util

        spec = importlib.util.find_spec(import_name)
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin and spec.origin not in ("built-in", "frozen"):
        return os.path.dirname(os.path.abspath(spec.origin))
    return os.getcwd()


def _join_prefix(prefix: str | None, rule: str) -> str:
    if prefix is None:
        return rule
    if rule:
        return "/".join((prefix.rstrip("/"), rule.lstrip("/")))
    return prefix


class _RouteMethods:
    """``route`` and the ``get``/``post``/... shortcuts, shared by apps and blueprints."""

    def add_url_rule(
        self,
        rule: str,
        endpoint: str | None = None,
        view_func: ViewFunc | None = None,
        **options: Any,
    ) -> None:
        raise NotImplementedError

    def route(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        def decorator(func: ViewFunc) -> ViewFunc:
            endpoint = options.pop("endpoint", None)
            self.add_url_rule(rule, endpoint, func, **options)
            return func

        return decorator

    def get(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["GET"], **options)

    def post(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["POST"], **options)

    def put(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["PUT"], **options)

    def patch(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["PATCH"], **options)

    def delete(self, rule: str, **options: Any) -> Callable[[ViewFunc], ViewFunc]:
        return self.route(rule, methods=["DELETE"], **options)


class Blueprint(_RouteMethods):
    """A group of routes registered on an application under one name and URL prefix.

    Endpoints are ``<blueprint name>.<view name>``; hooks and error handlers added
    to a blueprint apply to its routes only.
    """

    def __init__(
        self,
        name: str,
        import_name: str,
        url_prefix: str | None = None,
        static_folder: str | None = None,
        template_folder: str | None = None,
    ) -> None:
        if not name or "." in name:
            raise ValueError("a blueprint name may not be empty or contain a dot")
        self.name = name
        self.import_name = import_name
        self.url_prefix = url_prefix
        self._deferred: list[Callable[[Application, str | None], None]] = []
        self.before_request_funcs: list[Callable[[], Any]] = []
        self.after_request_funcs: list[Callable[[Response], Response]] = []
        self.error_handlers: dict[Any, Callable[[Exception], Any]] = {}

    def add_url_rule(
        self,
        rule: str,
        endpoint: str | None = None,
        view_func: ViewFunc | None = None,
        **options: Any,
    ) -> None:
        if endpoint is not None and "." in endpoint:
            raise ValueError("a blueprint endpoint may not contain a dot")
        if endpoint is None:
            if view_func is None:
                raise ValueError("a rule needs an endpoint or a view function")
            endpoint = view_func.__name__
        full_endpoint = f"{self.name}.{endpoint}"

        def register(app: Application, prefix: str | None) -> None:
            app.add_url_rule(
                _join_prefix(prefix, rule), full_endpoint, view_func, **options
            )

        self._deferred.append(register)

    def before_request(self, func: Callable[[], Any]) -> Callable[[], Any]:
        self.before_request_funcs.append(func)
        return func

    def after_request(
        self, func: Callable[[Response], Response]
    ) -> Callable[[Response], Response]:
        self.after_request_funcs.append(func)
        return func

    def register_error_handler(
        self, code_or_exception: Any, func: Callable[[Exception], Any]
    ) -> None:
        self.error_handlers[code_or_exception] = func

    def errorhandler(
        self, code_or_exception: Any
    ) -> Callable[[Callable[[Exception], Any]], Callable[[Exception], Any]]:
        def decorator(func: Callable[[Exception], Any]) -> Callable[[Exception], Any]:
            self.register_error_handler(code_or_exception, func)
            return func

        return decorator

    def register(self, app: Application, url_prefix: str | None = None) -> None:
        prefix = self.url_prefix if url_prefix is None else url_prefix
        for register in self._deferred:
            register(app, prefix)
        app.before_request_funcs.setdefault(self.name, []).extend(
            self.before_request_funcs
        )
        app.after_request_funcs.setdefault(self.name, []).extend(
            self.after_request_funcs
        )
        app.error_handler_spec.setdefault(self.name, {}).update(self.error_handlers)

    def __repr__(self) -> str:
        return f"<Blueprint {self.name!r}>"


class Application(_RouteMethods):
    """A WSGI web application.

    ``Application(__name__)`` serves ``static/`` next to the module under ``/static/``
    and renders templates from ``templates/``. Routes come from ``add_url_rule``,
    ``route`` and registered blueprints; ``before_request``/``after_request`` hooks
    and error handlers apply to every request. The object is the WSGI callable;
    ``run`` serves it, ``test_client`` calls it in-process.
    """

    response_class = Response
    request_class = Request
    json_provider_class = JSONProvider

    def __init__(
        self,
        import_name: str,
        static_url_path: str | None = None,
        static_folder: str | None = "static",
        template_folder: str | None = "templates",
        root_path: str | None = None,
    ) -> None:
        self.import_name = import_name
        self.name = import_name if import_name != "__main__" else _main_name()
        self.root_path = root_path or get_root_path(import_name)
        self.static_folder = (
            os.path.join(self.root_path, static_folder) if static_folder else None
        )
        if static_url_path is None and static_folder:
            static_url_path = "/" + os.path.basename(static_folder.rstrip("/\\"))
        self.static_url_path = static_url_path
        self.template_folder = template_folder
        self.debug = False
        self.logger = logging.getLogger("logos.web")
        self.json: JSONProvider = self.json_provider_class(self)
        self.url_map = Router()
        self.view_functions: dict[str, ViewFunc] = {}
        self.blueprints: dict[str, Blueprint] = {}
        self.before_request_funcs: dict[str | None, list[Callable[[], Any]]] = {
            None: []
        }
        self.after_request_funcs: dict[
            str | None, list[Callable[[Response], Response]]
        ] = {None: []}
        self.teardown_request_funcs: list[Callable[[BaseException | None], Any]] = []
        self.error_handler_spec: dict[
            str | None, dict[Any, Callable[[Exception], Any]]
        ] = {None: {}}
        self._templates = TemplateLoader(
            os.path.join(self.root_path, template_folder or "templates")
        )
        if self.static_folder and self.static_url_path:
            self.add_url_rule(
                f"{self.static_url_path.rstrip('/')}/<path:filename>",
                endpoint="static",
                view_func=self.send_static_file,
                methods=["GET"],
            )

    # routes

    def add_url_rule(
        self,
        rule: str,
        endpoint: str | None = None,
        view_func: ViewFunc | None = None,
        methods: Iterable[str] | None = None,
        defaults: dict[str, Any] | None = None,
        **options: Any,
    ) -> None:
        if endpoint is None:
            if view_func is None:
                raise ValueError("a rule needs an endpoint or a view function")
            endpoint = view_func.__name__
        if methods is None:
            methods = getattr(view_func, "methods", None) or ("GET",)
        if isinstance(methods, str):
            raise TypeError("methods must be an iterable of strings, not a string")
        if view_func is not None:
            existing = self.view_functions.get(endpoint)
            if existing is not None and existing is not view_func:
                raise AssertionError(
                    "View function mapping is overwriting an existing endpoint "
                    f"function: {endpoint}"
                )
            self.view_functions[endpoint] = view_func
        self.url_map.add(Rule(rule, endpoint, methods, defaults))

    def register_blueprint(self, blueprint: Blueprint, **options: Any) -> None:
        known = self.blueprints.get(blueprint.name)
        if known is not None and known is not blueprint:
            raise ValueError(
                f"a different blueprint named {blueprint.name!r} is already registered"
            )
        self.blueprints[blueprint.name] = blueprint
        blueprint.register(self, **options)

    def send_static_file(self, filename: str) -> Response:
        if not self.static_folder:
            raise NotFound()
        return send_from_directory(self.static_folder, filename)

    # hooks

    def before_request(self, func: Callable[[], Any]) -> Callable[[], Any]:
        self.before_request_funcs[None].append(func)
        return func

    def after_request(
        self, func: Callable[[Response], Response]
    ) -> Callable[[Response], Response]:
        self.after_request_funcs[None].append(func)
        return func

    def teardown_request(
        self, func: Callable[[BaseException | None], Any]
    ) -> Callable[[BaseException | None], Any]:
        self.teardown_request_funcs.append(func)
        return func

    def register_error_handler(
        self, code_or_exception: Any, func: Callable[[Exception], Any]
    ) -> None:
        """Handle an HTTP status code or an exception class (and its subclasses) with ``func``."""
        if isinstance(code_or_exception, int) or (
            isinstance(code_or_exception, type)
            and issubclass(code_or_exception, BaseException)
        ):
            self.error_handler_spec[None][code_or_exception] = func
            return
        raise ValueError(
            "an error handler is registered for a status code or an exception class"
        )

    def errorhandler(
        self, code_or_exception: Any
    ) -> Callable[[Callable[[Exception], Any]], Callable[[Exception], Any]]:
        def decorator(func: Callable[[Exception], Any]) -> Callable[[Exception], Any]:
            self.register_error_handler(code_or_exception, func)
            return func

        return decorator

    # contexts and clients

    def app_context(self) -> AppContext:
        return AppContext(self)

    def request_context(self, environ: dict[str, Any]) -> RequestContext:
        return RequestContext(self, self.request_class(environ))

    def test_client(self) -> Any:
        from webframe.testing import Client

        return Client(self)

    def render_template(self, template_name: str, context: dict[str, Any]) -> str:
        return self._templates.render(
            template_name, context, json_dumps=self.json.dumps
        )

    # responses

    def make_response(self, rv: Any) -> Response:
        """The response for a view's return value.

        A response is kept; text, bytes or an iterator become a body; a dict or
        list becomes JSON; an HTTP error becomes its answer. A tuple adds a status
        and/or headers: ``(body, status)``, ``(body, headers)``, ``(body, status,
        headers)``.
        """
        status: Any = None
        headers: Any = None
        if isinstance(rv, tuple):
            if len(rv) == 3:
                rv, status, headers = rv
            elif len(rv) == 2:
                if isinstance(rv[1], (Headers, dict, tuple, list)):
                    rv, headers = rv
                else:
                    rv, status = rv
            else:
                raise TypeError(
                    "The view function did not return a valid response tuple. The "
                    "tuple must have the form (body, status, headers), (body, status), "
                    "or (body, headers)."
                )
        if rv is None:
            raise TypeError(
                "The view function did not return a valid response. The function "
                "either returned None or ended without a return statement."
            )
        if isinstance(rv, HTTPError):
            rv = rv.get_response()
        if not isinstance(rv, Response):
            if isinstance(rv, (str, bytes, bytearray)) or isinstance(rv, Iterator):
                rv = self.response_class(rv, status=status, headers=headers)
                status = headers = None
            elif isinstance(rv, (dict, list)):
                rv = self.json.response(rv)
            else:
                raise TypeError(
                    "The view function did not return a valid response. The return "
                    f"type must be a string, dict, list, tuple with headers or status, "
                    f"Response instance, or WSGI callable, but it was a {type(rv).__name__}."
                )
        if status is not None:
            rv.status = status
        if headers:
            rv.headers.update(headers)
        return rv

    def make_default_options_response(self, path: str) -> Response:
        response = self.response_class(status=200)
        response.headers["Allow"] = ", ".join(
            sorted(self.url_map.allowed_methods(path))
        )
        return response

    # dispatch

    def _match_request(self, request: Request) -> None:
        try:
            rule, args = self.url_map.match(request.path, request.method)
        except (NotFound, MethodNotAllowed, RequestRedirect) as error:
            request.routing_exception = error
            return
        request.url_rule = rule
        request.endpoint = rule.endpoint
        request.view_args = args

    def preprocess_request(self, request: Request) -> Any:
        for name in (None, request.blueprint):
            if name is None or name in self.before_request_funcs:
                for func in self.before_request_funcs.get(name, ()):
                    rv = func()
                    if rv is not None:
                        return rv
        return None

    def dispatch_request(self, request: Request) -> Any:
        if request.routing_exception is not None:
            raise request.routing_exception
        rule: Rule = request.url_rule
        if rule.provide_automatic_options and request.method == "OPTIONS":
            return self.make_default_options_response(request.path)
        return self.view_functions[rule.endpoint](**(request.view_args or {}))

    def process_response(self, request: Request, response: Response) -> Response:
        names: tuple[str | None, ...] = (
            (request.blueprint, None) if request.blueprint else (None,)
        )
        for name in names:
            for func in reversed(self.after_request_funcs.get(name, [])):
                response = func(response)
        return response

    def finalize_request(
        self, request: Request, rv: Any, from_error_handler: bool = False
    ) -> Response:
        response = self.make_response(rv)
        try:
            response = self.process_response(request, response)
        except Exception:
            if not from_error_handler:
                raise
            self.logger.exception(
                "Request finalizing failed with an error while handling an error"
            )
        return response

    def full_dispatch_request(self, request: Request) -> Response:
        try:
            rv = self.preprocess_request(request)
            if rv is None:
                rv = self.dispatch_request(request)
        except Exception as error:
            rv = self.handle_user_exception(request, error)
        return self.finalize_request(request, rv)

    def _find_error_handler(
        self, error: BaseException, blueprint: str | None
    ) -> Callable[[Exception], Any] | None:
        code = error.code if isinstance(error, HTTPError) else None
        for name in (blueprint, None):
            handlers = self.error_handler_spec.get(name)
            if not handlers:
                continue
            if code is not None and code in handlers:
                return handlers[code]
            for cls in type(error).__mro__:
                if cls in handlers:
                    return handlers[cls]
        return None

    def handle_user_exception(self, request: Request, error: Exception) -> Any:
        """A registered handler's answer for ``error``; an HTTP error's own answer; else re-raised."""
        if isinstance(error, RequestRedirect):
            return redirect(error.new_path, error.code)
        handler = self._find_error_handler(error, request.blueprint)
        if handler is None:
            if isinstance(error, HTTPError):
                return error.get_response()
            raise error
        return handler(error)

    def handle_exception(self, request: Request, error: Exception) -> Response:
        """Log an unhandled error and answer 500 (through a 500 handler when one exists)."""
        self.logger.exception("Exception on %s [%s]", request.path, request.method)
        server_error: Any = InternalServerError(original_exception=error)
        if self.debug:
            server_error = Response(
                traceback.format_exc(), status=500, mimetype="text/plain"
            )
        else:
            handler = self._find_error_handler(server_error, request.blueprint)
            if handler is not None:
                try:
                    server_error = handler(server_error)
                except Exception:
                    self.logger.exception("The error handler failed")
                    server_error = InternalServerError(original_exception=error)
        try:
            return self.finalize_request(request, server_error, from_error_handler=True)
        except Exception:
            self.logger.exception("The error response could not be built")
            return InternalServerError().get_response()

    # WSGI

    def wsgi_app(
        self, environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> Any:
        request = self.request_class(environ)
        ctx = RequestContext(self, request)
        ctx.push()
        error: BaseException | None = None
        try:
            self._match_request(request)
            try:
                response = self.full_dispatch_request(request)
            except Exception as exc:
                error = exc
                response = self.handle_exception(request, exc)
            status, headers, app_iter = response.get_wsgi_response(environ)
            start_response(status, headers)
            return app_iter
        finally:
            for func in self.teardown_request_funcs:
                try:
                    func(error)
                except Exception:
                    self.logger.exception("A teardown function failed")
            request.close()
            ctx.pop()

    def __call__(
        self, environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> Any:
        return self.wsgi_app(environ, start_response)

    def run(
        self,
        host: str | None = None,
        port: int | None = None,
        debug: bool | None = None,
        use_reloader: bool = False,
        threaded: bool = True,
        **options: Any,
    ) -> None:
        """Serve the application on ``host:port`` until interrupted.

        ``threaded`` runs every connection on its own thread. ``use_reloader`` is
        accepted for compatibility and does nothing: the deploy restarts the process.
        """
        if debug is not None:
            self.debug = bool(debug)
        if self.debug:
            self.logger.setLevel(logging.DEBUG)
        from webframe.serving import serve

        serve(self, host or "127.0.0.1", int(port or 5000), threaded=threaded)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


def _main_name() -> str:
    file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not file:
        return "__main__"
    return os.path.splitext(os.path.basename(file))[0]


__all__ = ["Application", "Blueprint", "get_root_path"]
