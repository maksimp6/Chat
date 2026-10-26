from browser import capabilities, orchestration, pipeline


def test_secret_inputs_are_redacted_from_inspection_results():
    value = capabilities.sanitize_browser_value(
        {"inputs": [{"name": "access_token", "type": "hidden", "value": "SECRET-VALUE"}]}
    )
    assert "SECRET-VALUE" not in str(value)


def test_navigate_counts_as_a_write_for_session_scheduling():
    assert "navigate" in orchestration.WRITE_ACTIONS


def test_truncated_observation_is_kept_as_a_truncated_snapshot():
    snapshot = pipeline._extract_snapshot(
        {"truncated": True, "preview": "partial", "original_chars": 99999}
    )
    assert snapshot is not None
    assert snapshot.truncated is True
