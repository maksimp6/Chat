"""Yandex AI Studio File & Vector Store Manager Mixin"""
import json
import time
import requests
import logging

api_logger = logging.getLogger("yandex_api_debug")

from yandex_client import YandexClientError, _sanitize_for_log

class YandexFileManagerMixin:
    """Миксин для Files и Vector Stores. Подключается к YandexResponsesClient."""

    def _fm_request(self, method, url, **kwargs):
        """Запрос с retry + логирование через _log_request/_log_response.
        Для multipart убирает Content-Type из заголовков сессии."""
        restore_ct = None
        if kwargs.get('files') and 'Content-Type' in self.session.headers:
            restore_ct = self.session.headers.pop('Content-Type')

        self._log_request(method, url, **kwargs)

        delays = [0.0, 0.5, 1.5, 4.5]
        last_exc = None
        for attempt, delay in enumerate(delays):
            if delay > 0:
                time.sleep(delay)
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
                self._log_response(resp)
                if restore_ct is not None:
                    self.session.headers['Content-Type'] = restore_ct
                return resp
            except requests.exceptions.Timeout:
                last_exc = YandexClientError("Network timeout")
                api_logger.warning(f"[FILES/VS] Timeout attempt {attempt+1}/4 on {method} {url}")
            except requests.exceptions.RequestException as e:
                last_exc = YandexClientError(f"Network error: {e}")
                api_logger.warning(f"[FILES/VS] Network error attempt {attempt+1}/4: {e}")

        if restore_ct is not None:
            self.session.headers['Content-Type'] = restore_ct
        raise last_exc or YandexClientError("Unknown network error")

    def _fm_handle_error(self, resp, context):
        """Обработка HTTP-ошибок с логированием."""
        status = resp.status_code
        try:
            err_body = resp.json()
            err_msg = err_body.get("error", {}).get("message", str(err_body)) if isinstance(err_body, dict) else str(err_body)
        except Exception:
            err_msg = resp.text[:200] if resp.text else "unknown"
        api_logger.error(f"[FILES/VS] {context} failed: HTTP {status}: {err_msg}")
        if status == 404:
            raise YandexClientError(f"{context}: Not found (404)")
        if status == 413:
            raise YandexClientError(f"{context}: File too large (max 128 MB) (413)")
        if status in (401, 403):
            raise YandexClientError(f"{context}: Auth failed ({status})")
        if status == 429:
            raise YandexClientError(f"{context}: Rate limit (429): {err_msg}")
        raise YandexClientError(f"{context} failed (HTTP {status}): {err_msg}")

    # === Files API ===

    def list_files(self, limit=100, after=None):
        api_logger.info(f"[FILES] List (limit={limit}, after={after})")
        params = {"limit": limit}
        if after: params["after"] = after
        resp = self._fm_request("GET", f"{self.base_url}/files", params=params)
        if resp.status_code >= 400: self._fm_handle_error(resp, "List files")
        return resp.json()

    def upload_file(self, file_obj, filename, purpose="assistants", expires_after=None):
        api_logger.info(f"[FILES] Upload: {filename} (purpose={purpose})")
        files = {"file": (filename, file_obj)}
        data = {"purpose": purpose}
        if expires_after:
            data["expires_after"] = json.dumps(expires_after)
        resp = self._fm_request("POST", f"{self.base_url}/files", files=files, data=data)
        if resp.status_code >= 400: self._fm_handle_error(resp, "Upload file")
        result = resp.json()
        api_logger.info(f"[FILES] Uploaded: id={result.get('id')}, bytes={result.get('bytes')}")
        return result

    def delete_file(self, file_id):
        api_logger.info(f"[FILES] Delete: {file_id}")
        resp = self._fm_request("DELETE", f"{self.base_url}/files/{file_id}")
        if resp.status_code >= 400: self._fm_handle_error(resp, "Delete file")
        return resp.json()

    def retrieve_file(self, file_id):
        api_logger.info(f"[FILES] Retrieve: {file_id}")
        resp = self._fm_request("GET", f"{self.base_url}/files/{file_id}")
        if resp.status_code >= 400: self._fm_handle_error(resp, "Retrieve file")
        return resp.json()

    def download_file(self, file_id):
        api_logger.info(f"[FILES] Download: {file_id}")
        resp = self._fm_request("GET", f"{self.base_url}/files/{file_id}/content")
        if resp.status_code >= 400: self._fm_handle_error(resp, "Download file")
        api_logger.info(f"[FILES] Downloaded: {len(resp.content)} bytes")
        return resp.content

    # === Vector Stores API ===

    def list_vector_stores(self, limit=100):
        api_logger.info(f"[VS] List (limit={limit})")
        resp = self._fm_request("GET", f"{self.base_url}/vector_stores", params={"limit": limit})
        if resp.status_code >= 400: self._fm_handle_error(resp, "List VS")
        return resp.json()

    def create_vector_store(self, name, file_ids=None, chunking_strategy=None, expires_after=None):
        api_logger.info(f"[VS] Create: {name}")
        payload = {"name": name}
        if file_ids: payload["file_ids"] = file_ids
        if chunking_strategy: payload["chunking_strategy"] = chunking_strategy
        if expires_after: payload["expires_after"] = expires_after
        resp = self._fm_request("POST", f"{self.base_url}/vector_stores", json=payload)
        if resp.status_code >= 400: self._fm_handle_error(resp, "Create VS")
        result = resp.json()
        api_logger.info(f"[VS] Created: id={result.get('id')}")
        return result

    def get_vector_store(self, vs_id):
        api_logger.info(f"[VS] Get: {vs_id}")
        resp = self._fm_request("GET", f"{self.base_url}/vector_stores/{vs_id}")
        if resp.status_code >= 400: self._fm_handle_error(resp, "Get VS")
        return resp.json()

    def delete_vector_store(self, vs_id):
        api_logger.info(f"[VS] Delete: {vs_id}")
        resp = self._fm_request("DELETE", f"{self.base_url}/vector_stores/{vs_id}")
        if resp.status_code >= 400: self._fm_handle_error(resp, "Delete VS")
        return resp.json()

    def list_vs_files(self, vs_id, limit=100, filter_status=None):
        api_logger.info(f"[VS] List files in {vs_id} (filter={filter_status})")
        params = {"limit": limit}
        if filter_status: params["filter"] = filter_status
        resp = self._fm_request("GET", f"{self.base_url}/vector_stores/{vs_id}/files", params=params)
        if resp.status_code >= 400: self._fm_handle_error(resp, "List VS files")
        return resp.json()

    def add_file_to_vs(self, vs_id, file_id, chunking_strategy=None):
        api_logger.info(f"[VS] Add file {file_id} to {vs_id}")
        payload = {"file_id": file_id}
        if chunking_strategy: payload["chunking_strategy"] = chunking_strategy
        resp = self._fm_request("POST", f"{self.base_url}/vector_stores/{vs_id}/files", json=payload)
        if resp.status_code >= 400: self._fm_handle_error(resp, "Add file to VS")
        return resp.json()

    def remove_file_from_vs(self, vs_id, file_id):
        api_logger.info(f"[VS] Remove file {file_id} from {vs_id}")
        resp = self._fm_request("DELETE", f"{self.base_url}/vector_stores/{vs_id}/files/{file_id}")
        if resp.status_code >= 400: self._fm_handle_error(resp, "Remove file from VS")
        return resp.json()
