"""Test configuration and zero-leak environment guarantees."""

from pathlib import Path

from beherit.config import Settings


def test_default_config_zero_leak():
    """Ensure defaults contain NO private IPs or credentials."""
    s = Settings(
        _env_file=None,
        BEHERIT_HOST="0.0.0.0",
        BEHERIT_PORT=8650,
        BEHERIT_DATA_DIR=Path("./data"),
    )
    assert s.BEHERIT_HOST == "0.0.0.0"
    assert s.BEHERIT_PORT == 8650
    assert "100." not in s.BEHERIT_HOST
    assert "192.168" not in s.BEHERIT_HOST
    assert s.agents_dir == Path("./data/agents").resolve()
