import json
import runpy

import run_agent
import run_agent_loop
import yandex_agent_loop


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _UrlResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class _CaptureLogger:
    def __init__(self):
        self.events = []

    def emit(self, level, message, stream_name="alice_pro"):
        self.events.append((level, message, stream_name))


def test_run_agent_dispatches_git_filesystem_and_unknown_tools(monkeypatch):
    monkeypatch.setattr(run_agent, "GIT_TOOLS", {"git-tool": {}})
    monkeypatch.setattr(run_agent, "FS_TOOLS", {"fs-tool": {}})
    monkeypatch.setattr(run_agent, "execute_git_tool", lambda name, args: ("git", name, args))
    monkeypatch.setattr(run_agent, "execute_fs_tool", lambda name, args: ("fs", name, args))

    assert run_agent.dispatch_any_tool("git-tool", {"a": 1}) == ("git", "git-tool", {"a": 1})
    assert run_agent.dispatch_any_tool("fs-tool", {"b": 2}) == ("fs", "fs-tool", {"b": 2})
    assert "не найден" in run_agent.dispatch_any_tool("missing", {})["error"]


def test_run_agent_queries_yandex_with_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _JsonResponse(
            {"result": {"alternatives": [{"message": {"text": '{"text":"done"}'}}]}}
        )

    monkeypatch.setenv("YANDEX_PROJECT_ID", "project")
    monkeypatch.setenv("YANDEX_API_KEY", "secret")
    monkeypatch.setattr(run_agent.requests, "post", fake_post)

    result = run_agent.query_yandex([{"role": "user", "text": "hello"}])

    assert result == '{"text":"done"}'
    assert captured["url"] == run_agent.API_URL
    assert captured["timeout"] == 45
    assert captured["headers"]["Authorization"] == "Api-Key secret"
    assert captured["json"]["modelUri"] == "gpt://project/yandexgpt/latest"


def test_run_agent_task_handles_tools_text_bad_json_and_limits(monkeypatch, capsys):
    fence = chr(96) * 3
    responses = iter(
        [
            fence + 'json\n{"tool":"tool-a","args":{"x":1}}\n' + fence,
            '{"text":"finished"}',
        ]
    )
    monkeypatch.setattr(run_agent, "query_yandex", lambda messages: next(responses))
    monkeypatch.setattr(run_agent, "dispatch_any_tool", lambda name, args: {"ok": [name, args]})
    assert run_agent.run_task("task") == "finished"
    assert "Вызов инструмента" in capsys.readouterr().out

    monkeypatch.setattr(run_agent, "query_yandex", lambda messages: "not-json")
    assert run_agent.run_task("task") == "not-json"

    monkeypatch.setattr(run_agent, "query_yandex", lambda messages: '{"other":1}')
    assert run_agent.run_task("task") == '{"other":1}'

    monkeypatch.setattr(run_agent, "query_yandex", lambda messages: '{"tool":"tool-a"}')
    monkeypatch.setattr(run_agent, "dispatch_any_tool", lambda name, args: {"ok": True})
    assert run_agent.run_task("task", max_turns=1) == "Превышен лимит шагов выполнения."


def test_run_agent_main_entrypoint(monkeypatch, capsys):
    monkeypatch.setenv("YANDEX_PROJECT_ID", "project")
    monkeypatch.setenv("YANDEX_API_KEY", "secret")
    monkeypatch.setattr(
        run_agent.requests,
        "post",
        lambda *args, **kwargs: _JsonResponse(
            {"result": {"alternatives": [{"message": {"text": '{"text":"main-done"}'}}]}}
        ),
    )

    runpy.run_path(run_agent.__file__, run_name="__main__")

    output = capsys.readouterr().out
    assert "Финальный ответ агента" in output
    assert "main-done" in output


