import unittest
from unittest.mock import Mock, patch

from yandex_response_poller import wait_for_response


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class TraceStub:
    def __init__(self):
        self.responses = []
        self.events = []

    def add_response(self, *args, **kwargs):
        self.responses.append((args, kwargs))

    def add_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class YandexResponsePollerTests(unittest.TestCase):
    @patch("yandex_response_poller.time.sleep")
    def test_polls_until_completed_and_records_changed_snapshots(self, sleep):
        session = Mock()
        session.get.side_effect = [
            FakeResponse({"id": "r1", "status": "in_progress"}),
            FakeResponse({"id": "r1", "status": "completed", "output_text": "done"}),
        ]
        trace = TraceStub()

        result = wait_for_response(
            task_id="r1",
            responses_url="https://example.test/responses",
            session=session,
            log_request=Mock(),
            log_response=lambda response: response,
            error_cls=RuntimeError,
            execution_trace=trace,
            trace_step=2,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(len(trace.responses), 2)
        self.assertTrue(any(event[0][0] == "api_poll_completed" for event in trace.events))
        self.assertTrue(sleep.called)

    @patch("yandex_response_poller.time.sleep")
    def test_failed_response_raises_client_error(self, sleep):
        session = Mock()
        session.get.return_value = FakeResponse(
            {
                "id": "r1",
                "status": "failed",
                "error": {"message": "bad request"},
            }
        )

        with self.assertRaisesRegex(RuntimeError, "bad request"):
            wait_for_response(
                task_id="r1",
                responses_url="https://example.test/responses",
                session=session,
                log_request=Mock(),
                log_response=lambda response: response,
                error_cls=RuntimeError,
            )

    @patch("yandex_response_poller.time.time", side_effect=[0, 10])
    def test_timeout_raises(self, _time):
        session = Mock()
        session.get.return_value = FakeResponse({"id": "r1", "status": "in_progress"})

        with patch("yandex_response_poller.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "Timeout"):
                wait_for_response(
                    task_id="r1",
                    responses_url="https://example.test/responses",
                    session=session,
                    log_request=Mock(),
                    log_response=lambda response: response,
                    error_cls=RuntimeError,
                    timeout=1,
                )


if __name__ == "__main__":
    unittest.main()
