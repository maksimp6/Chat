"""Polling helper for background Yandex Responses API tasks."""

# Short answers keep a 3-second poll so they arrive promptly. Only long background jobs
# poll less often; a 30-second cap from the start delayed finished answers by up to 30 s.
SHORT_WAIT_SECONDS = 120


def poll_delay_cap(elapsed):
    return 3 if elapsed < SHORT_WAIT_SECONDS else 15
