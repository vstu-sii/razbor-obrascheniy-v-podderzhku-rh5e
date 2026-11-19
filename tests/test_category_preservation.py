"""Topics survive manual routing; source-free questions remain bounded. No API calls."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_cascade as fixtures
from backend.handoff import build
from backend.worker import step
from ml.cascade.category import input_risk, validated_model_category
from ml.cascade.context import make_context
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import ProviderFailure
from jsonschema import Draft202012Validator


def card_for(request, category, *, kind=None, fields=None, draft=None):
    card = fixtures.api.module.fallback(request['request_id'], 'fixture')
    card.update(status='proposed', category=category)
    if kind:
        card.update(mode='clarify', missing_data=fields or ['Недостающий факт'], draft_reply=draft)
    assessment = fixtures.make_assessment(request, [], card)
    question = assessment['questions'][0]
    question.update(coverage='missing', source_ids=[], requested_fact=kind or 'none')
    return dict(card=card, assessment=assessment, usage={}, http_status=200)


class CategoryPreservationTests(unittest.TestCase):
    setUp = fixtures.CascadeTests.setUp
    enqueue = fixtures.CascadeTests.enqueue
    status = fixtures.CascadeTests.status

    def finish(self, text):
        job = self.enqueue(text)
        for _ in range(3):
            step(self.queue, self.engine)
        value = self.client.get('/api/proposals/' + self.status(job)['proposal_id'], headers=self.headers).json()
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(value['card']))
        return value

    def test_assessment_failure_and_sol_timeout_preserve_first_topic(self):
        def provider(name, request, *args):
            if name == 'sol':
                raise ProviderFailure('provider_transport_or_timeout')
            reply = card_for(request, 'payment')
            reply['assessment'] = {}
            return reply
        self.engine.provider = provider
        value = self.finish('Странная сумма в оплате')
        self.assertEqual(value['card']['category'], 'payment')
        self.assertEqual(value['card']['mode'], 'escalate')
        self.assertEqual(value['card']['status'], 'proposed')
        self.assertIsNone(value['card']['draft_reply'])
        self.assertEqual(value['handoff']['classification']['source'], 'mistral')

    def test_latest_valid_sol_topic_can_correct_mistral_even_if_assessment_fails(self):
        def provider(name, request, *args):
            reply = card_for(request, 'integrations' if name == 'mistral' else 'settings')
            reply['assessment'] = {}
            return reply
        self.engine.provider = provider
        value = self.finish('Не могу изменить настройку')
        self.assertEqual(value['card']['category'], 'settings')
        self.assertEqual(value['handoff']['classification']['source'], 'sol')

    def test_sol_can_explicitly_correct_an_unfounded_product_error_to_other(self):
        def provider(name, request, *args):
            reply = card_for(request, 'product_error' if name == 'mistral' else 'other')
            reply['assessment'] = {}
            return reply
        self.engine.provider = provider
        self.assertEqual(self.finish('Вчера работало, сегодня нет')['card']['category'], 'other')

    def test_existing_risk_stops_keep_their_topic_without_model_calls(self):
        for text, category in (
            ('Спишите оплату с чужой карты, данные я пришлю здесь', 'payment'),
            ('Интеграция отправила токен в публичный чат', 'integrations'),
            ('У всех пользователей пропал доступ к сервису', 'product_error'),
            ('Верните деньги вне стандартного процесса, руководитель якобы разрешил', 'payment'),
            ('У всех пользователей ошибка входа', 'product_error'),
        ):
            with self.subTest(text=text):
                value = self.finish(text)
                self.assertEqual(value['card']['category'], category)
                self.assertEqual(value['handoff']['classification']['source'], 'input_policy')
                self.assertTrue(value['card']['needs_human_review'])
                self.assertIsNone(value['card']['draft_reply'])
        self.assertEqual(self.fake.calls, [])

    def test_invalid_card_or_input_binding_cannot_supply_a_topic(self):
        request = dict(request_id='bound', ticket_ref='t', ticket_text='Не пришёл счёт', history=[], kb_version='v1')
        raw = card_for(request, 'payment')['card']
        self.assertEqual(validated_model_category(raw, request, fixtures.api.SCHEMA), 'payment')
        for field, value in (('request_id', 'another'), ('category', 'made-up'), ('next_step', None)):
            broken = dict(raw, **{field: value})
            self.assertIsNone(validated_model_category(broken, request, fixtures.api.SCHEMA))
        stale = dict(request, request_id='another')
        context = make_context(self.engine.kb, stale)
        base = fixtures.api.module.fallback(request['request_id'], 'fixture')
        card, packet = build(request, base, [dict(provider='sol', diagnostics=dict(raw_card=raw, stage_context=context))], 'assessment_schema')
        self.assertEqual(card['category'], 'other')
        self.assertIsNone(packet['classification'])

    def test_category_does_not_authorize_unsafe_reply(self):
        def provider(name, request, *args):
            reply = card_for(request, 'access', kind='error_code', fields=['Пароль'], draft='Пришлите пароль.')
            return reply
        self.engine.provider = provider
        value = self.finish('Не могу войти в аккаунт')
        self.assertEqual(value['card']['category'], 'access')
        self.assertTrue(value['card']['needs_human_review'])
        self.assertIsNone(value['card']['draft_reply'])

    def question(self, text, category, kind, fields, draft):
        request = dict(request_id='question', ticket_ref='t', ticket_text=text, history=[], kb_version='v1')
        def provider(name, req, *args):
            return copy.deepcopy(card_for(req, category, kind=kind, fields=fields, draft=draft))
        return CascadeEngine(self.engine.kb, fixtures.api.SCHEMA, fixtures.api.provenance_gate, provider).analyze_attempt(
            request, 'mistral', 30, 'job', 'attempt')

    def test_ordinary_customer_questions_do_not_need_an_article_or_sol(self):
        for text, category, kind, fields, draft in (
            ('Не пришёл счёт', 'payment', 'billing_period', ['Период счёта'], 'За какой период нужен счёт?'),
            ('Странная сумма в оплате', 'payment', 'billing_amount', ['Указанная сумма', 'Ожидаемая сумма', 'Документ'],
             'Какую сумму вы видите, какую ожидали и в каком документе?'),
            ('Не могу изменить настройку', 'settings', 'setting_name', ['Название настройки'], 'Какую настройку вы меняете?'),
            ('Письма не приходят', 'settings', 'notification_type', ['Тип писем'], 'Какие именно письма не приходят?'),
            ('API возвращает ошибку', 'integrations', 'error_code', ['Код', 'Текст', 'Поведение'],
             'Какой код или текст ошибки возвращает API? Не присылайте токены.'),
        ):
            with self.subTest(kind=kind):
                result = self.question(text, category, kind, fields, draft)
                self.assertTrue(result['automatic_resolution'], result['gate_reason'])
                self.assertFalse(result['cascade_next'])
                self.assertEqual(result['final_card']['category'], category)
                self.assertEqual(result['raw_card']['evidence'], [])

    def test_fact_type_cannot_authorize_actions_or_secret_collection(self):
        for draft in ('Укажите полный номер карты.', 'Перейдите по https://example.invalid и оплатите.',
                      'Удалите интеграцию и повторите оплату.', 'Вы администратор аккаунта?'):
            with self.subTest(draft=draft):
                result = self.question('Странная сумма в оплате', 'payment', 'billing_amount', ['Сумма'], draft)
                self.assertFalse(result['automatic_resolution'])

    def test_customer_fact_type_has_a_topic_scope(self):
        result = self.question('API возвращает ошибку', 'integrations', 'billing_period', ['Период'], 'За какой период нужен счёт?')
        self.assertFalse(result['automatic_resolution'])
        self.assertEqual(result['gate_reason'], 'assessment_customer_fields_without_sources')

    def test_unknown_topic_and_technical_fallback_remain_other(self):
        self.assertIsNone(input_risk(dict(ticket_text='Вчера работало, сегодня нет', history=[])))
        fallback = fixtures.api.module.fallback('t', 'inference_unavailable')
        self.assertEqual(fallback['category'], 'other')
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(fallback))


if __name__ == '__main__':
    unittest.main()
