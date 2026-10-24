"""Generation no longer depends on repository permission variables."""
import pytest
from agent_runtime.generation_gate import generation_allowed


@pytest.mark.parametrize("environ", [{}, {"LOGOS_GENERATION_ALLOWED": "false"}, {"LOGOS_GENERATION_ALLOWED": "true"}])
def test_generation_has_no_permission_gate(environ):
    assert generation_allowed(environ)
