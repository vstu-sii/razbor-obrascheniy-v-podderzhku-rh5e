"""Read final provider receipts and match them to a reserved attempt."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import os

import httpx
from ml.cascade.providers import MODELS, provider_tls_context


def canonical_id(value):
    return value.removeprefix('chatcmpl-') if isinstance(value, str) and value else None


def matching_receipt(attempt, diagnostics, receipts):
    identifiers = {canonical_id(diagnostics.get(k)) for k in ('completion_id', 'provider_request_id')}
    identifiers.discard(None)
    matches = []
    for receipt in receipts:
        if receipt.get('model') != MODELS['sol'] or receipt.get('status') not in ('success', 'aborted'):
            continue
        receipt_ids = {canonical_id(receipt.get(k)) for k in ('id', 'completion_id', 'request_id')}
        if identifiers:
            matched = bool(identifiers.intersection(receipt_ids))
        else:
            usage = diagnostics.get('usage') or {}
            try:
                if not isinstance(usage, dict) or not isinstance(receipt.get('created_at'), str):
                    continue
                occurred = datetime.fromisoformat(receipt['created_at'].replace('Z', '+00:00')).timestamp()
                complete_usage = all(type(usage.get(k)) is int and usage[k] >= 0
                                     for k in ('prompt_tokens', 'completion_tokens'))
                matched = (complete_usage and type(receipt.get('tokens_in')) is int
                           and type(receipt.get('tokens_out')) is int
                           and receipt.get('tokens_in') == usage.get('prompt_tokens')
                           and receipt.get('tokens_out') == usage.get('completion_tokens')
                           and abs(occurred - attempt['reserved_at']) <= 90)
            except (ValueError, TypeError, KeyError):
                matched = False
        if matched:
            matches.append(receipt)
    return matches[0] if len(matches) == 1 else None


def receipt_cost(receipt):
    try:
        amount = Decimal(str(receipt['cost_usd']))
        if not amount.is_finite() or amount < 0:
            return None
        return int((amount * 1000000).to_integral_value(rounding=ROUND_CEILING))
    except (KeyError, InvalidOperation, ValueError):
        return None


def fetch_receipts():
    key = os.environ.get('APIMIRA_API_KEY')
    if not key:
        return []
    with httpx.Client(timeout=5, trust_env=False, verify=provider_tls_context()) as client:
        response = client.get('https://apimira.com/v1/account/usage',
                              headers={'Authorization': 'Bearer ' + key})
        response.raise_for_status()
        data = response.json().get('data', [])
    return data if isinstance(data, list) and all(isinstance(r, dict) for r in data) else []
