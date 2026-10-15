"""Standalone reference BM25 diagnostic, separate from controlled-context LLM run."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from evaluate import DATASET, EVIDENCE, read_jsonl

def tokenize(text):
    return re.findall(r'[а-яёa-z0-9]+', text.lower())

def retrieve(case, kb):
    data = case['input']
    docs = [item for item in kb if item['status'] == 'approved' and item['version'] == data['kb_version']]
    if not docs:
        return []
    query = set(tokenize(data['ticket_text'] + ' ' + ' '.join(item['text'] for item in data['history'])))
    bags = [Counter(tokenize(doc['title'] + ' ' + doc['excerpt'])) for doc in docs]
    average = sum(sum(bag.values()) for bag in bags) / len(bags)
    frequency = Counter(term for bag in bags for term in bag)
    scores = []
    for doc, bag in zip(docs, bags):
        score = sum(math.log(1 + (len(docs) - frequency[t] + .5) / (frequency[t] + .5)) * bag[t] * 2.5 /
                    (bag[t] + 1.5 * (.25 + .75 * sum(bag.values()) / average)) for t in sorted(query & bag.keys()))
        if score > 0:
            scores.append((score, doc['source_id']))
    return [source for _, source in sorted(scores, key=lambda item: (-item[0], item[1]))[:3]]

def run():
    cases = [case for case in read_jsonl(DATASET / 'cases.jsonl') if case['id'] == case['group_id'] and case['uc'] != 'UC-04']
    kb = read_jsonl(DATASET / 'kb.jsonl')
    rows = []
    for case in cases:
        found = retrieve(case, kb)
        expected = set(case['expected']['evidence_ids'])
        rows.append({'case_id': case['id'], 'retrieved': found, 'gold': sorted(expected),
                     'recall_at_3': len(set(found) & expected) / len(expected) if expected else None})
    positives = [row for row in rows if row['recall_at_3'] is not None]
    report = {'scope': 'offline_reference_bm25_not_deployed_retriever', 'cases': len(rows),
              'positive_cases': len(positives),
              'mean_recall_at_3': sum(row['recall_at_3'] for row in positives) / len(positives) if positives else None,
              'no_gold_evidence_cases_with_hits': sum(not row['gold'] and bool(row['retrieved']) for row in rows),
              'source_sha256': hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest(),
              'api_calls': 0, 'rows': rows}
    (EVIDENCE / 'retrieval-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report

if __name__ == '__main__':
    result = run()
    print(json.dumps({key: value for key, value in result.items() if key != 'rows'}))
