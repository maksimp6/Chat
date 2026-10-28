"""Russian texts for the machine error codes that the chat routes answer with.

An error answer keeps its machine ``error`` code and its HTTP status; ``detail``
says what happened and what to do, for the person in front of the screen.
"""

from __future__ import annotations

from typing import Any

# The provider key is added in the «Ключи провайдеров» window (🔑 in the top bar).
PROVIDER_KEY_MISSING = (
    "Нет ключа провайдера. Нажмите 🔑 «Ключи провайдеров» в верхней панели, "
    "введите API key (для Yandex Cloud вместе с Project ID), нажмите «Подключить» "
    "и повторите запрос."
)
PROVIDER_KEY_EXPIRED = (
    "Срок действия ключа провайдера истёк. Нажмите 🔑 «Ключи провайдеров» в верхней "
    "панели, введите ключ заново (для Yandex Cloud вместе с Project ID), нажмите "
    "«Подключить» и повторите запрос."
)
PROVIDER_KEY_UNAVAILABLE = (
    "Ключ провайдера не найден или больше не действует. Нажмите 🔑 «Ключи провайдеров» "
    "в верхней панели, введите API key (для Yandex Cloud вместе с Project ID), "
    "нажмите «Подключить» и повторите запрос."
)
EMPTY_MODEL_REPLY = (
    "Модель закончила работу, но не написала текст ответа (например, только вызвала "
    "инструменты). Нажмите «Повторить» или задайте вопрос иначе."
)
_REQUEST_DATA_MISSING = (
    "Сервер не получил данные запроса. Обновите страницу и повторите действие."
)
_CONVERSATION_SETTINGS_BROKEN = (
    "Настройки диалога повреждены. Откройте настройки диалога и сохраните их заново."
)

