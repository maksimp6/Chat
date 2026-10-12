import unittest
from alice_pro.android_shim import AndroidShim, Backend, Keyguard, SimulatedAndroid, Evidence


class AndroidShimTests(unittest.TestCase):
    def test_simulated_never_real_verified(self):
        fake = SimulatedAndroid(keyguard=Keyguard.UNLOCKED)
        self.assertFalse(AndroidShim(Backend.SIMULATED, fake).observe().verified)

    def test_locked_rejects_tap_swipe(self):
        shim = AndroidShim()
        self.assertFalse(shim.tap(1, 1))
        self.assertFalse(shim.swipe(1, 1, 2, 2))

    def test_unlocked_simulated_actions(self):
        shim = AndroidShim(transport=SimulatedAndroid(keyguard=Keyguard.UNLOCKED))
        self.assertTrue(shim.tap(1, 1))
        self.assertTrue(shim.swipe(1, 1, 2, 2))
        self.assertEqual(shim.trace[-1]["outcome"], "submitted")

    def test_bounds(self):
        shim = AndroidShim(transport=SimulatedAndroid(keyguard=Keyguard.UNLOCKED))
        self.assertFalse(shim.tap(-1, 2))
        self.assertFalse(shim.swipe(0, 0, 999, 1))

    def test_fault_injection(self):
        fake = SimulatedAndroid(
            keyguard=Keyguard.UNLOCKED, faults={"observation_unavailable", "capture_failure"}
        )
        shim = AndroidShim(transport=fake)
        self.assertFalse(shim.tap(1, 1))
        self.assertEqual(shim.observe().keyguard, Keyguard.UNKNOWN)
        with self.assertRaises(RuntimeError):
            shim.screenshot()

    def test_unconfigured_real_backends_fail_closed(self):
        for backend in (Backend.EMULATOR, Backend.DEVICE):
            shim = AndroidShim(backend)
            self.assertEqual(shim.observe().keyguard, Keyguard.UNKNOWN)
            self.assertFalse(shim.tap(1, 1))
            with self.assertRaises(RuntimeError):
                shim.screenshot()

    def test_mismatched_transport(self):
        class Spoof:
            def observe(self):
                return Evidence(Backend.DEVICE, Keyguard.UNLOCKED, True, "fake")

        with self.assertRaises(RuntimeError):
            AndroidShim(Backend.SIMULATED, Spoof()).observe()

    def test_backend_spoof_cannot_claim_verified(self):
        class Spoof:
            def observe(self):
                return Evidence(Backend.DEVICE, Keyguard.UNLOCKED, True, "trusted")

        self.assertFalse(AndroidShim(Backend.DEVICE, Spoof()).observe().verified)

    def test_keyguard_changes_before_dispatch(self):
        class Flipping(SimulatedAndroid):
            def observe(self):
                self.keyguard = Keyguard.LOCKED
                return super().observe()

        shim = AndroidShim(transport=Flipping(keyguard=Keyguard.UNLOCKED))
        self.assertFalse(shim.tap(1, 1))

    def test_parallel_simulated_taps(self):
        from concurrent.futures import ThreadPoolExecutor

        shim = AndroidShim(transport=SimulatedAndroid(keyguard=Keyguard.UNLOCKED))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: shim.tap(1, 1), range(40)))
        self.assertEqual(sum(results), 40)
        self.assertEqual(len(shim.transport.events), 40)

    def test_screenshot_png_fixture(self):
        self.assertTrue(AndroidShim().screenshot().startswith(bytes.fromhex("89504e47")))


if __name__ == "__main__":
    unittest.main()
