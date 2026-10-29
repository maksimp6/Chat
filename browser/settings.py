"""Per-session browser settings, user-agent presets and request header constants."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger("logos.browser.settings")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Mobile Safari/537.36"
)
_BRANDS = '"Chromium";v="153", "Google Chrome";v="153", "Not.A/Brand";v="99"'
# Sent only with DEFAULT_USER_AGENT; the mobile preset has its own hints.
CLIENT_HINTS = [
    ("sec-ch-ua", _BRANDS),
    ("sec-ch-ua-mobile", "?0"),
    ("sec-ch-ua-platform", '"Windows"'),
]
MOBILE_CLIENT_HINTS = [
    ("sec-ch-ua", _BRANDS),
    ("sec-ch-ua-mobile", "?1"),
    ("sec-ch-ua-platform", '"Android"'),
]
DOCUMENT_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)
DEFAULT_ACCEPT_LANGUAGE = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
ENGINE_MODES = ("auto", "http", "chrome")
DIALOG_MODES = ("accept", "dismiss")

DEFAULT_VIEWPORT = (1366, 768)
MOBILE_VIEWPORT = (412, 915)


@dataclass
class ChromeOptions:
    """What a Chrome launch and its page setup need from the session settings."""

    user_agent: str | None
    accept_language: str
    extra_headers: list[tuple[str, str]]
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    mobile: bool
    javascript: bool
    load_images: bool
    dialogs: str
    dialog_prompt_text: str | None
    proxy: str
    ignore_tls_errors: bool


@dataclass
class SessionSettings:
    """Settings of one browser session; every capability is on by default."""

    user_agent: str = ""
    accept_language: str = DEFAULT_ACCEPT_LANGUAGE
    extra_headers: list[tuple[str, str]] = field(default_factory=list)
    default_engine: str = "http"
    viewport_width: int = DEFAULT_VIEWPORT[0]
    viewport_height: int = DEFAULT_VIEWPORT[1]
    device_scale_factor: float = 1.0
    mobile: bool = False
    javascript: bool = True
    load_images: bool = True
    dialogs: str = "accept"
    dialog_prompt_text: str | None = None
    proxy: str = ""
    ignore_tls_errors: bool = False

    def http_user_agent(self) -> str:
        """The User-Agent the http engine sends; "" means the desktop preset."""
        return self.user_agent or DEFAULT_USER_AGENT

    def client_hints(self) -> list[tuple[str, str]]:
        """Client hints matching the desktop or mobile preset; none for a custom UA."""
        agent = self.http_user_agent()
        if agent == DEFAULT_USER_AGENT:
            return list(CLIENT_HINTS)
        if agent == MOBILE_USER_AGENT:
            return list(MOBILE_CLIENT_HINTS)
        return []

    def to_chrome(self) -> ChromeOptions:
        """Chrome options; an empty user agent keeps Chrome's own (HeadlessChrome→Chrome)."""
        return ChromeOptions(
            user_agent=self.user_agent or None,
            accept_language=self.accept_language,
            extra_headers=list(self.extra_headers),
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            device_scale_factor=self.device_scale_factor,
            mobile=self.mobile,
            javascript=self.javascript,
            load_images=self.load_images,
            dialogs=self.dialogs,
            dialog_prompt_text=self.dialog_prompt_text,
            proxy=self.proxy,
            ignore_tls_errors=self.ignore_tls_errors,
        )

    def apply_user_agent(self, value: str, *, explicit: Iterable[str] = ()) -> None:
        """Apply a user_agent setting: "desktop", "mobile", "" or a literal string.

        "mobile" also switches mobile emulation on and the viewport to 412×915,
        except for the fields named in ``explicit`` (mobile, viewport_width,
        viewport_height) that the caller sets in the same call; applying those
        explicit values after this call has the same effect.
        """
        given = set(explicit)
        text = (value or "").strip()
        preset = text.lower()
        if preset == "desktop":
            self.user_agent = DEFAULT_USER_AGENT
        elif preset == "mobile":
            self.user_agent = MOBILE_USER_AGENT
            if "mobile" not in given:
                self.mobile = True
            if "viewport_width" not in given:
                self.viewport_width = MOBILE_VIEWPORT[0]
            if "viewport_height" not in given:
                self.viewport_height = MOBILE_VIEWPORT[1]
        elif not text:
            self.user_agent = ""
        else:
            self.user_agent = value

    def describe(self) -> list[str]:
        """Russian "key: value" lines; header values masked, proxy password hidden."""
        from browser.redact import display_url, mask_header_value

        if not self.user_agent:
            agent = "стандартный (Chrome 153, Windows; в chrome — собственный Chrome)"
        elif self.user_agent == DEFAULT_USER_AGENT:
            agent = "desktop (Chrome 153, Windows)"
        elif self.user_agent == MOBILE_USER_AGENT:
            agent = "mobile (Chrome 153, Android)"
        else:
            agent = self.user_agent
        if self.extra_headers:
            headers = "; ".join(
                f"{name}: {mask_header_value(name, value)}"
                for name, value in self.extra_headers
            )
        else:
            headers = "нет"
        scale = f"{self.device_scale_factor:g}".replace(".", ",")
        dialogs = "принимать" if self.dialogs == "accept" else "отклонять"
        prompt = (
            f"«{self.dialog_prompt_text}»"
            if self.dialog_prompt_text is not None
            else "текст по умолчанию страницы"
        )
        return [
            f"User-Agent: {agent}",
            f"Accept-Language: {self.accept_language}",
            f"Доп. заголовки: {headers}",
            f"Движок по умолчанию: {self.default_engine}",
            f"Окно chrome: {self.viewport_width}×{self.viewport_height}, "
            f"масштаб {scale}, мобильный режим: {'да' if self.mobile else 'нет'}",
            f"JavaScript в chrome: {'да' if self.javascript else 'нет'}",
            f"Изображения в chrome: {'да' if self.load_images else 'нет'}",
            f"Диалоги страницы: {dialogs}; ответ на prompt: {prompt}",
            f"Прокси: {display_url(self.proxy) if self.proxy else 'из окружения'}",
            "Проверка TLS-сертификатов: "
            + ("выключена" if self.ignore_tls_errors else "включена"),
        ]


__all__ = [
    "CLIENT_HINTS",
    "ChromeOptions",
    "DEFAULT_ACCEPT_LANGUAGE",
    "DEFAULT_USER_AGENT",
    "DIALOG_MODES",
    "DOCUMENT_ACCEPT",
    "ENGINE_MODES",
    "MOBILE_CLIENT_HINTS",
    "MOBILE_USER_AGENT",
    "SessionSettings",
]
