"""Daily admission and pre-network reservations under the queue's shared DB lock."""
from datetime import datetime, timezone
import time
from uuid import uuid4
from fastapi import HTTPException
from backend.limits.settings import configured


class DailyLimits:
    def __init__(self):
        self.requests = configured('USER_REQUESTS_PER_DAY', 100)
        self.tokens = configured('USER_TOKENS_PER_DAY', 1000000)
        self.calls = configured('USER_MODEL_CALLS_PER_DAY', 100)
        self.anomaly = configured('ANOMALY_TOKENS_PER_MINUTE', 200000)

    def day(self, now=None):
        return datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).date().isoformat()

    def row(self, c, actor, now=None):
        day = self.day(now)
        c.execute('INSERT INTO daily_usage(actor_ref,usage_day) VALUES (?,?) ON CONFLICT(actor_ref,usage_day) DO NOTHING', (actor,day))
        return day, c.execute('SELECT * FROM daily_usage WHERE actor_ref=? AND usage_day=?', (actor,day)).fetchone()

    def admit(self, c, actor):
        day, usage = self.row(c, actor)
        if usage['requests'] >= self.requests:
            # Caller commits a denial event separately; no accepted event/job is recorded.
            raise HTTPException(429, 'Daily request limit reached', headers={'Retry-After':'3600'})
        c.execute('UPDATE daily_usage SET requests=requests+1 WHERE actor_ref=? AND usage_day=?', (actor,day))

    def reserve(self, c, actor, amount, now):
        day, usage = self.row(c, actor, now)
        if usage['model_calls'] >= self.calls:
            event(c, 'degradation', 'daily_call_limit', now)
            return False
        if usage['reserved_tokens'] + amount > self.tokens:
            event(c, 'degradation', 'daily_token_limit', now)
            return False
        c.execute('UPDATE daily_usage SET reserved_tokens=reserved_tokens+?,model_calls=model_calls+1 WHERE actor_ref=? AND usage_day=?', (amount,actor,day))
        return True


def event(c, kind, reason, now=None):
    c.execute('INSERT INTO security_event VALUES (?,?,?,?)', (str(uuid4()),kind,reason,time.time() if now is None else now))
    from backend.observability import emit
    emit('policy', outcome=kind, reason=reason)
