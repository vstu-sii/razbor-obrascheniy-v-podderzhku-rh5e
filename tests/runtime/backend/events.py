"""Durable, bounded queue. All admission, claim and review locks share one order."""
from contextlib import contextmanager
import hashlib
import json
import os
import time
from uuid import uuid4

from fastapi import HTTPException


from limits.settings import configured


class Queue:
    def __init__(self, connect, manual_factory=None):
        self.connect = connect
        self.manual_factory = manual_factory
        self.running = configured('MAX_RUNNING', 2, 32)
        self.pending = configured('MAX_PENDING', 100, 10000)
        self.rpm = configured('MODEL_REQUESTS_PER_MINUTE', 20)
        self.budget = configured('TOKEN_BUDGET', 1000000)
        self.deadline = 55

    @contextmanager
    def transaction(self):
        with self.connect() as c:
            if getattr(c, 'dialect', '') == 'postgresql':
                c.execute('SELECT pg_advisory_xact_lock(310012)')
            else:
                c.execute('BEGIN IMMEDIATE')
            yield c

    def ticket(self, c, ref):
        suffix = ' FOR UPDATE' if getattr(c, 'dialect', '') == 'postgresql' else ''
        return c.execute('SELECT * FROM ticket_snapshot WHERE ticket_ref=?' + suffix, (ref,)).fetchone()

    def accept(self, c, event, payload):
        """Called inside one transaction; no acknowledgement before it commits."""
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        old = c.execute('SELECT * FROM analysis_event WHERE event_id=?', (event['event_id'],)).fetchone()
        if old:
            if old['event_hash'] != fingerprint:
                raise HTTPException(409, 'event_id belongs to different metadata')
            return {'job_id': old['job_id'], 'duplicate': True}
        old = c.execute('SELECT * FROM analysis_job WHERE ticket_ref=? AND input_version=?',
                        (event['ticket_ref'], event['input_version'])).fetchone()
        if old:
            if old['input_json'] != encoded:
                raise HTTPException(409, 'Version already belongs to different input')
            return {'job_id': old['job_id'], 'duplicate': True}
        ticket = self.ticket(c, event['ticket_ref'])
        if ticket and event['input_version'] <= ticket['input_version']:
            raise HTTPException(409, 'Stale input version')
        waiting = c.execute("SELECT job_id FROM analysis_job WHERE ticket_ref=? AND state='queued'", (event['ticket_ref'],)).fetchone()
        count = c.execute("SELECT COUNT(*) AS n FROM analysis_job WHERE state='queued'").fetchone()['n']
        if not waiting and count >= self.pending:
            raise HTTPException(503, 'Queue full; retry the same event', headers={'Retry-After': '5'})
        if waiting:
            c.execute("UPDATE analysis_job SET state='superseded',reason='newer_input' WHERE job_id=?", (waiting['job_id'],))
        job_id = str(uuid4())
        c.execute('INSERT INTO analysis_event(event_id,event_hash,ticket_ref,input_version,event_type,kb_version,occurred_at,job_id) VALUES (?,?,?,?,?,?,?,?)',
                  (event['event_id'], fingerprint, event['ticket_ref'], event['input_version'], event['event_type'], event['kb_version'], event['occurred_at'], job_id))
        c.execute("INSERT INTO analysis_job(job_id,event_id,ticket_ref,input_version,input_json,state,created_at,deadline_at) VALUES (?,?,?,?,?,'queued',?,?)",
                  (job_id, event['event_id'], event['ticket_ref'], event['input_version'], encoded, time.time(), time.time()+self.deadline))
        c.execute('INSERT INTO ticket_snapshot(ticket_ref,input_version,input_json,job_id) VALUES (?,?,?,?) ON CONFLICT(ticket_ref) DO UPDATE SET input_version=excluded.input_version,input_json=excluded.input_json,job_id=excluded.job_id',
                  (event['ticket_ref'], event['input_version'], encoded, job_id))
        return {'job_id': job_id, 'duplicate': False}

    def recover(self, c, now):
        # Unknown network cost stays reserved. Lease expiry never resets attempts.
        c.execute("UPDATE model_attempt SET state='unknown' WHERE state='reserved' AND job_id IN (SELECT job_id FROM analysis_job WHERE state='running' AND lease_until<=?)", (now,))
        rows = c.execute("SELECT * FROM analysis_job WHERE state IN ('queued','running')").fetchall()
        for row in rows:
            expired = row['state'] == 'running' and row['lease_until'] <= now
            if row['deadline_at'] <= now or (expired and row['attempts'] >= 2):
                self.save_manual(c, row, 'deadline_or_attempt_limit')
                c.execute("UPDATE analysis_job SET state='manual_review',reason='deadline_or_attempt_limit',lease_until=NULL WHERE job_id=?", (row['job_id'],))
            elif expired:
                ticket = self.ticket(c, row['ticket_ref'])
                state = 'queued' if ticket['input_version'] == row['input_version'] else 'superseded'
                c.execute('UPDATE analysis_job SET state=?,lease_until=NULL WHERE job_id=?', (state, row['job_id']))

    def save_manual(self, c, job, reason):
        """A failed task remains readable and reviewable, without a client draft."""
        if not self.manual_factory or job['proposal_id']:
            return
        payload = json.loads(job['input_json'])
        card = self.manual_factory(payload['request_id'], reason)
        proposal_id = str(uuid4())
        c.execute('INSERT INTO analysis_proposal(proposal_id,request_id,input_hash,ticket_ref,card_json,diagnostics_json,input_json,input_version) VALUES (?,?,?,?,?,?,?,?)',
                  (proposal_id, payload['request_id'], hashlib.sha256(job['input_json'].encode()).hexdigest(), job['ticket_ref'],
                   json.dumps(card, ensure_ascii=False), json.dumps({'provider':'manual_review','attempts':job['attempts'],'reason':reason}), job['input_json'], job['input_version']))
        c.execute('UPDATE analysis_job SET proposal_id=? WHERE job_id=?', (proposal_id, job['job_id']))
        trace = {'event_id':job['event_id'], 'input_version':job['input_version'],
                 'request_id':payload['request_id'], 'kb_version':payload['kb_version'],
                 'attempt':job['attempts'], 'state':'manual_review', 'reason':reason,
                 'end_to_end_ms':round((time.time()-job['created_at'])*1000), 'model_latency_ms':None}
        c.execute('INSERT INTO event_trace(trace_id,job_id,metadata_json) VALUES (?,?,?)', (str(uuid4()),job['job_id'],json.dumps(trace)))

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
            row = c.execute("SELECT * FROM analysis_job j WHERE state='queued' AND not_before<=? AND NOT EXISTS (SELECT 1 FROM analysis_job r WHERE r.ticket_ref=j.ticket_ref AND r.state='running') ORDER BY created_at LIMIT 1" + suffix, (now,)).fetchone()
            if not row:
                return None
            row = dict(row)
            # UTF-8 bytes plus maximum completion is a conservative upper reservation.
            reservation = len(row['input_json'].encode('utf-8')) + 65536
            used = c.execute('SELECT COALESCE(SUM(reserved_tokens),0) AS n FROM model_attempt').fetchone()['n']
            if used + reservation > self.budget:
                return None
            attempt = row['attempts'] + 1
            attempt_id = str(uuid4())
            c.execute("INSERT INTO model_attempt(job_id,attempt_no,request_id,reserved_tokens,state,reserved_at) VALUES (?,?,?,?,'reserved',?)", (row['job_id'], attempt, attempt_id, reservation, now))
            c.execute("UPDATE analysis_job SET state='running',attempts=?,lease_until=?,claim_id=?,started_at=COALESCE(started_at,?) WHERE job_id=?", (attempt, now+30, attempt_id, now, row['job_id']))
            row.update(attempts=attempt, claim_id=attempt_id, claimed_at=row['started_at'] or now)
            return row

    def finish(self, job, result=None):
        with self.transaction() as c:
            now = time.time()
            current = c.execute('SELECT * FROM analysis_job WHERE job_id=?', (job['job_id'],)).fetchone()
            # Fence a late worker after recovery, or an expired deadline.
            if current['state'] != 'running' or current['claim_id'] != job['claim_id']:
                return
            c.execute("UPDATE model_attempt SET state=? WHERE request_id=?", ('completed' if result else 'unknown', job['claim_id']))
            ticket = self.ticket(c, job['ticket_ref'])
            if now > current['deadline_at']:
                self.save_manual(c, current, 'deadline')
                c.execute("UPDATE analysis_job SET state='manual_review',reason='deadline',lease_until=NULL WHERE job_id=?", (job['job_id'],))
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
            digest = hashlib.sha256(job['input_json'].encode()).hexdigest()
            c.execute('INSERT INTO analysis_proposal(proposal_id,request_id,input_hash,ticket_ref,card_json,diagnostics_json,input_json,input_version) VALUES (?,?,?,?,?,?,?,?)',
                      (proposal_id, payload['request_id'], digest, job['ticket_ref'], json.dumps(card, ensure_ascii=False),
                       json.dumps({key: result.get(key) for key in ('provider','latency_ms','usage','gate_passed','attempts')}), job['input_json'], job['input_version']))
            state = 'ready' if card['status'] == 'proposed' else 'manual_review'
            if ticket['input_version'] != job['input_version']:
                state = 'superseded'
            c.execute('UPDATE analysis_job SET state=?,proposal_id=?,lease_until=NULL WHERE job_id=?', (state, proposal_id, job['job_id']))
            trace = {'event_id': job['event_id'], 'input_version': job['input_version'], 'request_id': payload['request_id'], 'kb_version': payload['kb_version'], 'attempt': job['attempts'], 'state': state, 'queue_wait_ms': round((job['claimed_at']-job['created_at'])*1000), 'end_to_end_ms': round((now-job['created_at'])*1000), 'model_latency_ms': result.get('latency_ms')}
            c.execute('INSERT INTO event_trace(trace_id,job_id,metadata_json) VALUES (?,?,?)', (str(uuid4()), job['job_id'], json.dumps(trace)))

    def status(self, job_id):
        with self.transaction() as c:
            self.recover(c, time.time())
            row = c.execute('SELECT job_id,ticket_ref,input_version,state,attempts,reason,proposal_id FROM analysis_job WHERE job_id=?', (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Job not found')
            return dict(row)
