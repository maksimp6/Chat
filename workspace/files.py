"""Yandex AI Studio File & Vector Store Manager Mixin"""

import copy
import json
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import MaxRetryError
from urllib3.util import Retry

api_logger = logging.getLogger("yandex_api_debug")

from yandex_client_modules.errors import YandexClientError
from yandex_client_modules.request_mixin import (
    _resolve_global_provider_credential,
    provider_request_headers,
)

# The client waits up to 5 minutes for an upload, so the server must wait at least as long.
UPLOAD_TIMEOUT_SECONDS = 300


class YandexFileManagerMixin:
    """Миксин для Files и Vector Stores. Подключается к YandexResponsesClient."""

    def _fm_request(self, method, url, timeout=30, **kwargs):
        """Запрос с retry + логирование через _log_request/_log_response.
        Для multipart убирает Content-Type из заголовков сессии."""
        credential = _resolve_global_provider_credential(self)
        headers = dict(kwargs.pop("headers", None) or {})
        headers.update(provider_request_headers(credential))
        if kwargs.get("files"):
            # None removes the session's JSON Content-Type for this request only,
            # so requests can write the multipart boundary.
            headers["Content-Type"] = None
        kwargs["headers"] = headers

        # Only a read is repeated. A timed-out upload may already have been stored, so repeating it
        # stores a second copy, and a repeated delete reports the file as not found.
        retries = 3 if method.upper() in {"GET", "HEAD", "OPTIONS"} else 0
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            other=retries,
            redirect=0,
            status=0,
            allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
            backoff_factor=0.25,
            respect_retry_after_header=False,
        )
        # Do not mount an adapter on the shared Responses session: that could retry
        # a concurrent model request. Keep its configuration, but own these pools.
        session = copy.copy(self.session)
        session.headers = self.session.headers.copy()
        session.cookies = self.session.cookies.copy()
        adapter = HTTPAdapter(max_retries=0)
        session.adapters = {"https://": adapter, "http://": adapter}
        try:
            while True:
                self._log_request(method, url, **kwargs)
                try:
                    resp = session.request(method, url, timeout=timeout, **kwargs)
                    self._log_response(resp)
                    return resp
                except requests.exceptions.RequestException as exc:
                    message = (
                        "Network timeout"
                        if isinstance(exc, requests.exceptions.Timeout)
                        else "Network error"
                    )
                    api_logger.warning(
                        "[FILES/VS] %s attempt %s/%s on %s %s",
                        message,
                        len(retry.history) + 1,
                        retries + 1,
                        method,
                        url,
                    )
                    try:
                        # The adapter never retries internally: each attempt,
                        # including failures while consuming a body, stays logged.
                        retry = retry.increment(method=method, url=url, error=exc)
                    except MaxRetryError:
                        # A provider that does not answer in time is a gateway timeout (504), so the
                        # client can say that the file may already have been saved.
                        status = (
                            504
                            if isinstance(exc, requests.exceptions.Timeout)
                            else None
                        )
                        raise YandexClientError(message, status_code=status) from exc
                    retry.sleep()
        finally:
            session.close()

    def _fm_handle_error(self, resp, context):
        """Обработка HTTP-ошибок с логированием."""
        status = resp.status_code
        try:
            err_body = resp.json()
            err_msg = (
                err_body.get("error", {}).get("message", str(err_body))
                if isinstance(err_body, dict)
                else str(err_body)
            )
        except Exception:
            err_msg = resp.text[:200] if resp.text else "unknown"
        api_logger.error(f"[FILES/VS] {context} failed: HTTP {status}: {err_msg}")
        # The status travels with the error, so the route answers with it instead of a generic 500.
        # A provider-side auth failure or server error is an upstream failure, reported as 502.
        if status == 404:
            raise YandexClientError(f"{context}: Not found (404)", status_code=404)
        if status == 413:
            raise YandexClientError(
                f"{context}: File too large (max 128 MB) (413)", status_code=413
            )
        if status in (401, 403):
            raise YandexClientError(
                f"{context}: Auth failed ({status})", status_code=502
            )
        if status == 429:
            raise YandexClientError(
                f"{context}: Rate limit (429): {err_msg}", status_code=429
            )
        raise YandexClientError(
            f"{context} failed (HTTP {status}): {err_msg}",
            status_code=502 if status >= 500 else 400,
        )

    # === Files API ===

    def list_files(self, limit=100, after=None):
        api_logger.info(f"[FILES] List (limit={limit}, after={after})")
        params = {"limit": limit}
        if after:
            params["after"] = after
        resp = self._fm_request("GET", f"{self.base_url}/files", params=params)
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "List files")
        return resp.json()

    def upload_file(self, file_obj, filename, purpose="assistants", expires_after=None):
        api_logger.info(f"[FILES] Upload: {filename} (purpose={purpose})")
        files = {"file": (filename, file_obj)}
        data = {"purpose": purpose}
        if expires_after:
            data["expires_after"] = json.dumps(expires_after)
        resp = self._fm_request(
            "POST",
            f"{self.base_url}/files",
            files=files,
            data=data,
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Upload file")
        result = resp.json()
        api_logger.info(
            f"[FILES] Uploaded: id={result.get('id')}, bytes={result.get('bytes')}"
        )
        return result

    def delete_file(self, file_id):
        api_logger.info(f"[FILES] Delete: {file_id}")
        resp = self._fm_request("DELETE", f"{self.base_url}/files/{file_id}")
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Delete file")
        return resp.json()

    def retrieve_file(self, file_id):
        api_logger.info(f"[FILES] Retrieve: {file_id}")
        resp = self._fm_request("GET", f"{self.base_url}/files/{file_id}")
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Retrieve file")
        return resp.json()

    def download_file(self, file_id):
        api_logger.info(f"[FILES] Download: {file_id}")
        resp = self._fm_request("GET", f"{self.base_url}/files/{file_id}/content")
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Download file")
        api_logger.info(f"[FILES] Downloaded: {len(resp.content)} bytes")
        return resp.content

    # === Vector Stores API ===

    def list_vector_stores(self, limit=100):
        api_logger.info(f"[VS] List (limit={limit})")
        resp = self._fm_request(
            "GET", f"{self.base_url}/vector_stores", params={"limit": limit}
        )
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "List VS")
        return resp.json()

    def create_vector_store(
        self, name, file_ids=None, chunking_strategy=None, expires_after=None
    ):
        api_logger.info(f"[VS] Create: {name}")
        payload = {"name": name}
        if file_ids:
            payload["file_ids"] = file_ids
        if chunking_strategy:
            payload["chunking_strategy"] = chunking_strategy
        if expires_after:
            payload["expires_after"] = expires_after
        resp = self._fm_request("POST", f"{self.base_url}/vector_stores", json=payload)
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Create VS")
        result = resp.json()
        api_logger.info(f"[VS] Created: id={result.get('id')}")
        return result

    def get_vector_store(self, vs_id):
        api_logger.info(f"[VS] Get: {vs_id}")
        resp = self._fm_request("GET", f"{self.base_url}/vector_stores/{vs_id}")
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Get VS")
        return resp.json()

    def delete_vector_store(self, vs_id):
        api_logger.info(f"[VS] Delete: {vs_id}")
        resp = self._fm_request("DELETE", f"{self.base_url}/vector_stores/{vs_id}")
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Delete VS")
        return resp.json()

    def list_vs_files(self, vs_id, limit=100, filter_status=None):
        api_logger.info(f"[VS] List files in {vs_id} (filter={filter_status})")
        params = {"limit": limit}
        if filter_status:
            params["filter"] = filter_status
        resp = self._fm_request(
            "GET", f"{self.base_url}/vector_stores/{vs_id}/files", params=params
        )
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "List VS files")
        return resp.json()

    def add_file_to_vs(self, vs_id, file_id, chunking_strategy=None):
        api_logger.info(f"[VS] Add file {file_id} to {vs_id}")
        payload = {"file_id": file_id}
        if chunking_strategy:
            payload["chunking_strategy"] = chunking_strategy
        resp = self._fm_request(
            "POST", f"{self.base_url}/vector_stores/{vs_id}/files", json=payload
        )
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Add file to VS")
        return resp.json()

    def remove_file_from_vs(self, vs_id, file_id):
        api_logger.info(f"[VS] Remove file {file_id} from {vs_id}")
        resp = self._fm_request(
            "DELETE", f"{self.base_url}/vector_stores/{vs_id}/files/{file_id}"
        )
        if resp.status_code >= 400:
            self._fm_handle_error(resp, "Remove file from VS")
        return resp.json()
