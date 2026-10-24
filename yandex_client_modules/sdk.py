"""
sdk.py - Минимальный SDK для работы с Yandex AI Studio
"""

import requests
from api.yandex_transport import yandex_request
import json

from core.config import upstream_model_id


class AliceSDK:
    """SDK для работы с Yandex AI Studio API"""

    def __init__(self, api_key, project_id, base_url="https://ai.api.cloud.yandex.net/v1"):
        self.api_key = api_key
        self.project_id = project_id
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Api-Key {api_key}",
                "Content-Type": "application/json",
                "x-yc-project-id": project_id,
                "OpenAI-Project": project_id,
            }
        )

    def create_conversation(self):
        """Создать новый диалог"""
        resp = yandex_request(
            self.session.post,
            "POST",
            f"{self.base_url}/conversations",
            base_url=self.base_url,
            json={"metadata": {}, "items": []},
        )
        resp.raise_for_status()
        return resp.json()

    def send_message(self, conv_id, message, model="logos-runtime"):
        """Отправить сообщение в диалог"""
        payload = {
            "model": f"gpt://{self.project_id}/{upstream_model_id(model)}/latest",
            "input": [{"role": "user", "content": message}],
            "instructions": "Ты — полезный ассистент.",
            "background": True,
            "conversation": conv_id,
        }
        resp = yandex_request(
            self.session.post,
            "POST",
            f"{self.base_url}/responses",
            base_url=self.base_url,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def list_models(self):
        """Получить список доступных моделей"""
        resp = yandex_request(
            self.session.get, "GET", f"{self.base_url}/models", base_url=self.base_url
        )
        resp.raise_for_status()
        return resp.json()


# Пример использования
if __name__ == "__main__":
    import os

    sdk = AliceSDK(os.environ["YANDEX_API_KEY"], os.environ["YANDEX_PROJECT_ID"])

    # Создать диалог
    conv = sdk.create_conversation()
    print(f"Создан диалог: {conv['id']}")

    # Отправить сообщение
    resp = sdk.send_message(conv["id"], "Привет!", model="logos-runtime")
    print(f"Ответ: {resp}")
