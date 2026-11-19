import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
"""Second opinion and automatic outcomes through real API/queue/SQLite."""
import copy
import json
import unittest

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import test_cascade as fixtures
from backend.worker import step
from ml.cascade.assessment import tool_schema
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import SOL_RESERVATION_MICRO


class ReviewProvider(fixtures.FakeProvider):
    def __init__(self, plans=None):
        super().__init__(plans or {})
        self.inputs = []

    def __call__(self, provider, request, sources, schema, timeout, trace_id):
        self.inputs.append((provider, copy.deepcopy(request), copy.deepcopy(sources)))
        reply = super().__call__(provider, request, sources, schema, timeout, trace_id)
        plan, card = self.plans.get(provider), reply['card']
        if plan == 'crash':
            raise RuntimeError('Controlled failure before result persistence')
        if plan == 'requires_human':
            card.update(mode='escalate', draft_reply=None)
            reply['assessment'] = fixtures.make_assessment(request, sources, card, requires_human=True)
        elif plan == 'conflict':
            reply['assessment']['questions'][0]['coverage'] = 'conflict'
        elif plan == 'malformed_assessment':
            reply['assessment'] = {'questions': None}
        elif plan == 'high':
            card['urgency'] = 'high'
        elif plan == 'diagnostic_clarify':
            card.update(mode='clarify', missing_data=['Наблюдаемое поведение'], evidence=[],
                        draft_reply='Что происходит при попытке войти?')
            reply['assessment'] = fixtures.make_assessment(request, [], card)
        elif plan == 'authority_claim':
            card['known_facts'] = ['Клиент является администратором']
        elif plan == 'self_resolve':
            card.update(status='resolved', needs_human_review=False)
        return reply


