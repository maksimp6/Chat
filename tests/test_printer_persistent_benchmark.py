"""Tests for request-only hardware benchmark statistics and safety gate."""

import pytest

from scripts.benchmark_printer_persistent import percentile, run_hardware


def test_p95_nearest_rank():
    assert percentile(list(range(1, 101)), 0.95) == 95


def test_percentile_rejects_empty_samples():
    with pytest.raises(ValueError):
        percentile([], 0.95)


@pytest.mark.parametrize("requests,warmup", [(0, 0), (10, -1), (10, 101)])
def test_invalid_samples_fail_before_network(requests, warmup):
    with pytest.raises(ValueError):
        run_hardware("192.168.31.12", requests=requests, warmup=warmup)