DETAILS: dict[str, str] = {
    # Requests and dialogs.
    "JSON object is required": _REQUEST_DATA_MISSING,
    "json_body_required": "Сервер не получил данные запроса. Повторите отправку.",
    "conversation_not_found": (
        "Диалог не найден: его удалили. Откройте другой диалог или создайте новый."
    ),
    "conversation_id is required": (
        "Не выбран диалог. Откройте или создайте диалог и отправьте сообщение снова."
    ),
    "model must be a string": (
        "Не выбрана модель. Выберите модель и отправьте сообщение снова."
    ),
    "model_requires_dedicated_api": (
        "Эта модель не отвечает в текстовом чате. Выберите текстовую модель."
    ),
    "model_uses_completion_api": "Эта модель не ведёт диалог. Выберите другую модель.",
    "invalid_attachments": (
        "Фото не приняты: можно прикрепить до 4 фото PNG, JPEG или WebP, каждое "
        "до 5 МБ. Уберите лишние или неподходящие фото и отправьте снова."
    ),
    "message or attachments are required": (
        "Сообщение пустое. Напишите текст или прикрепите фото."
    ),
    "model_does_not_accept_images": (
        "Эта модель не принимает фото. Выберите модель с поддержкой фото."
    ),
    "invalid_params": (
        "Параметры запроса не приняты. Проверьте настройки модели и повторите."
    ),
    "invalid_settings": (
        "Настройки диалога не приняты. Проверьте настройки и инструменты диалога."
    ),
    "invalid_retry_message": (
        "Повторить нельзя: сохранённый вопрос не совпадает с этим. "
        "Отправьте вопрос заново."
    ),
    "invalid_skill_selection": (
        "Выбранные навыки недоступны. Измените выбор навыков и повторите."
    ),
    "invalid_role": "Роль не найдена. Выберите роль из списка.",
    "invalid_tool_categories": (
        "Выбраны неизвестные категории инструментов. "
        "Обновите страницу и выберите категории заново."
    ),
    "model_not_available_for_dialog": (
        "Эта модель недоступна для диалога. Выберите другую модель."
    ),
    "model_catalog_unavailable": (
        "Список моделей провайдера сейчас недоступен. Повторите позже; если ошибка "
        "не проходит, проверьте ключ в 🔑 «Ключи провайдеров»."
    ),
    # MCP servers and approved tool actions.
    "invalid_mcp_server": (
        "Сервер MCP не сохранён: укажите адрес https://… без логина и пароля "
        "в адресе и сохраните снова."
    ),
    "mcp_server_not_found": (
        "Сервер MCP не найден: его уже удалили. Обновите список серверов."
    ),
    "tool_action_failed": (
        "Инструмент не выполнил действие. Причина записана в журнале сервера. "
        "Попросите модель повторить действие в чате."
    ),
    "tool_execution_failed": (
        "Сервер не завершил подтверждённое действие. "
        "Проверьте, выполнилось ли оно, прежде чем повторять."
    ),
    # Provider access: the completion, embedding, classification and voice routes.
    "provider_credential_unavailable": (
        "Нет действующего ключа провайдера. "
        "Добавьте ключ в «Ключи провайдеров» и повторите."
    ),
    "completion_not_configured": PROVIDER_KEY_UNAVAILABLE,
    "embeddings_not_configured": PROVIDER_KEY_UNAVAILABLE,
    "classification_not_configured": PROVIDER_KEY_UNAVAILABLE,
    "provider_quota_exceeded": (
        "Достигнут лимит запросов к провайдеру. Повторите после времени сброса."
    ),
    "unknown_parameters": (
        "Запрос содержит неизвестные параметры. Уберите их и повторите."
    ),
    # Completion.
    "unsupported_completion_model": (
        "Эта модель не поддерживается API завершения текста. "
        "Укажите модель из поля model ответа."
    ),
    "invalid_completion_request": (
        "Запрос не принят: проверьте сообщения и параметры генерации."
    ),
    "completion_upstream_unanswered": (
        "Провайдер не ответил на запрос. Повторите позже."
    ),
    "completion_upstream_invalid": (
        "Провайдер вернул ответ неверного формата. Повторите запрос."
    ),
    "completion_incomplete": (
        "Модель не завершила ответ: он обрезан или отфильтрован. "
        "Повторите запрос или измените его."
    ),
    # Embeddings.
    "send model or mode, not both": "Укажите либо model, либо mode, но не оба поля.",
    "unknown embedding model": (
        "Модель эмбеддингов не найдена. Выберите модель из списка models."
    ),
    "mode must be doc or query": "Поле mode принимает значения doc или query.",
    "dimensions requires an embedding v2 model": (
        "Поле dimensions работает только с моделями эмбеддингов v2. "
        "Уберите его или выберите модель v2."
    ),
    "invalid_dimensions": (
        "Такое значение dimensions не поддерживается. "
        "Выберите значение из списка dimensions."
    ),
    "invalid_embedding_request": (
        "Тексты не приняты: передайте text или список texts из непустых строк, "
        "не больше 64 за запрос; остальные отправьте отдельным запросом."
    ),
    "embedding_deadline_exceeded": (
        "Провайдер не успел обработать все тексты. "
        "Отправьте оставшиеся тексты отдельным запросом."
    ),
    "embedding_upstream_failed": (
        "Провайдер не ответил или вернул неверные данные. "
        "Отправьте оставшиеся тексты позже."
    ),
    "embedding_dimensions_mismatch": (
        "Провайдер вернул векторы разной длины. Повторите запрос."
    ),
    # Classification.
    "classification_upstream_unknown": (
        "Провайдер не ответил, и неизвестно, выполнен ли запрос. Повторите позже."
    ),
    "classification_upstream_failed": (
        "Провайдер отклонил запрос или вернул ошибку. "
        "Проверьте ключ и параметры запроса и повторите."
    ),
    "classification_upstream_invalid": (
        "Провайдер вернул ответ неверного формата. Повторите запрос."
    ),
    # Pictures.
    "not_an_image_model": (
        "Эта модель не создаёт изображения. Выберите модель изображений."
    ),
    "invalid_conversation_id": "Диалог не распознан. Откройте его заново и повторите.",
    "request_id is required": (
        "Сервер не получил номер запроса. Обновите страницу и повторите."
    ),
    "message is required": "Описание картинки пустое. Напишите, что нарисовать.",
    "prompt_too_long": (
        "Запрос для изображения слишком длинный. Сократите его и повторите."
    ),
    "invalid_image_size": (
        "Этот размер изображения не поддерживается. Выберите другой размер."
    ),
    "image_model_does_not_take_photos": (
        "Модель изображений не принимает фото: уберите фото или выберите другую модель."
    ),
    "request_id_conflict": (
        "Этот запрос изображения уже отправлен с другим текстом. "
        "Отправьте запрос заново."
    ),
    "image_generation_failed": (
        "Провайдер не создал изображение. Измените запрос и повторите."
    ),
    "image_generation_storage_failed": (
        "Запрос или готовая картинка не сохранились на сервере. Повторная платная "
        "генерация не запускалась. Отправьте запрос заново."
    ),
    "invalid_classification_request": (
        "Запрос классификации не принят: проверьте model, text и labels (уникальные "
        "строки) и samples, затем повторите."
    ),
    "invalid_request_id": "Запрос изображения не распознан. Отправьте его заново.",
    "image_request_not_found": (
        "Запрос изображения не найден на сервере. Отправьте его заново."
    ),
    "invalid_image_id": "Ссылка на картинку неверна. Обновите диалог.",
    "image_not_found": "Картинка не найдена на сервере. Сгенерируйте её заново.",
    # Voice.
    "voice_session_cancelled": "Голосовая сессия отменена. Начните новую.",
    "text or PCM WAV audio is required": (
        "Нет ни текста, ни записи. Скажите фразу или напишите текст и отправьте снова."
    ),
    "unsupported_voice_transport": (
        "Этот способ передачи голоса не поддерживается. Выберите другой режим голоса."
    ),
    "select_an_available_text_model_for_speechkit_pipeline": (
        "Для режима SpeechKit нужна доступная текстовая модель. "
        "Выберите её и начните сессию снова."
    ),
    "select_a_realtime_voice_model": (
        "Выбрана не голосовая модель. Выберите модель Realtime для голоса."
    ),
    "voice_is_not_compatible_with_model": (
        "Этот голос не подходит к выбранной модели. Выберите другой голос."
    ),
    "role_is_not_compatible_with_voice": (
        "Эта роль не подходит к выбранному голосу. Выберите другую роль или голос."
    ),
    "response_mode_must_be_audio_both_or_text": (
        "Режим ответа не распознан. Выберите: голос, голос и текст или только текст."
    ),
    "speech_tts_live_requires_audio_output": (
        "Эта модель отвечает только голосом. Выберите режим ответа с голосом."
    ),
    "invalid_conversation_settings": _CONVERSATION_SETTINGS_BROKEN,
    "instructions_must_be_text": (
        "Инструкции должны быть текстом. Исправьте их в настройках диалога."
    ),
    "invalid_text_model_uri": (
        "Не удалось составить адрес текстовой модели. Выберите другую модель."
    ),
    "text_model_catalog_unavailable": (
        "Список текстовых моделей сейчас недоступен. Повторите позже."
    ),
    "select_an_available_discovered_text_model": (
        "Эта текстовая модель недоступна. Выберите другую модель."
    ),
    "voice_session_not_found": (
        "Голосовая сессия закончилась или не найдена. Начните новую."
    ),
    "speech_tts_live_requires_text": (
        "Эта модель принимает только текст. Напишите текст вместо записи."
    ),
    "realtime_requires_pcm16_wav_audio": (
        "Запись должна быть в формате WAV (PCM, 16 бит). Повторите запись."
    ),
    "voice_session_input_closed": (
        "Сессия уже приняла ввод. Начните новую сессию для следующей фразы."
    ),
    "audio_chunk_is_empty": "Запись пустая. Проверьте микрофон и повторите.",
    "audio_exceeds_session_upload_limit": (
        "Запись слишком длинная. Говорите короче и повторите."
    ),
    "text_is_required": "Текст пустой. Напишите фразу и отправьте снова.",
    "speechkit_pipeline_requires_microphone_audio": (
        "В режиме SpeechKit нужна запись с микрофона. Скажите фразу вместо текста."
    ),
    "voice_output_not_ready": "Голосовой ответ ещё не готов. Подождите и повторите.",
}


def error_body(
    error: str, *, detail_of: str | None = None, **fields: Any
) -> dict[str, Any]:
    """An error body: ``error`` as given, its Russian ``detail`` and the extra fields.

    ``detail_of`` names the text when ``error`` is not a code of its own, for example
    the message of a validation error.
    """
    return {"error": error, "detail": DETAILS[detail_of or error], **fields}


__all__ = [
    "DETAILS",
    "EMPTY_MODEL_REPLY",
    "PROVIDER_KEY_EXPIRED",
    "PROVIDER_KEY_MISSING",
    "PROVIDER_KEY_UNAVAILABLE",
    "error_body",
]
