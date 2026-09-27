import pytest


def test_extract_timing_metrics_empty_trace():
    from compute_resources import extract_timing_metrics

    trace = {"timings": {}, "responses": [], "tool_calls": []}
    metrics = extract_timing_metrics(trace)

    assert metrics["total_duration_ms"] == 0.0
    assert metrics["api_time_ms"] == 0.0
    assert metrics["tool_time_ms"] == 0.0
    assert metrics["processing_time_ms"] == 0.0


def test_extract_timing_metrics_with_values():
    from compute_resources import extract_timing_metrics

    trace = {
        "timings": {"total_duration_ms": 1000.0},
        "responses": [
            {"timing_ms": 300.0},
            {"timing_ms": 200.0},
        ],
        "tool_calls": [
            {"timing_ms": 150.0},
        ],
    }
    metrics = extract_timing_metrics(trace)

    assert metrics["total_duration_ms"] == 1000.0
    assert metrics["api_time_ms"] == 500.0
    assert metrics["tool_time_ms"] == 150.0
    assert metrics["processing_time_ms"] == 500.0


def test_calculate_compute_cost():
    from compute_resources import calculate_compute_cost

    metrics = {
        "processing_time_ms": 60000,
        "tool_time_ms": 30000,
        "api_time_ms": 10000,
        "api_calls": [],
    }
    cost = calculate_compute_cost(metrics)

    assert cost["cpu_time_ms"] == 60000
    assert cost["tool_time_ms"] == 30000
    assert cost["total_compute_cost"] > 0


def test_build_compute_billing_item():
    from compute_resources import build_compute_billing_item

    trace = {
        "timings": {"total_duration_ms": 1000.0},
        "responses": [{"timing_ms": 300.0}],
        "tool_calls": [{"timing_ms": 150.0}],
    }
    item = build_compute_billing_item(trace)

    assert item["type"] == "compute"
    assert item["cost_status"] == "calculated"
    assert item["total_cost"] > 0
    assert "cpu_cost" in item
    assert "tool_cost" in item
    assert "api_call_cost" in item


def test_aggregate_compute_costs_no_items():
    from compute_resources import aggregate_compute_costs

    result = aggregate_compute_costs([])

    assert result["total_cost"] == 0.0
    assert result["cost_status"] == "calculated"
    assert result["cpu_cost"] == 0.0
    assert result["tool_cost"] == 0.0


def test_aggregate_compute_costs_multiple_items():
    from compute_resources import aggregate_compute_costs

    items = [
        {
            "type": "compute",
            "cost_status": "calculated",
            "cpu_cost": 0.1,
            "tool_cost": 0.05,
            "api_call_cost": 0.02,
            "total_compute_cost": 0.17,
            "total_cost": 0.17,
            "cpu_time_ms": 1000,
            "tool_time_ms": 500,
            "api_time_ms": 200,
        },
        {
            "type": "compute",
            "cost_status": "calculated",
            "cpu_cost": 0.1,
            "tool_cost": 0.05,
            "api_call_cost": 0.02,
            "total_compute_cost": 0.17,
            "total_cost": 0.17,
            "cpu_time_ms": 1000,
            "tool_time_ms": 500,
            "api_time_ms": 200,
        },
    ]
    result = aggregate_compute_costs(items)

    assert result["cost_status"] == "calculated"
    assert result["cpu_cost"] == pytest.approx(0.2)
    assert result["tool_cost"] == pytest.approx(0.1)
    assert result["total_cost"] == pytest.approx(0.34)
    assert result["cpu_time_ms"] == 2000
    assert result["tool_time_ms"] == 1000


def test_aggregate_compute_costs_with_context():
    from compute_resources import aggregate_compute_costs

    items = [
        {
            "type": "compute",
            "cost_status": "calculated",
            "cpu_cost": 0.1,
            "tool_cost": 0.05,
            "api_call_cost": 0.02,
            "total_compute_cost": 0.17,
            "total_cost": 0.17,
            "cpu_time_ms": 1000,
            "tool_time_ms": 500,
            "api_time_ms": 200,
        },
    ]
    context = {
        "owner_id": "user-1",
        "trace_id": "trace-123",
        "invocation_id": "inv-456",
    }
    result = aggregate_compute_costs(items, context)

    assert result["owner_id"] == "user-1"
    assert result["trace_id"] == "trace-123"
    assert result["invocation_id"] == "inv-456"


def test_compute_resources_integrated_with_billing():
    from billing import aggregate_billing

    items = [
        {
            "type": "ai",
            "cost_status": "calculated",
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_input_tokens": 10,
            "total_tokens": 150,
            "input_cost": 0.1,
            "output_cost": 0.05,
            "cached_input_cost": 0.01,
            "audio_cost": 0.0,
            "cache_savings": 0.001,
            "total_cost": 0.161,
        },
        {
            "type": "compute",
            "cost_status": "calculated",
            "cpu_cost": 0.1,
            "tool_cost": 0.05,
            "api_call_cost": 0.02,
            "total_compute_cost": 0.17,
            "total_cost": 0.17,
            "cpu_time_ms": 1000,
            "tool_time_ms": 500,
            "api_time_ms": 200,
        },
    ]
    result = aggregate_billing(items)

    assert result["total_cost"] == pytest.approx(0.331, rel=1e-2)
    assert result["input_cost"] == pytest.approx(0.1)
    assert result["output_cost"] == pytest.approx(0.05)
    assert result["tool_cost"] == pytest.approx(0.05)
    assert result["compute_cost"] == pytest.approx(0.17)
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50


def test_compute_zero_cost_item():
    from compute_resources import build_compute_billing_item

    trace = {
        "timings": {"total_duration_ms": 0.0},
        "responses": [],
        "tool_calls": [],
    }
    item = build_compute_billing_item(trace)

    assert item["type"] == "compute"
    assert item["total_cost"] == 0.0
    assert item["cpu_cost"] == 0.0
    assert item["tool_cost"] == 0.0
