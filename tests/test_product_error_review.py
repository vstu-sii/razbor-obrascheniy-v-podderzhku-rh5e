"""Ready product-error answers receive Sol; diagnostics remain on Mistral."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_cascade as fixtures
from backend.worker import step
from ml.cascade.engine import needs_second_opinion


class ProductErrorReviewTests(unittest.TestCase):
    setUp=fixtures.CascadeTests.setUp
    enqueue=fixtures.CascadeTests.enqueue
    status=fixtures.CascadeTests.status
    rows=fixtures.CascadeTests.rows

    def test_ready_product_error_gets_sol_despite_valid_first_card(self):
        job=self.enqueue('Ошибка E42 при импорте CSV, что делать?')
        step(self.queue,self.engine)
        self.assertEqual(self.status(job)['state'],'queued')
        self.assertEqual(self.fake.calls,['mistral'])
        step(self.queue,self.engine)
        self.assertEqual(self.fake.calls,['mistral','sol'])
        self.assertEqual(self.status(job)['state'],'ready')

    def test_product_error_diagnostic_stays_on_free_first_stage(self):
        calls=[]
        def provider(name,req,sources,schema,timeout,trace):
            calls.append(name)
            card=fixtures.api.module.fallback(req['request_id'],'fixture')
            card.update(status='proposed',mode='clarify',category='product_error',draft_reply='Что происходит при импорте?',missing_data=['Описание сбоя'])
            assessment=fixtures.make_assessment(req,[],card)
            assessment['questions'][0]['requested_fact']='observed_behavior'
            return dict(card=card,assessment=assessment,usage={},http_status=200)
        self.engine.provider=provider
        job=self.enqueue('Импорт падает')
        step(self.queue,self.engine)
        self.assertEqual(calls,['mistral'])
        self.assertEqual(self.status(job)['state'],'ready')

    def test_answer_policy_is_selected_by_category_not_punctuation(self):
        req=dict(ticket_text='Кто я????????',history=[])
        self.assertTrue(needs_second_opinion(req,dict(mode='answer',category='product_error')))
        self.assertFalse(needs_second_opinion(req,dict(mode='clarify',category='product_error')))
        self.assertFalse(needs_second_opinion(req,dict(mode='answer',category='settings',draft_reply='Откройте уведомления.')))


if __name__=='__main__':unittest.main()
