import unittest
from printer_service.android_shim import AndroidShim, Backend, Keyguard, SimulatedAndroid, Evidence


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

    def test_png_corrupt_crc_and_trailing_data(self):
        from printer_service.android_shim import validate_png

        png = AndroidShim().screenshot()
        self.assertTrue(validate_png(png))
        self.assertFalse(validate_png(png[:-1]))
        self.assertFalse(validate_png(png + b"junk"))
        changed = bytearray(png)
        changed[33] ^= 1
        self.assertFalse(validate_png(bytes(changed)))

    def test_real_transport_cannot_dispatch_after_spoofed_unlocked(self):
        class Spoof:
            called = False

            def observe(self):
                return Evidence(Backend.DEVICE, Keyguard.UNLOCKED, True, "spoof")

            def tap(self, x, y):
                self.called = True
                return True

        transport = Spoof()
        shim = AndroidShim(Backend.DEVICE, transport)
        self.assertFalse(shim.tap(1, 1))
        self.assertFalse(transport.called)

    def test_spoofed_device_swipe_never_dispatched(self):
        class Spoof:
            called = False

            def observe(self):
                return Evidence(Backend.DEVICE, Keyguard.UNLOCKED, True, "spoof")

            def swipe(self, *args):
                self.called = True
                return True

        transport = Spoof()
        self.assertFalse(AndroidShim(Backend.DEVICE, transport).swipe(1, 1, 2, 2))
        self.assertFalse(transport.called)

    def test_toctou_between_observation_and_dispatch(self):
        class Flipping(SimulatedAndroid):
            def observe(self):
                evidence = super().observe()
                self.keyguard = Keyguard.LOCKED
                return evidence

        transport = Flipping(keyguard=Keyguard.UNLOCKED)
        shim = AndroidShim(transport=transport)
        self.assertFalse(shim.tap(1, 1))
        self.assertFalse(shim.swipe(1, 1, 2, 2))
        self.assertEqual(transport.events, [])

    def test_png_invalid_compressed_payload_with_valid_crc(self):
        import struct
        import zlib
        from printer_service.android_shim import validate_png

        png = bytearray(AndroidShim().screenshot())
        offset = 8 + 25
        length = struct.unpack_from(">I", png, offset)[0]
        self.assertEqual(png[offset + 4 : offset + 8], b"IDAT")
        png[offset + 8] ^= 0xFF
        crc = zlib.crc32(png[offset + 4 : offset + 8 + length])
        struct.pack_into(">I", png, offset + 8 + length, crc)
        self.assertFalse(validate_png(bytes(png)))

    def test_png_trailing_zlib_stream_rejected(self):
        import struct
        import zlib
        from printer_service.android_shim import validate_png

        png = AndroidShim().screenshot()
        offset = 33
        length = struct.unpack_from(">I", png, offset)[0]
        payload = png[offset + 8 : offset + 8 + length] + b"trailing"
        chunk = struct.pack(">I", len(payload)) + b"IDAT" + payload
        chunk += struct.pack(">I", zlib.crc32(b"IDAT" + payload))
        modified = png[:offset] + chunk + png[offset + 12 + length :]
        self.assertFalse(validate_png(modified))

    def test_png_invalid_filter_rejected(self):
        import struct
        import zlib
        from printer_service.android_shim import validate_png

        png = AndroidShim().screenshot()
        offset = 33
        length = struct.unpack_from(">I", png, offset)[0]
        original = zlib.decompress(png[offset + 8 : offset + 8 + length])
        payload = zlib.compress(bytes([5]) + original[1:])
        chunk = struct.pack(">I", len(payload)) + b"IDAT" + payload
        chunk += struct.pack(">I", zlib.crc32(b"IDAT" + payload))
        self.assertFalse(validate_png(png[:offset] + chunk + png[offset + 12 + length :]))

    def test_screenshot_png_fixture(self):
        self.assertTrue(AndroidShim().screenshot().startswith(bytes.fromhex("89504e47")))


if __name__ == "__main__":
    unittest.main()
