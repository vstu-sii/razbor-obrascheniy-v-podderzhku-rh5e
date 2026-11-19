"""AI-013 cascade on the current LAB04 queue and per-identity daily quotas."""
import hashlib
import json
import os
import time
from decimal import Decimal, ROUND_FLOOR
from uuid import uuid4
from fastapi import HTTPException
from backend.events import Queue as SingleQueue
from backend.limits.daily import event as policy_event
from ml.cascade.providers import (
    CASCADE_GRACE_SECONDS, CASCADE_STAGE_TIMEOUTS, MAX_INPUT_TOKENS, MAX_OUTPUT,
    MISTRAL_MAX_INPUT_TOKENS, MODELS, SOL_RESERVATION_MICRO,
)
from ml.cascade.context import make_context, checked_context


class Queue(SingleQueue):
    def __init__(self, connect, manual_factory=None, mode='extractive', cascade_enabled=False, cascade_kb=None):
        super().__init__(connect, manual_factory, mode=mode)
        self.cascade_enabled = cascade_enabled
        if cascade_enabled:
            self.deadline = sum(CASCADE_STAGE_TIMEOUTS.values()) + CASCADE_GRACE_SECONDS
        self.mistral_rpm = int(os.environ.get('MISTRAL_REQUESTS_PER_MINUTE', '2'))
        if not 0 <= self.mistral_rpm <= 10000:
            raise ValueError('Invalid Mistral requests-per-minute setting')
        self.mistral_running = int(os.environ.get('MISTRAL_MAX_RUNNING', '1'))
        if not 1 <= self.mistral_running <= 32:
            raise ValueError('Invalid Mistral concurrency setting')
        self.cascade_kb = cascade_kb if cascade_kb is not None else []
        rub = Decimal(os.environ.get('CASCADE_BUDGET_RUB', '10'))
        if not rub.is_finite() or not 0 < rub <= 10:
            raise ValueError('Cascade budget must be positive and at most 10 RUB')
        self.money_budget_micro = int((rub / Decimal(89) * 1000000).to_integral_value(rounding=ROUND_FLOOR))

    def accept(self, c, event, payload, actor='integration'):
        result = super().accept(c, event, payload, actor=actor)
        if not result['duplicate']:
            c.execute('UPDATE analysis_job SET model_route=? WHERE job_id=?',
                      ('cascade-v1' if self.cascade_enabled else 'single', result['job_id']))
        return result

    def operator_handoff(self, c, job, card, reason):
        from backend.handoff import build
        attempts = [dict(row) for row in c.execute(
            'SELECT provider,invocation_claimed,diagnostics_json FROM model_attempt WHERE job_id=? ORDER BY attempt_no',
            (job['job_id'],)).fetchall()]
        for attempt in attempts:
            attempt['diagnostics'] = json.loads(attempt.pop('diagnostics_json') or '{}')
        return build(json.loads(job['input_json']), card, attempts, reason)

    def save_manual(self, c, job, reason):
        if job['model_route'] != 'cascade-v1':
            return super().save_manual(c, job, reason)
        if not self.manual_factory or job['proposal_id']:
            return
        payload = json.loads(job['input_json'])
        card, handoff = self.operator_handoff(c, job, self.manual_factory(payload['request_id'], reason), reason)
        proposal_id = str(uuid4())
        c.execute('INSERT INTO analysis_proposal(proposal_id,request_id,input_hash,ticket_ref,card_json,diagnostics_json,input_json,input_version) VALUES (?,?,?,?,?,?,?,?)',
                  (proposal_id, payload['request_id'], hashlib.sha256(job['input_json'].encode()).hexdigest(), job['ticket_ref'],
                   json.dumps(card, ensure_ascii=False), json.dumps({'handoff':handoff,'reason':reason}, ensure_ascii=False), job['input_json'], job['input_version']))
        c.execute('UPDATE analysis_job SET proposal_id=? WHERE job_id=?', (proposal_id, job['job_id']))
        trace = {'event_id':job['event_id'], 'input_version':job['input_version'], 'request_id':payload['request_id'],
                 'kb_version':payload['kb_version'], 'attempt':job['attempts'], 'state':'manual_review', 'reason':reason,
                 'end_to_end_ms':round((time.time()-job['created_at'])*1000), 'model_latency_ms':None}
        c.execute('INSERT INTO event_trace(trace_id,job_id,metadata_json) VALUES (?,?,?)',
                  (str(uuid4()),job['job_id'],json.dumps(trace)))

    def reconcile_billing(self, receipts=None):
        """Settle final, unambiguously matched receipts once under the queue lock."""
        from ml.cascade.billing import fetch_receipts, matching_receipt, receipt_cost
        if receipts is None:
            if not os.environ.get('APIMIRA_API_KEY'):
                return 0
            with self.transaction() as c:
                now = time.time()
                state = c.execute("SELECT next_reconcile_at FROM provider_runtime_state WHERE provider='sol'").fetchone()
                pending = c.execute("SELECT 1 FROM model_attempt WHERE provider='sol' AND billed_micro_usd IS NULL AND state!='reserved' LIMIT 1").fetchone()
                if not pending or state['next_reconcile_at'] > now:
                    return 0
                c.execute("UPDATE provider_runtime_state SET next_reconcile_at=? WHERE provider='sol'", (now + 30,))
            try:
                receipts = fetch_receipts()
            except Exception:
                # A billing outage keeps the existing reservations and input queue.
                return 0
        settled = 0
        with self.transaction() as c:
            attempts = c.execute("SELECT * FROM model_attempt WHERE provider='sol' AND billed_micro_usd IS NULL AND state!='reserved'").fetchall()
            for attempt in attempts:
                diagnostics = json.loads(attempt['diagnostics_json'] or '{}')
                receipt = matching_receipt(attempt, diagnostics, receipts)
                if not receipt or not isinstance(receipt.get('id'), str) or not receipt['id']:
                    continue
                debit = receipt_cost(receipt)
                if debit is None:
                    continue
                used = c.execute('SELECT attempt_id FROM provider_billing_receipt WHERE provider=? AND receipt_id=?',
                                 ('sol', receipt['id'])).fetchone()
                if used:
                    continue
                # Ambiguous token/time matches must not settle two local attempts.
                candidates = [a for a in attempts if matching_receipt(a, json.loads(a['diagnostics_json'] or '{}'), [receipt])]
                if len(candidates) != 1:
                    continue
                c.execute('INSERT INTO provider_billing_receipt VALUES (?,?,?,?,?,?)',
                          ('sol', receipt['id'], attempt['request_id'], debit, receipt['status'], time.time()))
                diagnostics.update(billing_status='settled', billed_micro_usd=debit,
                                   billing_receipt_id=receipt['id'], billing_receipt_status=receipt['status'])
                c.execute('UPDATE model_attempt SET billed_micro_usd=?,diagnostics_json=? WHERE request_id=?',
                          (debit, json.dumps(diagnostics, ensure_ascii=False), attempt['request_id']))
                settled += 1
        return settled

    def claim(self):
        with self.transaction() as c:
            # Admission may commit while this worker waits for the shared lock.
            # Use the time after acquiring it for ordering, leases and deadlines.
            now = time.time()
            self.recover(c, now)
            active = c.execute("SELECT COUNT(*) AS n FROM analysis_job WHERE state='running'").fetchone()['n']
            if active >= self.running:
                return None
            minute = c.execute('SELECT COUNT(*) AS n FROM model_attempt WHERE reserved_at>?', (now-60,)).fetchone()['n']
            if minute >= self.rpm:
                return None
            suffix = ' FOR UPDATE SKIP LOCKED' if getattr(c, 'dialect', '') == 'postgresql' else ''
            states = {r['provider']: r for r in c.execute('SELECT * FROM provider_runtime_state').fetchall()}
            mistral_ready = max(states['mistral']['next_call_at'], states['mistral']['cooldown_until'])
            mistral_active = c.execute("SELECT COUNT(*) AS n FROM analysis_job j JOIN model_attempt m ON m.request_id=j.claim_id WHERE j.state='running' AND m.provider='mistral'").fetchone()['n']
            if mistral_active >= self.mistral_running:
                mistral_ready = max(mistral_ready, now + 1)
            sol_ready = states['sol']['cooldown_until']
            row = c.execute("SELECT * FROM analysis_job j WHERE (j.model_route!='cascade-v1' OR (j.attempts=0 AND ?<=?) OR (j.attempts>0 AND ?<=?)) AND state='queued' AND not_before<=? AND NOT EXISTS (SELECT 1 FROM analysis_job r WHERE r.ticket_ref=j.ticket_ref AND r.state='running') ORDER BY created_at LIMIT 1" + suffix, (mistral_ready, now, sol_ready, now, now)).fetchone()
            if not row:
                return None
            row = dict(row)
            # UTF-8 bytes plus maximum completion is a conservative upper reservation.
            reservation = len(row['input_json'].encode('utf-8')) + 65536
            used = c.execute('SELECT COALESCE(SUM(reserved_tokens),0) AS n FROM model_attempt').fetchone()['n']
            attempt = row['attempts'] + 1
            cascade = row['model_route'] == 'cascade-v1'
            provider = ('mistral' if attempt == 1 else 'sol') if cascade else None
            model = MODELS[provider] if provider else None
            money_reserve = SOL_RESERVATION_MICRO if provider == 'sol' else 0
            money_used = c.execute("SELECT COALESCE(SUM(COALESCE(billed_micro_usd,reserved_micro_usd)),0) AS n FROM model_attempt WHERE provider='sol'").fetchone()['n']
            if cascade and money_used + money_reserve > self.money_budget_micro:
                policy_event(c, 'denial', 'cascade_money_budget_exhausted', now)
                self.save_manual(c, row, 'cascade_money_budget_exhausted')
                c.execute("UPDATE analysis_job SET state='manual_review',reason='cascade_money_budget_exhausted' WHERE job_id=?", (row['job_id'],))
                return None
            if cascade and not self.cascade_enabled:
                self.save_manual(c, row, 'cascade_mode_unavailable')
                c.execute("UPDATE analysis_job SET state='manual_review',reason='cascade_mode_unavailable' WHERE job_id=?", (row['job_id'],))
                policy_event(c, 'denial', 'cascade_mode_unavailable', now)
                return None
            stage_context = None
            if cascade:
                payload = json.loads(row['input_json'])
                if attempt == 1:
                    try:
                        stage_context = make_context(self.cascade_kb, payload)
                    except ValueError:
                        self.save_manual(c, row, 'knowledge_snapshot_unavailable')
                        c.execute("UPDATE analysis_job SET state='manual_review',reason='knowledge_snapshot_unavailable' WHERE job_id=?", (row['job_id'],))
                        return None
                else:
                    previous = c.execute('SELECT diagnostics_json FROM model_attempt WHERE job_id=? AND attempt_no=1', (row['job_id'],)).fetchone()
                    try:
                        stage_context = checked_context(json.loads(previous['diagnostics_json'])['stage_context'], payload)
                    except (ValueError, KeyError, TypeError):
                        self.save_manual(c, row, 'cascade_context_unavailable')
                        c.execute("UPDATE analysis_job SET state='manual_review',reason='cascade_context_unavailable' WHERE job_id=?", (row['job_id'],))
                        return None
                if provider == 'sol':
                    reservation = MAX_INPUT_TOKENS + MAX_OUTPUT
                else:
                    # The free provider may receive substantially more than Sol.
                    # Double JSON byte sizes cover nested escaping; the technical
                    # model window is the hard upper bound, not a monetary cap.
                    snapshot_bytes = len(json.dumps(stage_context['kb_snapshot'], ensure_ascii=False).encode('utf-8'))
                    reservation = min(MISTRAL_MAX_INPUT_TOKENS + MAX_OUTPUT,
                                      2 * len(row['input_json'].encode('utf-8')) + 2 * snapshot_bytes + 65536)
            if used + reservation > self.budget:
                return None
            effective_mode = 'cascade' if cascade else ('extractive' if self.mode == 'cascade' else self.mode)
            if effective_mode in ('llm', 'cascade'):
                if not self.daily.reserve(c, row['actor_ref'], reservation, now):
                    if cascade:
                        self.save_manual(c, row, 'cascade_daily_quota_exhausted')
                        c.execute("UPDATE analysis_job SET state='manual_review',reason='cascade_daily_quota_exhausted' WHERE job_id=?", (row['job_id'],))
                        return None
                    effective_mode = 'extractive'
                else:
                    recent = c.execute('SELECT COALESCE(SUM(reserved_tokens),0) AS n FROM model_attempt WHERE reserved_at>?', (now-60,)).fetchone()['n']
                    if recent + reservation >= self.daily.anomaly:
                        policy_event(c, 'spend_anomaly', 'reservation_rate', now)
            if provider == 'mistral' and self.mistral_rpm:
                c.execute('UPDATE provider_runtime_state SET next_call_at=? WHERE provider=?', (now + 60/self.mistral_rpm, provider))
            attempt_id = str(uuid4())
            c.execute("INSERT INTO model_attempt(job_id,attempt_no,request_id,reserved_tokens,state,reserved_at,provider,model,reserved_micro_usd) VALUES (?,?,?,?,'reserved',?,?,?,?)", (row['job_id'], attempt, attempt_id, reservation, now, provider, model, money_reserve))
            if stage_context is not None:
                c.execute('UPDATE model_attempt SET diagnostics_json=? WHERE request_id=?',
                          (json.dumps({'stage_context': stage_context}, ensure_ascii=False), attempt_id))
            lease_until = now + 30
            if provider:
                lease_until = min(row['deadline_at'], now + CASCADE_STAGE_TIMEOUTS[provider] + CASCADE_GRACE_SECONDS)
            c.execute("UPDATE analysis_job SET state='running',attempts=?,lease_until=?,claim_id=?,started_at=COALESCE(started_at,?) WHERE job_id=?", (attempt, lease_until, attempt_id, now, row['job_id']))
            c.execute('UPDATE analysis_job SET inference_mode=? WHERE job_id=?', (effective_mode, row['job_id']))
            row.update(inference_mode=effective_mode, attempts=attempt, claim_id=attempt_id, claimed_at=row['started_at'] or now, provider=provider, model=model, reserved_micro_usd=money_reserve, lease_until=lease_until)
            return row

    def claim_inference(self, job_id, attempt_id, provider, payload):
        """Consume a persisted reservation exactly once before external I/O."""
        with self.transaction() as c:
            row = c.execute('SELECT * FROM analysis_job WHERE job_id=?', (job_id,)).fetchone()
            attempt = c.execute('SELECT * FROM model_attempt WHERE job_id=? AND request_id=?', (job_id, attempt_id)).fetchone()
            now = time.time()
            if (not row or not attempt or row['model_route'] != 'cascade-v1' or row['state'] != 'running'
                    or row['claim_id'] != attempt_id or row['deadline_at'] <= now
                    or row['lease_until'] <= now or attempt['state'] != 'reserved'
                    or attempt['provider'] != provider or attempt['model'] != MODELS[provider]
                    or attempt['invocation_claimed']):
                raise HTTPException(409, 'Reservation unavailable or already consumed')
            if provider == 'sol' and attempt['reserved_micro_usd'] < SOL_RESERVATION_MICRO:
                raise HTTPException(409, 'Paid reservation missing')
            ticket = self.ticket(c, row['ticket_ref'])
            if ticket['input_version'] != row['input_version']:
                raise HTTPException(409, 'Input version is stale')
            stored = json.loads(row['input_json'])
            if stored != payload:
                raise HTTPException(409, 'Input differs from reserved snapshot')
            # Leave one second for persisting the result before the job/lease expires.
            remaining = min(row['deadline_at'], row['lease_until']) - now - 1
            if remaining <= 0:
                raise HTTPException(409, 'Insufficient time for reserved inference')
            c.execute('UPDATE model_attempt SET invocation_claimed=1 WHERE request_id=?', (attempt_id,))
            return stored, remaining

    def inference_context(self, job_id, attempt_id, payload):
        """Load context only for the authoritative, current reserved attempt."""
        with self.transaction() as c:
            row = c.execute('SELECT * FROM analysis_job WHERE job_id=?', (job_id,)).fetchone()
            attempt = c.execute('SELECT * FROM model_attempt WHERE job_id=? AND request_id=?', (job_id, attempt_id)).fetchone()
            now = time.time()
            if (not row or not attempt or row['state'] != 'running' or row['claim_id'] != attempt_id
                    or row['deadline_at'] <= now or row['lease_until'] <= now
                    or attempt['state'] != 'reserved' or json.loads(row['input_json']) != payload):
                raise HTTPException(409, 'Cascade context reservation is unavailable')
            ticket = self.ticket(c, row['ticket_ref'])
            if not ticket or ticket['input_version'] != row['input_version']:
                raise HTTPException(409, 'Input version is stale')
            try:
                return checked_context(json.loads(attempt['diagnostics_json'])['stage_context'], payload)
            except (ValueError, KeyError, TypeError):
                raise HTTPException(409, 'Cascade context is unavailable') from None

    def finish(self, job, result=None):
        if job.get('model_route', 'single') != 'cascade-v1':
            return super().finish(job, result)
        with self.transaction() as c:
            now = time.time()
            current = c.execute('SELECT * FROM analysis_job WHERE job_id=?', (job['job_id'],)).fetchone()
            # Fence a late worker after recovery, or an expired deadline.
            if current['state'] != 'running' or current['claim_id'] != job['claim_id']:
                return
            c.execute("UPDATE model_attempt SET state=? WHERE request_id=?", ('completed' if result else 'unknown', job['claim_id']))
            if current['model_route'] == 'cascade-v1' and result:
                # Keep the pre-network snapshot even when a legacy controlled result omits context.
                prior = c.execute('SELECT diagnostics_json FROM model_attempt WHERE request_id=?', (job['claim_id'],)).fetchone()
                prior_context = json.loads(prior['diagnostics_json'] or '{}').get('stage_context')
                result = dict(result)
                if result.get('stage_context') is None and prior_context is not None:
                    result['stage_context'] = prior_context
                if result.get('stage_context') is not None:
                    checked_context(result['stage_context'], json.loads(job['input_json']))
                    if prior_context and result['stage_context']['kb_hash'] != prior_context['kb_hash']:
                        raise ValueError('Cannot replace the reserved knowledge snapshot')
                billed = 0 if result.get('not_sent') or job['provider'] == 'mistral' else result.get('billed_micro_usd')
                result['reserved_micro_usd'] = job['reserved_micro_usd']
                if result.get('http_status') == 429:
                    delay = result.get('retry_after_seconds') or 60
                    c.execute('UPDATE provider_runtime_state SET cooldown_until=? WHERE provider=?',
                              (now + min(3600, max(1, delay)), job['provider']))
                # Only verified billing may settle a reserve; usage estimates cannot.
                if billed is not None and (type(billed) is not int or billed < 0):
                    billed = None
                result['billing_status'] = 'settled' if billed is not None else 'pending'
                c.execute('UPDATE model_attempt SET billed_micro_usd=?,diagnostics_json=? WHERE request_id=?',
                          (billed, json.dumps(result, ensure_ascii=False), job['claim_id']))
                attempt_trace = {'trace_kind':'model_attempt','event_id':job['event_id'],
                                 'job_id':job['job_id'],'input_version':job['input_version'],
                                 'request_id':result['request_id'],'attempt_id':job['claim_id'],
                                 'attempt':job['attempts'],'provider':job['provider'],'model':job['model'],
                                 'usage':result.get('usage'),'latency_ms':result.get('latency_ms'),
                                 'route_reason':result.get('route_reason'),'cascade_next':result.get('cascade_next'),
                                 'inference_skipped':result.get('inference_skipped',False)}
                c.execute('INSERT INTO event_trace(trace_id,job_id,metadata_json) VALUES (?,?,?)',
                          (str(uuid4()),job['job_id'],json.dumps(attempt_trace)))
            ticket = self.ticket(c, job['ticket_ref'])
            if now > current['deadline_at']:
                self.save_manual(c, current, 'deadline')
                c.execute("UPDATE analysis_job SET state='manual_review',reason='deadline',lease_until=NULL WHERE job_id=?", (job['job_id'],))
                return
            if result and current['model_route'] == 'cascade-v1' and result.get('cascade_next') and job['attempts'] < 2:
                if ticket['input_version'] != job['input_version']:
                    c.execute("UPDATE analysis_job SET state='superseded',lease_until=NULL WHERE job_id=?", (job['job_id'],))
                elif current['deadline_at'] - now <= 2:
                    self.save_manual(c, current, 'insufficient_time_for_sol')
                    c.execute("UPDATE analysis_job SET state='manual_review',reason='insufficient_time_for_sol',lease_until=NULL WHERE job_id=?", (job['job_id'],))
                else:
                    c.execute("UPDATE analysis_job SET state='queued',reason=?,not_before=?,lease_until=NULL WHERE job_id=?",
                              (result['route_reason'], now, job['job_id']))
                return
            if not result:
                state = 'queued' if job['attempts'] < 2 else 'manual_review'
                if ticket['input_version'] != job['input_version']:
                    state = 'superseded'
                if state == 'manual_review':
                    self.save_manual(c, current, 'inference_unavailable')
                c.execute("UPDATE analysis_job SET state=?,reason='inference_unavailable',not_before=?,lease_until=NULL WHERE job_id=?", (state, now+1, job['job_id']))
                return
            proposal_id = str(uuid4())
            payload = json.loads(job['input_json'])
            card = result['final_card']
            diagnostics = {key: result.get(key) for key in ('provider','model','latency_ms','usage','gate_passed','attempts','route_reason','completion_id','returned_model','raw_card','automatic_resolution')}
            if card['needs_human_review']:
                card, diagnostics['handoff'] = self.operator_handoff(c, job, card, result.get('route_reason') or 'manual_review')
            digest = hashlib.sha256(job['input_json'].encode()).hexdigest()
            c.execute('INSERT INTO analysis_proposal(proposal_id,request_id,input_hash,ticket_ref,card_json,diagnostics_json,input_json,input_version) VALUES (?,?,?,?,?,?,?,?)',
                      (proposal_id, payload['request_id'], digest, job['ticket_ref'], json.dumps(card, ensure_ascii=False),
                       json.dumps(diagnostics, ensure_ascii=False), job['input_json'], job['input_version']))
            if current['model_route'] == 'cascade-v1':
                state = 'ready' if card['status'] == 'resolved' and not card['needs_human_review'] else 'manual_review'
            else:
                state = 'ready' if card['status'] == 'proposed' else 'manual_review'
            if ticket['input_version'] != job['input_version']:
                state = 'superseded'
            c.execute('UPDATE analysis_job SET state=?,proposal_id=?,reason=?,lease_until=NULL WHERE job_id=?', (state, proposal_id, result.get('route_reason', current['reason']), job['job_id']))
            trace = {'event_id': job['event_id'], 'input_version': job['input_version'], 'request_id': payload['request_id'], 'kb_version': payload['kb_version'], 'attempt': job['attempts'], 'state': state, 'automatic_resolution': result.get('automatic_resolution', False), 'queue_wait_ms': round((job['claimed_at']-job['created_at'])*1000), 'end_to_end_ms': round((now-job['created_at'])*1000), 'model_latency_ms': result.get('latency_ms')}
            c.execute('INSERT INTO event_trace(trace_id,job_id,metadata_json) VALUES (?,?,?)', (str(uuid4()), job['job_id'], json.dumps(trace)))
