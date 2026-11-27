"""Resolve cache scope from trusted queue state after single-use claim validation."""
import hashlib


def authenticated_cache_scope(queue, job_id):
    with queue.connect() as connection:
        row = connection.execute('SELECT actor_ref FROM analysis_job WHERE job_id=?', (job_id,)).fetchone()
    if row is None or not isinstance(row['actor_ref'], str) or not row['actor_ref']:
        return None
    return hashlib.sha256(('actor:' + row['actor_ref']).encode()).hexdigest()
