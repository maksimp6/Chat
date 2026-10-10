import unittest
from alice_mouse_frame_policy import Frame, FrameAwareMouse


class FrameAwareTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000_000_000
        self.m = FrameAwareMouse(now_ns=lambda: self.now)
        self.assertTrue(self.m.heartbeat(self.m.token, 1))

    def advance(self, ms):
        self.now += ms * 1_000_000

    def frame(self, seq, frame_id="same"):
        return self.m.observe(Frame(seq, self.now, frame_id))

    def test_unchanged_frames_are_new_observations(self):
        self.assertTrue(self.frame(1))
        self.advance(500)
        self.assertTrue(self.frame(2))
        self.assertEqual(self.m.frame.frame_id, "same")

    def test_slow_capture_does_not_renew_heartbeat(self):
        self.frame(1)
        self.assertTrue(self.m.command("down"))
        self.advance(1200)
        self.assertTrue(self.frame(2))
        self.m.tick()
        self.assertIsNone(self.m.button)
        self.assertFalse(self.m.command("move"))

    def test_disconnect_releases(self):
        self.frame(1)
        self.m.command("down")
        self.m.disconnect()
        self.assertEqual(self.m.events[-1], ("up", 1, "safety"))

    def test_drag_across_frames(self):
        self.frame(1)
        self.assertTrue(self.m.command("down"))
        for i in range(2, 5):
            self.advance(400)
            self.assertTrue(self.m.heartbeat(self.m.token, i))
            self.assertTrue(self.frame(i))
            self.assertTrue(self.m.command("move"))
        self.assertTrue(self.m.command("up"))
        self.assertEqual([x[0] for x in self.m.events], ["down", "move", "move", "move", "up"])

    def test_stale_frame_rejected(self):
        self.frame(1)
        self.advance(2100)
        self.assertTrue(self.m.heartbeat(self.m.token, 2))
        self.assertFalse(self.m.command("down"))

    def test_heartbeat_auth_and_replay(self):
        self.assertFalse(self.m.heartbeat("wrong", 2))
        self.assertFalse(self.m.heartbeat(self.m.token, 1))
        self.assertTrue(self.m.heartbeat(self.m.token, 2))

    def test_hard_hold_limit_even_with_heartbeat(self):
        self.frame(1)
        self.m.command("down")
        for i in range(2, 14):
            self.advance(450)
            self.m.heartbeat(self.m.token, i)
            self.frame(i)
            self.m.tick()
        self.assertIsNone(self.m.button)
        self.assertIn(("up", 1, "safety"), self.m.events)

    def test_frame_does_not_authorize(self):
        self.frame(1)
        self.advance(1100)
        self.frame(2)
        self.assertFalse(self.m.command("down"))

    def test_explicit_up_after_frame_stale(self):
        self.frame(1)
        self.m.command("down")
        self.advance(2100)
        self.assertFalse(self.m.command("up"))
        self.assertEqual(self.m.events[-1], ("up", 1, "safety"))

    def test_future_or_replayed_frames_rejected(self):
        self.assertFalse(self.m.observe(Frame(1, self.now + 1, "x")))
        self.assertTrue(self.frame(1))
        self.assertFalse(self.frame(1))


if __name__ == "__main__":
    unittest.main()