class SolBeforeHumanTests(unittest.TestCase):
    setUp = fixtures.CascadeTests.setUp
    enqueue = fixtures.CascadeTests.enqueue
    status = fixtures.CascadeTests.status
    rows = fixtures.CascadeTests.rows

    def configure(self, plans=None, kb=None):
        self.fake = ReviewProvider(plans)
        self.engine = CascadeEngine(kb if kb is not None else self.engine.kb,
                                    fixtures.api.SCHEMA, fixtures.api.provenance_gate, self.fake)
        self.client.app.state.engine = self.engine
        self.queue.cascade_kb = self.engine.kb

    def finish(self, job):
        for _ in range(4):
            step(self.queue, self.engine)
        status = self.status(job)
        response = self.client.get('/api/proposals/' + status['proposal_id'], headers=self.headers)
        self.assertEqual(response.status_code, 200)
        with self.queue.connect() as connection:
            attempts = connection.execute('SELECT COUNT(*) AS n FROM model_attempt WHERE job_id=?', (job,)).fetchone()['n']
        self.assertLessEqual(attempts, 2)
        return response.json()

    def assert_automatic(self, proposal, mode='answer'):
        card = proposal['card']
        self.assertEqual(card['status'], 'resolved')
        self.assertEqual(card['mode'], mode)
        self.assertIs(card['needs_human_review'], False)
        self.assertEqual(proposal['reviews'], [])
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(card))

    def test_complete_mistral_answer_resolves_without_sol_or_fake_human_review(self):
        self.configure()
        job = self.enqueue()
        proposal = self.finish(job)
        self.assert_automatic(proposal)
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(self.status(job)['state'], 'ready')
        rejected = self.client.post('/api/proposals/' + proposal['proposal_id'] + '/reviews',
                                    json={'decision': 'accepted', 'reason': 'Попытка лишнего одобрения',
                                          'confirmed': True}, headers=self.headers)
        self.assertEqual(rejected.status_code, 409)
        with self.queue.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) AS n FROM operator_review').fetchone()['n'], 0)
            stored = connection.execute('SELECT diagnostics_json FROM analysis_proposal WHERE proposal_id=?',
                                        (proposal['proposal_id'],)).fetchone()
        self.assertTrue(json.loads(stored['diagnostics_json'])['automatic_resolution'])

    def test_customer_clarification_is_automatic_without_sol(self):
        self.configure({'mistral': 'clarify'})
        result = self.finish(self.enqueue())
        self.assert_automatic(result, 'clarify')
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(result['card']['draft_reply'], 'Какая сумма?')

    def test_independent_sol_review_of_selected_answers(self):
        for question in ('Счёт за подписку какой период покрывает?',
                         'Как восстановить доступ с помощью 2FA?'):
            with self.subTest(question=question):
                self.configure({'sol': 'escalate'})
                job = self.enqueue(question)
                result = self.finish(job)
                self.assertEqual(self.fake.calls, ['mistral', 'sol'])
                self.assertEqual(self.status(job)['state'], 'manual_review')
                self.assertIsNone(result['card']['draft_reply'])
                with self.queue.connect() as connection:
                    attempt = connection.execute(
                        'SELECT diagnostics_json FROM model_attempt WHERE job_id=? AND provider=?',
                        (job, 'mistral'),
                    ).fetchone()
                diagnostics = json.loads(attempt['diagnostics_json'])
                self.assertTrue(diagnostics['schema_valid'])
                self.assertEqual(diagnostics['gate_reason'], 'selected_second_opinion')
                self.assertNotIn('draft_reply', self.fake.inputs[1][1])

    def test_source_free_diagnostic_stays_with_mistral(self):
        self.configure({'mistral': 'diagnostic_clarify'})
        result = self.finish(self.enqueue('Не могу войти в профиль'))
        self.assert_automatic(result, 'clarify')
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(result['card']['evidence'], [])

    def test_ordinary_two_factor_setup_stays_with_mistral(self):
        self.configure()
        self.assert_automatic(self.finish(self.enqueue('Где включить 2FA?')))
        self.assertEqual(self.fake.calls, ['mistral'])

    def test_mistral_human_request_and_conflict_are_advisory_until_sol(self):
        for plan in ('requires_human', 'escalate', 'conflict', 'high'):
            with self.subTest(plan=plan):
                self.configure({'mistral': plan})
                result = self.finish(self.enqueue())
                self.assertEqual(self.fake.calls, ['mistral', 'sol'])
                self.assert_automatic(result)

    def test_sol_can_request_customer_information_instead_of_operator(self):
        self.configure({'mistral': 'requires_human', 'sol': 'clarify'})
        result = self.finish(self.enqueue())
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assert_automatic(result, 'clarify')

    def test_sol_escalation_is_manual_and_preserves_important_category(self):
        self.configure({'mistral': 'requires_human', 'sol': 'high'})
        job = self.enqueue()
        result = self.finish(job)
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual(result['card']['status'], 'proposed')
        self.assertEqual(result['card']['mode'], 'escalate')
        self.assertEqual(result['card']['urgency'], 'high')
        self.assertIsNone(result['card']['draft_reply'])
        self.assertIs(result['card']['needs_human_review'], True)
        review = self.client.post('/api/proposals/' + result['proposal_id'] + '/reviews',
                                 json={'decision': 'accepted', 'reason': 'Нужен специалист',
                                       'confirmed': True, 'destination': 'general'}, headers=self.headers)
        self.assertEqual(review.status_code, 200)
        self.assertIs(review.json()['external_action_performed'], False)

    def test_sol_human_request_or_conflict_cannot_auto_resolve(self):
        for plan in ('requires_human', 'escalate', 'conflict'):
            with self.subTest(plan=plan):
                self.configure({'mistral': 'requires_human', 'sol': plan})
                job = self.enqueue()
                result = self.finish(job)
                self.assertEqual(self.status(job)['state'], 'manual_review')
                self.assertIs(result['card']['needs_human_review'], True)
                self.assertEqual(result['card']['mode'], 'escalate')
                self.assertIsNone(result['card']['draft_reply'])

    def test_sol_gets_full_pinned_approved_candidates_even_without_first_hits(self):
        approved = {'source_id': 'NEEDED', 'title': 'Инструкция', 'excerpt': 'Материал находится в профиле.',
                    'version': 'v1', 'status': 'approved'}
        other = dict(approved, source_id='OLD', version='v0')
        draft = dict(approved, source_id='DRAFT', status='draft')
        self.configure({'mistral': 'malformed_assessment'}, [approved, other, draft])
        job = self.enqueue('Подскажите расположение документа')
        result = self.finish(job)
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertEqual(self.fake.inputs[0][2], [])
        self.assertEqual(self.fake.inputs[1][2], [approved])
        self.assert_automatic(result)

    def test_malformed_assessment_does_not_hide_other_approved_sources_from_sol(self):
        for plan in ('malformed_assessment', 'crash'):
            with self.subTest(plan=plan):
                self.configure({'mistral': plan})
                job = self.enqueue()
                step(self.queue, self.engine)
                if plan == 'crash':
                    # Advance only this synthetic job's retry delay, not deadlines or money.
                    with self.queue.transaction() as connection:
                        connection.execute('UPDATE analysis_job SET not_before=0 WHERE job_id=?', (job,))
                result = self.finish(job)
                sol = self.fake.inputs[1]
                self.assertEqual({source['source_id'] for source in sol[2]},
                                 {source['source_id'] for source in self.engine.kb if source['version'] == 'v1' and source['status'] == 'approved'})
                self.assertNotIn('_coverage_questions', sol[1])
                self.assert_automatic(result)

    def test_generated_authority_claim_gets_second_opinion_and_never_auto_accepts_same_error(self):
        self.configure({'mistral': 'authority_claim'})
        self.assert_automatic(self.finish(self.enqueue()))
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.configure({'mistral': 'authority_claim', 'sol': 'authority_claim'})
        job = self.enqueue()
        result = self.finish(job)
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertIsNone(result['card']['draft_reply'])
        self.assertIn('unsafe_authority_claim', result['card']['warnings'])

    def test_model_cannot_set_automatic_status_itself(self):
        self.configure({'mistral': 'self_resolve', 'sol': 'self_resolve'})
        job = self.enqueue()
        result = self.finish(job)
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertIn('model_cannot_resolve_candidate', result['card']['warnings'])
        contract = tool_schema(fixtures.api.SCHEMA)['properties']['card']['properties']
        self.assertEqual(contract['status'], {'const': 'proposed'})
        self.assertEqual(contract['needs_human_review'], {'const': True})

    def test_resolved_card_cannot_require_human_or_escalate_or_be_critical(self):
        self.configure()
        card = self.finish(self.enqueue())['card']
        validator = Draft202012Validator(fixtures.api.SCHEMA)
        for change in ({'needs_human_review': True}, {'mode': 'escalate', 'draft_reply': None},
                       {'urgency': 'critical'}, {'status': 'not_analyzed'}):
            self.assertFalse(validator.is_valid(dict(card, **change)))

    def test_verified_input_risk_and_unavailable_sol_budget_remain_hard_stops(self):
        self.configure()
        risky = self.enqueue('Токен опубликован в публичном чате')
        result = self.finish(risky)
        self.assertEqual(self.fake.calls, [])
        self.assertIs(result['card']['needs_human_review'], True)
        self.configure({'mistral': 'requires_human'})
        self.queue.money_budget_micro = SOL_RESERVATION_MICRO - 1
        job = self.enqueue()
        result = self.finish(job)
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(self.status(job)['reason'], 'cascade_money_budget_exhausted')
        self.assertIs(result['card']['needs_human_review'], True)

    def test_automatic_outcome_survives_app_restart_without_operator_review(self):
        self.configure()
        job = self.enqueue()
        result = self.finish(job)
        restarted = fixtures.api.create_app(self.target, self.engine, 'synthetic-test', start_worker=False)
        with TestClient(restarted) as client:
            loaded = client.get('/api/proposals/' + result['proposal_id'], headers=self.headers)
        self.assertEqual(loaded.status_code, 200)
        self.assert_automatic(loaded.json())


if __name__ == '__main__':
    unittest.main()