def test_openai_agent_builds_tools_and_handles_network_and_invalid_response(monkeypatch):
    schemas = [{"name": "x", "description": "X"}]
    assert run_agent_loop.build_openai_tools(schemas) == [
        {"type": "function", "function": schemas[0]}
    ]

    logger = _CaptureLogger()
    monkeypatch.setattr(run_agent_loop, "yc_logger", logger)

    def fail_post(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(run_agent_loop.requests, "post", fail_post)
    assert "Ошибка сетевого запроса" in run_agent_loop.execute_agent_turn("task", max_turns=1)
    assert logger.events[-1][0] == "ERROR"

    monkeypatch.setattr(
        run_agent_loop.requests,
        "post",
        lambda *args, **kwargs: _JsonResponse({"unexpected": True}),
    )
    assert "Пустой или ошибочный ответ API" in run_agent_loop.execute_agent_turn(
        "task", max_turns=1
    )


def test_openai_agent_handles_final_answer_tool_calls_and_limit(monkeypatch, capsys):
    logger = _CaptureLogger()
    monkeypatch.setattr(run_agent_loop, "yc_logger", logger)

    monkeypatch.setattr(
        run_agent_loop.requests,
        "post",
        lambda *args, **kwargs: _JsonResponse(
            {"choices": [{"message": {"content": "done", "tool_calls": []}}]}
        ),
    )
    assert run_agent_loop.execute_agent_turn("task", max_turns=1) == "done"

    responses = iter(
        [
            _JsonResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "tool-a",
                                            "arguments": '{"x":1}',
                                        },
                                    },
                                    {
                                        "function": {
                                            "name": "tool-b",
                                            "arguments": "{bad",
                                        },
                                    },
                                ],
                            }
                        }
                    ]
                }
            ),
            _JsonResponse({"choices": [{"message": {"content": "after-tools"}}]}),
        ]
    )
    monkeypatch.setattr(run_agent_loop.requests, "post", lambda *args, **kwargs: next(responses))
    calls = []
    monkeypatch.setattr(
        run_agent_loop,
        "dispatch_tool",
        lambda name, args: calls.append((name, args)) or {"ok": True},
    )

    assert run_agent_loop.execute_agent_turn("task", max_turns=2) == "after-tools"
    assert calls == [("tool-a", {"x": 1}), ("tool-b", {})]
    assert "Исполнение инструмента" in capsys.readouterr().out

    monkeypatch.setattr(
        run_agent_loop.requests,
        "post",
        lambda *args, **kwargs: _JsonResponse(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "tool-a",
                                        "arguments": "{}",
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        ),
    )
    assert "Превышен лимит итераций" in run_agent_loop.execute_agent_turn("task", max_turns=1)


def test_openai_agent_main_entrypoint(monkeypatch, capsys):
    monkeypatch.setattr(
        run_agent_loop.requests,
        "post",
        lambda *args, **kwargs: _JsonResponse(
            {"choices": [{"message": {"content": "main-answer"}}]}
        ),
    )

    runpy.run_path(run_agent_loop.__file__, run_name="__main__")

    output = capsys.readouterr().out
    assert "Финальный ответ агента" in output
    assert "main-answer" in output


