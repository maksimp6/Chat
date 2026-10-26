import threading

from chat import mcp_routes


def test_first_messages_of_one_chat_create_one_provider_conversation(monkeypatch):
    mapping = {}
    created = []
    creation_lock = threading.Lock()

    class FakeClient:
        def create_conversation(self, execution_trace=None):
            with creation_lock:
                created.append(1)
                return {"id": f"provider-{len(created)}"}

    monkeypatch.setattr(mcp_routes, "get_yandex_conversation", lambda conv_id: mapping.get(conv_id))
    monkeypatch.setattr(mcp_routes, "is_provider_pending", lambda conv_id: True)
    monkeypatch.setattr(
        mcp_routes, "set_yandex_conversation", lambda conv_id, provider_id: mapping.update({conv_id: provider_id})
    )

    threads = [
        threading.Thread(target=mcp_routes._provider_conversation_id, args=(FakeClient(), "chat-1", None))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert mapping == {"chat-1": "provider-1"}


def test_photo_exchange_is_repeated_for_the_text_follow_up(monkeypatch):
    monkeypatch.setattr(
        mcp_routes,
        "get_messages",
        lambda conv_id: [
            {"role": "user", "text": "Что на фото?", "source": "photo"},
            {"role": "assistant", "text": "Кот на диване", "source": None},
            {"role": "user", "text": "Объясни ответ", "source": None},
        ],
    )

    context = mcp_routes._photo_exchange_context("chat-1")

    assert "Что на фото?" in context
    assert "Кот на диване" in context
    assert "Объясни ответ" not in context


def test_photo_context_recognizes_current_photo_count_without_claiming_later_answer(monkeypatch):
    monkeypatch.setattr(
        mcp_routes,
        "get_messages",
        lambda conv_id: [
            {"role": "user", "text": "Фото без ответа", "attachments": {"images": 1}},
            {"role": "user", "text": "Другой вопрос"},
            {"role": "assistant", "text": "Ответ на другой вопрос"},
        ],
    )

    context = mcp_routes._photo_exchange_context("chat-1")

    assert "Фото без ответа" in context
    assert "Ответ на другой вопрос" not in context


def test_real_logos_client_keeps_local_settings_id_through_tool_continuation(monkeypatch):
    sent = []
    proposals = iter([
        {"id": "tool-step", "output": [{
            "type": "function_call", "name": "offline_tool", "call_id": "call-1", "arguments": "{}"
        }]},
        {"id": "final-step", "output": []},
    ])
    client = object.__new__(mcp_routes.LogosClient)

    def fake_provider(**kwargs):
        sent.append(kwargs)
        return next(proposals)

    monkeypatch.setattr(client, "_ask_provider", fake_provider)
    monkeypatch.setattr(client, "_execute_single_tool", lambda *args, **kwargs: {"result": "done"})
    monkeypatch.setattr(mcp_routes.mcp_storage, "list_servers", lambda **kwargs: [])
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda: "owner")

    response = client.ask_with_mcp(
        "run", "yandexgpt-5-lite", conversation_id="provider-id", settings_conversation_id="local-id"
    )

    assert response["id"] == "final-step"
    assert len(sent) == 2
    assert all(call["conversation_id"] == "provider-id" for call in sent)
    assert all(call["settings_conversation_id"] == "local-id" for call in sent)
