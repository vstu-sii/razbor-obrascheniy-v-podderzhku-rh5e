"""The service key alone cannot authorize an unreserved or replayed paid call."""
import os
import time
from fastapi import HTTPException


def consume(store, body):
    target = os.environ.get('DATABASE_URL') or os.environ.get('DATABASE_PATH','/data/triage.db')
    with store.open_store(target) as c:
        if getattr(c,'dialect','') == 'postgresql': c.execute('SELECT pg_advisory_xact_lock(310012)')
        else: c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM analysis_job WHERE job_id=?',(body.job_id,)).fetchone()
        if not row or row['state']!='running' or row['claim_id']!=body.claim_id or row['deadline_at']<=time.time() or row['lease_until']<=time.time():
            raise HTTPException(409,'No current inference reservation')
        import json
        if json.loads(row['input_json']) != body.input.model_dump():
            raise HTTPException(409,'Input differs from reserved snapshot')
        expected = 'extractive' if row['inference_mode']=='extractive' else None
        if body.mode != expected:
            raise HTTPException(409,'Mode differs from reserved policy')
        previous=c.execute('SELECT claim_id FROM inference_call WHERE claim_id=?',(body.claim_id,)).fetchone()
        if previous:
            raise HTTPException(409,'Reservation already used')
        c.execute('INSERT INTO inference_call VALUES (?,?)',(body.claim_id,time.time()))
