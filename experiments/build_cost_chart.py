"""Plot frozen latency and per-request cost evidence; no network calls."""
import json
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]


def main():
    rows = [json.loads(line) for line in (ROOT / 'evidence/results.jsonl').read_text(encoding='utf-8').splitlines()]
    config = json.loads((ROOT / 'evidence/experiment-config.json').read_text(encoding='utf-8'))
    budget = json.loads((ROOT / 'evidence/budget-summary.json').read_text(encoding='utf-8'))
    rate = Decimal(str(budget['rub_per_usd_assumption']))
    cases = config['case_ids']
    fig, (latency, cost) = plt.subplots(1, 2, figsize=(17, 6), layout='constrained')
    providers = [('sol', 'Sol', '#2459a6'), ('mistral_free', 'Ministral 14B', '#159978')]
    for provider, label, color in providers:
        selected = [next(r for r in rows if r['provider'] == provider and r['case_id'] == cid) for cid in cases]
        times = [r['latency_ms'] / 1000 for r in selected]
        rub = [float(Decimal(str(r['cost_upper_usd'])) * rate) for r in selected]
        latency.plot(range(len(cases)), times, color=color, alpha=.45)
        cost.plot(range(len(cases)), rub, color=color, alpha=.45)
        for x, (r, seconds, amount) in enumerate(zip(selected, times, rub)):
            latency_marker = '^' if r['http_status'] != 200 else ('o' if r['gate_passed'] else 'x')
            source = r['billing_source']
            cost_marker = 'o' if source == 'account_usage_matched_completion_and_tokens' else ('^' if source == 'reservation_unresolved' else 's')
            latency.scatter(x, seconds, marker=latency_marker, color=color, s=55)
            cost.scatter(x, amount, marker=cost_marker, color=color, s=55)
            cost.annotate(f'{amount:.3f}', (x, amount), xytext=(0, 7), textcoords='offset points', ha='center', fontsize=8)
    for ax in (latency, cost):
        ax.set_xticks(range(len(cases)), cases, rotation=55, ha='right')
        ax.grid(axis='y', alpha=.2)
    latency.set(title='Client latency: all 10 attempts per model', ylabel='Seconds')
    cost.set(title=f'Cost per attempt at {rate} RUB/USD', ylabel='RUB (confirmed / estimate / reserve)')
    cost.set_ylim(bottom=0)
    model_handles = [Line2D([], [], color=c, label=n) for _, n, c in providers]
    latency.legend(handles=model_handles + [Line2D([], [], color='gray', linestyle='', marker=m, label=l) for m, l in [('o', 'Gate passed'), ('x', 'Schema rejected'), ('^', 'Transport / timeout')]], fontsize=8)
    cost.legend(handles=model_handles + [Line2D([], [], color='gray', linestyle='', marker=m, label=l) for m, l in [('o', 'Confirmed bill'), ('s', 'Tariff estimate (Free mode)'), ('^', 'Unresolved reserve')]], fontsize=8)
    fig.suptitle('Frozen synthetic experiment: latency and cost for each request', fontsize=15)
    fig.supxlabel('Availability checks and earlier costs excluded. Reserve and tariff estimate are not confirmed charges.', fontsize=10)
    fig.savefig(ROOT / 'evidence/latency-cost.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    main()
