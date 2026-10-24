"""Correctness and schema tests for the printer benchmark runner."""

import pytest

from scripts.benchmark_printer import benchmarks, measure, run


def test_benchmark_operations_are_correct():
    for name, fn in benchmarks().items():
        result = fn()
        assert result is not None, name


def test_measure_schema():
    result = measure(lambda: 1 + 1, iterations=20, rounds=3)
    assert result["iterations"] == 20
    assert result["rounds"] == 3
    assert 0 <= result["min_us"] <= result["median_us"] <= result["p95_us"]


@pytest.mark.parametrize("iterations,rounds", [(0, 3), (1, 1), (-1, 7)])
def test_invalid_measurement_parameters(iterations, rounds):
    with pytest.raises(ValueError):
        measure(lambda: None, iterations=iterations, rounds=rounds)


def test_machine_readable_report():
    result = run(iterations=5, rounds=3)
    assert result["schema"] == "logos-printer-benchmark-v1"
    assert result["mode"] == "mock-no-network"
    assert set(result["results"]) == {
        "frame_decode",
        "rfc1918_validation",
        "route_lookup",
        "telemetry_merge",
    }
