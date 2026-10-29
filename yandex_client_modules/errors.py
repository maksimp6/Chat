"""Errors raised by the Yandex client."""


class YandexClientError(Exception):
    # True only when the provider refused the POST or never took it, so no response
    # exists for that request; any later failure (a poll, a broken stream) keeps False.
    request_not_accepted = False

    def __init__(self, message, status_code=None, user_text=None):
        super().__init__(message)
        self.status_code = status_code
        # Russian text for the person when the failure has an action they can take;
        # the message stays the technical cause for logs and traces.
        self.user_text = user_text
