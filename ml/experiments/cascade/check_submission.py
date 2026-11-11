"""AI-only smoke checks: real HTTP/schema and controlled provider, no external calls."""
from pathlib import Path
import sys
import os
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from ml.contracts.validation import SCHEMA,read_jsonl,provenance_gate
from ml.models.baseline import Baseline
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import ProviderFailure
from backend.routers.ai import create_app


def main():
    checks=0
    with patch.dict(os.environ,DEMO_OPERATOR_TOKEN='synthetic-offline-check',BASELINE_MODE='extractive',TRACE_PATH=os.devnull):
        with TestClient(create_app()) as client:
            assert client.get('/health').json()['baseline_mode']=='extractive';checks+=1
            req=dict(request_id='smoke',ticket_ref='smoke',ticket_text='Где скачать счёт?',history=[],kb_version='support-20261112')
            assert client.post('/v1/analyze',json=req).status_code==401;checks+=1
            headers={'Authorization':'Bearer synthetic-offline-check'}
            assert client.post('/v1/analyze',json=dict(req,history=[{'speaker':'invalid','text':'x'}]),headers=headers).status_code==422;checks+=1
            response=client.post('/v1/analyze',json=req,headers=headers)
            assert response.status_code==200 and response.json()['mode']=='answer';checks+=1
    kb=read_jsonl(ROOT/'ml/data/cascade/kb.jsonl');calls=[]
    def controlled(provider,req,sources,schema,timeout,trace):
        calls.append(provider)
        if provider=='mistral':raise ProviderFailure('controlled_transport_failure')
        card=Baseline([],SCHEMA,provenance_gate).extractive(req,sources)
        assessment={'questions':[dict(id='q1',question=req['ticket_text'],input_quote=req['ticket_text'],coverage='complete',
            source_ids=[s['source_id'] for s in sources],missing_customer_fields=[],answer_excerpt=card['draft_reply'],search_query='')],
            'requires_human':False,'reasoning_issue':False}
        return dict(card=card,assessment=assessment,usage={'prompt_tokens':1,'completion_tokens':1},http_status=200)
    engine=CascadeEngine(kb,SCHEMA,provenance_gate,controlled)
    first=engine.analyze_attempt(req,'mistral',25,'offline-job','offline-1')
    assert first['cascade_next'] and not first['automatic_resolution'];checks+=1
    second=engine.analyze_attempt(req,'sol',25,'offline-job','offline-2',stage_context=first['stage_context'])
    assert second['automatic_resolution'] and second['final_card']['status']=='resolved' and calls==['mistral','sol'];checks+=1
    bad=dict(second['final_card']);bad['evidence']=[dict(bad['evidence'][0],excerpt='invented')]
    assert provenance_gate(bad,req['request_id'],req['kb_version'],second['stage_context']['sources'])==(False,'evidence_provenance');checks+=1
    calls.clear()
    danger=dict(req,ticket_text='Токен опубликован в публичном чате.')
    stopped=engine.analyze_attempt(danger,'mistral',25,'offline-risk','offline-risk')
    assert not calls and stopped['not_sent'] and stopped['final_card']['draft_reply'] is None;checks+=1
    try:engine.analyze(req)
    except ValueError:checks+=1
    else:raise AssertionError('Unreserved cascade must not run')
    print('AI-only checks passed:',checks,'external model calls: 0')


if __name__=='__main__':main()
