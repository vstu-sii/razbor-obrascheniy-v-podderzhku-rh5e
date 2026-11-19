"""Bounded, opt-in cache of validated ordinary cards; never a source of authority."""
import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict


class AnswerCache:
    def __init__(self, ttl_seconds=300, max_entries=128, clock=time.monotonic):
        if not 0 < ttl_seconds <= 300 or not 0 < max_entries <= 128:
            raise ValueError('invalid_cache_bounds')
        self.ttl, self.capacity, self.clock = ttl_seconds, max_entries, clock
        self.entries, self.lock = OrderedDict(), threading.Lock()

    @staticmethod
    def key(scope, provider, request, context, schema, policy):
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError('trusted_cache_scope_required')
        # Scope MUST come from authenticated application identity, never the prompt.
        data = dict(scope=scope, provider=provider, policy=policy, schema=schema,
                    input={k: v for k, v in request.items() if k != 'request_id'},
                    context={k: v for k, v in context.items() if k != 'input_hash'})
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                        separators=(',', ':')).encode()).hexdigest()

    def get(self, key):
        with self.lock:
            row = self.entries.pop(key, None)
            if row is None or row[0] <= self.clock():
                return None
            self.entries[key] = row
            return copy.deepcopy(row[1])

    def put(self, key, result):
        if (result.get('automatic_resolution') is not True
                or result['final_card']['mode'] != 'answer'
                or result['final_card']['urgency'] not in ('low', 'medium', 'unassessed')):
            return
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = (self.clock() + self.ttl, copy.deepcopy(result))
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
