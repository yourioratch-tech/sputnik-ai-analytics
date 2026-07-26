from __future__ import annotations

from pathlib import Path


def _matching_lines(path: Path, terms: tuple[str, ...], limit: int) -> str:
    if not path.is_file():
        return f"[missing: {path.name}]"
    selected: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        lowered = line.lower()
        if any(term in lowered for term in terms):
            selected.append(f"L{number}: {line}")
        if len(selected) >= limit:
            break
    return "\n".join(selected)


def build_agent_context(config_dir: Path) -> str:
    """Build a compact prompt from fixed, version-controlled Sputnik sources."""
    root = config_dir.resolve().parent
    stream = root / "tradingview" / "sputnik_bar_stream.pine"
    overlay = root / "tradingview" / "sputnik_smc_yasx_orb_vwap_st_combined.pine"
    alerts = config_dir / "tradingview-alerts.yml"
    strategy = config_dir / "ooo_daily.yml"
    mac_policy = config_dir / "mac-agent-policy.yml"
    sections = [
        "SPUTNIK SOURCE CONTRACT\n"
        "TradingView completed-bar webhooks are observations, not exchange truth. "
        "Setup alerts are attention-only. Require freshness, timeframe, price, confirmation, "
        "invalidation, range/VWAP/retest and cross-asset evidence. If missing, say WAIT. "
        "Never place orders or treat model/news output as a signal.",
        "ALERT UNIVERSE\n" + alerts.read_text(encoding="utf-8") if alerts.is_file() else "",
        "BACKTEST STRATEGY\n" + strategy.read_text(encoding="utf-8") if strategy.is_file() else "",
        "MAC AGENT POLICY\n" + mac_policy.read_text(encoding="utf-8") if mac_policy.is_file() else "",
        "COMPLETED-BAR PINE\n" + stream.read_text(encoding="utf-8") if stream.is_file() else "",
        "SIGNAL SCORE PINE EXCERPT\n"
        + _matching_lines(
            overlay,
            ("longscore", "shortscore", "longsignal", "shortsignal", "strictneed"),
            70,
        ),
        "ALERT/SIGNAL PINE EXCERPT\n"
        + _matching_lines(
            overlay,
            (
                "alert",
                "signal",
                "vwap",
                "orb",
                "risk",
                "longscore",
                "shortscore",
                "entrytext",
                "stoptext",
                "targettext",
            ),
            45,
        ),
    ]
    return "\n\n".join(section for section in sections if section)[:12_000]
