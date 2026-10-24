"""Multiprocess fail-closed regression: independent shims share an attempted dispatch counter."""

import multiprocessing
import unittest

from alice_pro.android_shim import AndroidShim, Backend, Evidence, Keyguard


def _worker(start, counter, result):
    class SpoofedDevice:
        def observe(self):
            return Evidence(Backend.DEVICE, Keyguard.UNLOCKED, True, "spoof")

        def tap(self, x, y):
            with counter.get_lock():
                counter.value += 1
            return True

        def swipe(self, x1, y1, x2, y2):
            with counter.get_lock():
                counter.value += 1
            return True

    shim = AndroidShim(Backend.DEVICE, SpoofedDevice())
    if not start.wait(timeout=5):
        result.put("timeout")
        return
    result.put((shim.tap(1, 1), shim.swipe(1, 1, 2, 2), shim.observe().verified))


class AndroidShimMultiprocessTests(unittest.TestCase):
    def test_spoofed_device_never_dispatches_across_processes(self):
        ctx = multiprocessing.get_context("spawn")
        start = ctx.Event()
        counter = ctx.Value("i", 0)
        result = ctx.Queue()
        processes = [ctx.Process(target=_worker, args=(start, counter, result)) for _ in range(4)]
        for process in processes:
            process.start()
        try:
            start.set()
            outcomes = [result.get(timeout=15) for _ in processes]
            self.assertEqual(outcomes, [(False, False, False)] * 4)
            self.assertEqual(counter.value, 0)
        finally:
            for process in processes:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        self.assertTrue(all(process.exitcode == 0 for process in processes))
