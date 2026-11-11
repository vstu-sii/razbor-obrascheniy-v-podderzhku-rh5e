"""Validate published saved evidence without HTTP, keys or regeneration."""
from pathlib import Path
import json
import sys
from decimal import Decimal
from collections import Counter

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from jsonschema import Draft202012Validator
from ml.contracts.validation import SCHEMA
from ml.models.baseline import retrieve


def main():
    kb=[json.loads(l) for l in (ROOT/'ml/data/cascade/kb.jsonl').read_text(encoding='utf-8').splitlines()]
    total_generations=0
    for folder in sorted((Path(__file__).parent/'evidence').iterdir()):
        if not folder.is_dir() or not (folder/'results.jsonl').exists():continue
        rows=[json.loads(l) for l in (folder/'results.jsonl').read_text(encoding='utf-8').splitlines()]
        config=json.loads((folder/'config.json').read_text(encoding='utf-8'))
        assert len(rows)==len(config['cases'])
        assert len({r['case_id'] for r in rows})==len(rows)
        modes=Counter();charges={}
        for p in folder.glob('billing-*.json'):
            for entry in json.loads(p.read_text(encoding='utf-8'))['changes']:
                charges[entry['attempt_id']]=entry['billed_micro_usd']
        cost=0;generations=0;matches=0
        for row in rows:
            req=row['input'];card=row['proposal']['card']
            assert Draft202012Validator(SCHEMA).is_valid(card)
            ids=[s['source_id'] for s in retrieve(req['ticket_text'],req['history'],req['kb_version'],kb)]
            assert ids==row['initial_ids']
            modes[card['mode']]+=1;matches+=card['mode'] in row['expected']['expected_modes']
            calls=[a for a in row['attempts'] if not (a.get('diagnostics') or {}).get('not_sent',False)]
            if calls:
                proof=json.loads((folder/('langfuse-'+row['case_id']+'.json')).read_text(encoding='utf-8'))
                observations=[o for o in proof['observations'] if o['type']=='GENERATION']
                assert len(observations)==len(calls)
                assert all(o.get('input') and (o.get('output') or o.get('level')=='ERROR') for o in observations)
                generations+=len(observations)
            for a in row['attempts']:
                attempt=(a.get('diagnostics') or {}).get('attempt_id')
                if a['provider']=='sol':
                    assert attempt in charges,'Missing official billing reconciliation'
                    cost+=charges[attempt]
        total_generations+=generations
        print(json.dumps(dict(series=folder.name,jobs=len(rows),modes=modes,expected_mode_matches=matches,
            confirmed_cost_rub=str(Decimal(cost)*89/1000000),observations=generations,
            quality_claim='mode match is not semantic accuracy'),ensure_ascii=True))
    assert total_generations==34
    print('Verified 34 saved generations, including ERROR; new model calls: 0')


if __name__=='__main__':main()
