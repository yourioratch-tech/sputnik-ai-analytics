from pathlib import Path

from sputnik.agent_knowledge import build_agent_context


def test_agent_context_loads_version_controlled_pine_and_strategy():
    context = build_agent_context(Path("configs"))
    assert "SPUTNIK SOURCE CONTRACT" in context
    assert "barstate.isconfirmed" in context
    assert "longScore" in context
    assert "BACKTEST STRATEGY" in context
    assert len(context) <= 12_000
