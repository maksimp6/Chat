"""Ports of the tool_providers unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tool_providers._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    Config: Any  # core.config.Config
    EMBEDDING_MODELS: Any  # core.config.EMBEDDING_MODELS
    EmbeddingError: Any  # yandex_client_modules.embeddings.EmbeddingError
    KristallError: Any  # kristall.KristallError
    SSHRuntimeError: Any  # remote_runtime.ssh.SSHRuntimeError
    YandexClientError: Any  # yandex_client_modules.errors.YandexClientError
    YandexResponsesClient: Any  # yandex_client_modules.client.YandexResponsesClient
    assert_operation_allowed: (
        Any  # remote_runtime.ssh_settings.assert_operation_allowed
    )
    build_runtime: Any  # remote_runtime.ssh_settings.build_runtime
    embed_texts: Any  # yandex_client_modules.embeddings.embed_texts
    enqueue_local_tool_job: Any  # agent_runtime.local_gateway.enqueue_local_tool_job
    external_tool_sources: Any  # composition.tool_sources.SOURCES
    knowledge_vector_store_ids: (
        Any  # yandex_client_modules.tool_config.knowledge_vector_store_ids
    )
    memory_file_path: Any  # storage.records.memory_file_path
    runtime_for_operation: Any  # remote_runtime.ssh_settings.runtime_for_operation
    wait_for_local_tool_job: Any  # agent_runtime.local_gateway.wait_for_local_tool_job


_binding: Binding[Host] = Binding("tool_providers")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = [
    "Host",
    "HostNotBound",
    "assert_operation_allowed",
    "bind",
    "bound",
    "build_runtime",
    "current",
    "embed_texts",
    "enqueue_local_tool_job",
    "knowledge_vector_store_ids",
    "memory_file_path",
    "provide",
    "runtime_for_operation",
    "wait_for_local_tool_job",
]


def assert_operation_allowed(*args: Any, **kwargs: Any) -> Any:
    return current().assert_operation_allowed(*args, **kwargs)


def build_runtime(*args: Any, **kwargs: Any) -> Any:
    return current().build_runtime(*args, **kwargs)


def embed_texts(*args: Any, **kwargs: Any) -> Any:
    return current().embed_texts(*args, **kwargs)


def enqueue_local_tool_job(*args: Any, **kwargs: Any) -> Any:
    return current().enqueue_local_tool_job(*args, **kwargs)


def knowledge_vector_store_ids(*args: Any, **kwargs: Any) -> Any:
    return current().knowledge_vector_store_ids(*args, **kwargs)


def memory_file_path(*args: Any, **kwargs: Any) -> Any:
    return current().memory_file_path(*args, **kwargs)


def runtime_for_operation(*args: Any, **kwargs: Any) -> Any:
    return current().runtime_for_operation(*args, **kwargs)


def wait_for_local_tool_job(*args: Any, **kwargs: Any) -> Any:
    return current().wait_for_local_tool_job(*args, **kwargs)
