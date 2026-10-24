from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_pruned_agent_runner_stays_deleted():
    assert not (ROOT / "agent_runner.py").exists()
