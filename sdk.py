"""
sdk.py - Минимальный SDK для работы с Yandex AI Studio
"""
import requests
import json

class AliceSDK:
    """SDK для работы с Yandex AI Studio API"""
    
    def __init__(self, api_key, project_id, base_url="https://ai.api.cloud.yandex.net/v1"):
        self.api_key = api_key
        self.project_id = project_id
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
            "x-yc-project-id": project_id,
            "OpenAI-Project": project_id,
        })
    
    def create_conversation(self):
        """Создать новый диалог"""
        resp = self.session.post(f"{self.base_url}/conversations", json={"metadata": {}, "items": []})
        resp.raise_for_status()
        return resp.json()
    
    def send_message(self, conv_id, message, model="aliceai-llm"):
        """Отправить сообщение в диалог"""
        payload = {
            "model": f"gpt://{self.project_id}/{model}/latest",
            "input": [{"role": "user", "content": message}],
            "instructions": "Ты — полезный ассистент.",
            "background": True,
            "conversation": conv_id,
        }
        resp = self.session.post(f"{self.base_url}/responses", json=payload)
        resp.raise_for_status()
        return resp.json()
    
    def list_models(self):
        """Получить список доступных моделей"""
        resp = self.session.get(f"{self.base_url}/models")
        resp.raise_for_status()
        return resp.json()

# Пример использования
if __name__ == "__main__":
    from config import Config
    sdk = AliceSDK(Config.API_KEY, Config.PROJECT_ID)
    
    # Создать диалог
    conv = sdk.create_conversation()
    print(f"Создан диалог: {conv['id']}")
    
    # Отправить сообщение
    resp = sdk.send_message(conv['id'], "Привет!", model="aliceai-llm")
    print(f"Ответ: {resp}")
