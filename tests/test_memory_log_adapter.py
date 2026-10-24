import json
import unittest

from memory_log_adapter import MemoryLogAdapter, MAX_EVENT_BYTES


class Writer:
    def __init__(self, fail=False):
        self.events = {}
        self.fail = fail

    def append(self, stream, event_id, payload):
        if self.fail:
            raise OSError("storage unavailable")
        self.events.setdefault((stream, event_id), payload)


class MemoryLogAdapterTests(unittest.TestCase):
    def test_append_redacts_bearer(self):
        writer = Writer()
        event = MemoryLogAdapter(writer).append(
            trace_id="trace-1",
            source="agent",
            level="info",
            message="Authorization: Bearer abc123",
            event_id="event-1",
        )
        self.assertNotIn("abc123", next(iter(writer.events.values())).decode())
        self.assertEqual(event.event_id, "event-1")

    def test_idempotent_event_id(self):
        writer = Writer()
        adapter = MemoryLogAdapter(writer)
        for _ in range(2):
            adapter.append(trace_id="t", source="tool", level="info", message="ok", event_id="same")
        self.assertEqual(len(writer.events), 1)

    def test_failure_propagates(self):
        with self.assertRaises(OSError):
            MemoryLogAdapter(Writer(fail=True)).append(
                trace_id="t", source="api", level="error", message="failure"
            )

    def test_oversized_event_rejected_before_write(self):
        writer = Writer()
        with self.assertRaises(ValueError):
            MemoryLogAdapter(writer).append(
                trace_id="t", source="api", level="info", message="x" * MAX_EVENT_BYTES
            )
        self.assertEqual(writer.events, {})

    def test_requires_trace_and_source(self):
        with self.assertRaises(ValueError):
            MemoryLogAdapter(Writer()).append(
                trace_id="", source="api", level="info", message="test"
            )


if __name__ == "__main__":
    unittest.main()
