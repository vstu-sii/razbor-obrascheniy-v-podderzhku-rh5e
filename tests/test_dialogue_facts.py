"""Typed diagnostic intent and repeated-request regressions without generation."""
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_cascade as fixtures
from ml.cascade.assessment import tool_schema, validate_assessment
from ml.cascade.dialogue import state
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import MODELS, ProviderFailure, parse_response


class DialogueFactsTests(unittest.TestCase):
    setUp = fixtures.CascadeTests.setUp

    def run_question(self, request, kind, label, draft):
        def provider(name,req,sources,schema,timeout,trace):
            card=fixtures.api.module.fallback(req['request_id'],'fixture')
            card.update(status='proposed',mode='clarify',missing_data=[label],draft_reply=draft)
            assessment=fixtures.make_assessment(req,[],card)
            assessment['questions'][0]['requested_fact']=kind
            assessment['questions'][0]['missing_customer_fields']=['Описание нужного факта, свободная формулировка']
            return dict(card=card,assessment=assessment,usage={},http_status=200)
        return CascadeEngine(self.engine.kb,fixtures.api.SCHEMA,fixtures.api.provenance_gate,provider).analyze_attempt(request,'mistral',30,'job','attempt')

    def test_field_paraphrases_do_not_route_a_valid_diagnostic_to_sol(self):
        for text,kind,label,draft in (
            ('Импорт падает','error_code','Код ошибки, который возникает при падении импорта.','Какую ошибку вы наблюдаете при падении импорта?'),
            ('Не могу войти','observed_behavior','Описание наблюдаемого поведения при попытке входа','Опишите, пожалуйста, что именно не получается при входе.'),
        ):
            request=dict(request_id='id',ticket_ref='t',ticket_text=text,history=[],kb_version='v1')
            result=self.run_question(request,kind,label,draft)
            self.assertTrue(result['automatic_resolution'],result['gate_reason'])
            self.assertFalse(result['cascade_next'])

    def test_unavailable_code_is_not_requested_again_but_another_fact_is_allowed(self):
        request=dict(request_id='id',ticket_ref='t',ticket_text='После переписки: ошибка списания за сентябрь.',kb_version='v1',
                     history=[dict(speaker='operator',text='Нужны период списания и код ошибки.'),
                              dict(speaker='customer',text='Сентябрь, не август; код ошибки неизвестен.')])
        repeated=self.run_question(request,'error_code','Код ошибки','Какой код ошибки?')
        self.assertEqual(repeated['gate_reason'],'assessment_repeated_diagnostic')
        alternative=self.run_question(request,'observed_behavior','Суть ошибки','Что именно произошло со списанием за сентябрь?')
        self.assertTrue(alternative['automatic_resolution'],alternative['gate_reason'])

    def test_new_customer_code_updates_unavailable_state_and_operator_examples_are_not_facts(self):
        req=dict(ticket_text='Теперь показывает E42.',history=[dict(speaker='customer',text='Код ошибки неизвестен.')])
        self.assertEqual(state(req)['facts']['error_code']['value'],'e42')
        req=dict(ticket_text='Импорт падает.',history=[dict(speaker='operator',text='Какой код ошибки, например E42?')])
        self.assertEqual(state(req)['facts'],{})
        self.assertEqual(state(req)['previously_requested'],['error_code'])

    def test_tool_requires_enum_intent_without_changing_public_card(self):
        schema=tool_schema(fixtures.api.SCHEMA)
        question=schema['properties']['assessment']['properties']['questions']['items']
        self.assertIn('requested_fact',question['required'])
        self.assertIn('observed_behavior',question['properties']['requested_fact']['enum'])
        self.assertNotIn('requested_fact',schema['properties']['card']['properties'])

    def test_typed_intent_does_not_authorize_requesting_secrets(self):
        req=dict(request_id='id',ticket_ref='t',ticket_text='API возвращает ошибку',history=[],kb_version='v1')
        result=self.run_question(req,'observed_behavior','Значение токена','Пришлите сам токен для проверки.')
        self.assertFalse(result['automatic_resolution'])

    def test_active_transport_rejects_omitted_fact_type_but_archives_remain_readable(self):
        envelope=dict(card=dict(summary='Сохранённый текст'),assessment=dict(questions=[{}]))
        response=dict(model=MODELS['mistral'],usage=dict(prompt_tokens=10,completion_tokens=10),
                      choices=[dict(finish_reason='tool_calls',message=dict(tool_calls=[dict(type='function',
                          function=dict(name='propose_card',arguments=json.dumps(envelope)))]))])
        self.assertIsInstance(parse_response(response,'mistral')['card'],dict)
        with self.assertRaises(ProviderFailure) as error:
            parse_response(response,'mistral',request_id='current')
        self.assertEqual(error.exception.reason,'missing_requested_fact')


if __name__=='__main__':unittest.main()
