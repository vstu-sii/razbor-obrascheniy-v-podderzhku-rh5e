"""Score saved or new model results without confusing fallbacks with quality."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from jsonschema import Draft202012Validator

TASK = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((Path(__file__).with_name('response.schema.json')).read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA)

def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]

def provenance_gate(card, request_id, version, retrieved):
    if not isinstance(card, dict) or not VALIDATOR.is_valid(card):
        return False, 'schema'
    if card['request_id'] != request_id:
        return False, 'request_id'
    allowed = {(item['source_id'], item['version']): item for item in retrieved
               if item['status'] == 'approved' and item['version'] == version}
    for evidence in card['evidence']:
        source = allowed.get((evidence['source_id'], evidence['version']))
        if source is None or any(evidence[key] != source[key] for key in ('title', 'excerpt')):
            return False, 'evidence_provenance'
    return True, 'structure_and_provenance_only'