def test_yandex_iam_token_builds_signed_jwt(monkeypatch, tmp_path):
    key_path = tmp_path / "iam.json"
    key_path.write_text(
        json.dumps(
            {
                "id": "key-id",
                "service_account_id": "service-id",
                "private_key": "PRIVATE KEY",
            }
        ),
        encoding="utf-8",
    )

    captured = {}

    class FakeProcess:
        def communicate(self, data):
            captured["signed"] = data
            return b"signature", b""

    def fake_popen(cmd, stdin, stdout, stderr):
        captured["cmd"] = cmd
        return FakeProcess()

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _UrlResponse({"iamToken": "iam-token"})

    monkeypatch.setattr(yandex_agent_loop.time, "time", lambda: 1000)
    monkeypatch.setattr(yandex_agent_loop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(yandex_agent_loop.urllib.request, "urlopen", fake_urlopen)

    token = yandex_agent_loop.get_iam_token(str(key_path))

    assert token == "iam-token"
    assert captured["timeout"] == 10
    assert "openssl" in captured["cmd"]
    assert captured["signed"].count(b".") == 1
    request_body = json.loads(captured["request"].data.decode("utf-8"))
    assert request_body["jwt"].count(".") == 2


def test_yandex_tool_schema_conversion_uses_default_parameters():
    converted = yandex_agent_loop.convert_tools_to_yandex(
        [
            {"name": "one", "description": "One", "parameters": {"type": "object"}},
            {"name": "two", "description": "Two"},
        ]
    )
    assert converted[0]["function"]["parameters"] == {"type": "object"}
    assert converted[1]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_yandex_agent_handles_text_tools_and_iteration_limit(monkeypatch):
    logger = _CaptureLogger()
    monkeypatch.setattr(yandex_agent_loop, "yc_logger", logger)
    monkeypatch.setattr(yandex_agent_loop, "get_iam_token", lambda: "iam-token")

    monkeypatch.setattr(
        yandex_agent_loop.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _UrlResponse(
            {"result": {"alternatives": [{"message": {"text": "done"}}]}}
        ),
    )
    assert yandex_agent_loop.execute_yandex_turn("task", max_turns=1) == "done"

    responses = iter(
        [
            _UrlResponse(
                {
                    "result": {
                        "alternatives": [
                            {
                                "message": {
                                    "toolCallList": {
                                        "toolCalls": [
                                            {
                                                "functionCall": {
                                                    "name": "tool-a",
                                                    "arguments": {"x": 1},
                                                }
                                            },
                                            {"functionCall": {"name": "tool-b"}},
                                        ]
                                    }
                                }
                            }
                        ]
                    }
                }
            ),
            _UrlResponse(
                {"result": {"alternatives": [{"message": {"text": "after-tools"}}]}}
            ),
        ]
    )
    monkeypatch.setattr(
        yandex_agent_loop.urllib.request, "urlopen", lambda *args, **kwargs: next(responses)
    )
    calls = []
    monkeypatch.setattr(
        yandex_agent_loop,
        "dispatch_tool",
        lambda name, args: calls.append((name, args)) or {"ok": True},
    )

    assert yandex_agent_loop.execute_yandex_turn("task", max_turns=2) == "after-tools"
    assert calls == [("tool-a", {"x": 1}), ("tool-b", {})]

    monkeypatch.setattr(
        yandex_agent_loop.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _UrlResponse(
            {
                "result": {
                    "alternatives": [
                        {
                            "message": {
                                "toolCallList": {
                                    "toolCalls": [
                                        {"functionCall": {"name": "tool-a", "arguments": {}}}
                                    ]
                                }
                            }
                        }
                    ]
                }
            }
        ),
    )
    assert "Превышен лимит итераций" in yandex_agent_loop.execute_yandex_turn(
        "task", max_turns=1
    )


def test_yandex_agent_main_entrypoint(monkeypatch, tmp_path, capsys):
    key_path = tmp_path / "iam.json"
    key_path.write_text(
        json.dumps(
            {
                "id": "key-id",
                "service_account_id": "service-id",
                "private_key": "PRIVATE KEY",
            }
        ),
        encoding="utf-8",
    )

    class FakeProcess:
        def communicate(self, data):
            return b"signature", b""

    monkeypatch.setenv("YC_IAM_KEY", str(key_path))
    monkeypatch.setattr(yandex_agent_loop.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    def fake_urlopen(request, timeout):
        if "iam.api.cloud.yandex.net" in request.full_url:
            return _UrlResponse({"iamToken": "iam-token"})
        return _UrlResponse({"result": {"alternatives": [{"message": {"text": "main-yandex"}}]}})

    monkeypatch.setattr(yandex_agent_loop.urllib.request, "urlopen", fake_urlopen)

    runpy.run_path(yandex_agent_loop.__file__, run_name="__main__")

    output = capsys.readouterr().out
    assert "Ответ YandexGPT" in output
    assert "main-yandex" in output
