from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .backtest import BacktestResult
from .config import AppConfig
from .metrics import calculate_metrics


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def write_report(result: BacktestResult, config: AppConfig, output: str | Path) -> dict[str, Any]:
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    metrics = calculate_metrics(result, config.analytics)
    payload = _safe_json({"metrics": metrics, "config": config.to_dict()})

    (directory / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    result.equity.to_csv(directory / "equity.csv", index=False)
    result.trade_frame().to_csv(directory / "trades.csv", index=False)
    result.features.to_csv(directory / "features.csv", index=False)

    status = "POSITIVE" if metrics["total_return"] > 0 else "NEGATIVE"
    markdown = f"""# СПУТНИК // OOO Backtest

> {status} · Research simulation · Not financial advice

| Metric | Result |
|---|---:|
| Total return | {_pct(metrics['total_return'])} |
| Benchmark return | {_pct(metrics['benchmark_return'])} |
| CAGR | {_pct(metrics['cagr'])} |
| Sharpe | {metrics['sharpe']:.2f} |
| Maximum drawdown | {_pct(metrics['max_drawdown'])} |
| Trades | {metrics['trades']} |
| Win rate | {_pct(metrics['win_rate'])} |
| Profit factor | {metrics['profit_factor']:.2f} |
| Exposure | {_pct(metrics['exposure'])} |
| Fees | A${metrics['fees']:,.2f} |

## Integrity

Signals execute at the next open. Stops are gap-aware. When the daily high and
low touch both stop and target, the stop is assumed first. Demo data, if used,
has no investment meaning.
"""
    (directory / "report.md").write_text(markdown, encoding="utf-8")
    (directory / "index.html").write_text(_dashboard_html(payload), encoding="utf-8")
    return payload


def _dashboard_html(payload: dict[str, Any] | None = None) -> str:
    embedded = json.dumps(payload or {"metrics": {}})
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>СПУТНИК // OOO Intelligence</title>
<style>
:root{{--bg:#050807;--panel:#0b1210;--line:#173328;--text:#f4f7f5;--muted:#7e9b8e;
--green:#21d07a;--red:#ff3f67;--amber:#f2a93b;--cyan:#36c7e8;}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 80% 0,#10251d 0,
var(--bg) 38%);color:var(--text);font:14px ui-monospace,SFMono-Regular,Menlo,monospace;min-height:100vh}}
main{{max-width:1050px;margin:auto;padding:28px 18px}} header{{display:flex;justify-content:space-between;
align-items:end;border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:18px}}
h1{{font-size:clamp(24px,5vw,52px);margin:0;letter-spacing:.08em}} .tag{{color:var(--green)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}}
.card{{background:linear-gradient(145deg,#0d1713,#08100d);border:1px solid var(--line);
padding:16px;min-height:112px;box-shadow:0 10px 35px #0008}} .label{{color:var(--muted);
font-size:11px;letter-spacing:.16em;text-transform:uppercase}} .value{{font-size:28px;margin-top:16px}}
.green{{color:var(--green)}} .red{{color:var(--red)}} .amber{{color:var(--amber)}} .cyan{{color:var(--cyan)}}
section{{margin-top:18px;background:#08100dcc;border:1px solid var(--line);padding:18px}}
table{{width:100%;border-collapse:collapse}} td{{padding:9px;border-bottom:1px solid #14271f}}
td:last-child{{text-align:right;color:var(--cyan)}} footer{{color:var(--muted);margin-top:18px;font-size:11px}}
</style>
</head>
<body><main>
<header><div><div class="tag">🛰 SYSTEM ONLINE</div><h1>СПУТНИК</h1></div><div>OOO // MACRO CONFLUENCE</div></header>
<div id="cards" class="grid"></div>
<section><div class="label">Mission readout</div><table id="detail"></table></section>
<footer>RESEARCH SIMULATION · NEXT-OPEN EXECUTION · COSTS INCLUDED · NO BROKER CONNECTION</footer>
</main>
<script>
let state={embedded};
const pct=v=>(100*(v||0)).toFixed(2)+'%';
const money=v=>'A$'+Number(v||0).toLocaleString(undefined,{{maximumFractionDigits:0}});
function draw(d){{const m=d.metrics||{{}};const ret=m.total_return||0;
const cards=[['TOTAL RETURN',pct(ret),ret>=0?'green':'red'],['MAX DRAWDOWN',pct(m.max_drawdown),'red'],
['SHARPE',Number(m.sharpe||0).toFixed(2),'cyan'],['TRADES',m.trades||0,'amber']];
document.querySelector('#cards').innerHTML=cards.map(x=>`<div class="card"><div class="label">${{x[0]}}</div><div class="value ${{x[2]}}">${{x[1]}}</div></div>`).join('');
const rows=[['Final equity',money(m.final_equity)],['Win rate',pct(m.win_rate)],
['Profit factor',m.profit_factor==null?'∞':Number(m.profit_factor).toFixed(2)],
['Exposure',pct(m.exposure)],['Benchmark',pct(m.benchmark_return)],['Fees',money(m.fees)]];
document.querySelector('#detail').innerHTML=rows.map(x=>`<tr><td>${{x[0]}}</td><td>${{x[1]}}</td></tr>`).join('')}}
draw(state); fetch('/api/latest').then(r=>r.ok?r.json():null).then(d=>{{if(d)draw(d)}}).catch(()=>{{}});
</script></body></html>"""


def dashboard_html() -> str:
    return _dashboard_html()
