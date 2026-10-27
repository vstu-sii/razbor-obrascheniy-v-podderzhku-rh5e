"""Read and write synthetic data only on an explicitly local, extractive staging."""
import argparse
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import httpx


def audit(project, app_port=18520, monitor_port=18600):
    root=Path(__file__).resolve().parents[1]
    command=['docker','compose','-p',project,'-f',str(root/'compose.yml'),'-f',str(root/'compose.staging.yml')]
    base='http://127.0.0.1:'+str(app_port)
    with httpx.Client(base_url=base,timeout=15,trust_env=False) as client:
        health=client.get('/health').json()
        assert health['infrastructure']=='lab04' and health['baseline_mode']=='extractive'
        def secret(key):
            if key in ('operator_token','service_token','event_secret'):
                code="from backend.bootstrap import configure_database,operator_token; configure_database(); print(operator_token('/data',"+repr(key)+"))"
            else:
                assert key in ('metrics-token','grafana-password')
                code="from pathlib import Path; print(Path('/run/secrets/"+key+"').read_text())"
            return subprocess.check_output(command+['exec','-T','app','python','-c',code],text=True).strip()
        operator=secret('operator_token');headers={'Authorization':'Bearer '+operator}
        def ready(identifier):
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                row=client.get('/api/jobs/'+identifier,headers=headers).json()
                if row['state'] in ('ready','manual_review'):return row
                time.sleep(.2)
            raise AssertionError('No completed staging job')
        body={'request_id':str(uuid4()),'ticket_ref':'lab04-'+str(uuid4()),
              'ticket_text':'Где администратору скачать счёт и акт?', 'history':[],'kb_version':'v1'}
        created=client.post('/api/demo/tickets',json=body,headers=headers)
        assert created.status_code==202,created.text
        row=ready(created.json()['job_id']);assert row['state']=='ready',row
        path='/api/proposals/'+row['proposal_id']
        assert client.post(path+'/reviews',json={'decision':'accepted','confirmed':True,'reason':'Synthetic staging review'},headers=headers).status_code==200
        assert client.get('/metrics',headers=headers).status_code==401
        metrics=client.get('/metrics',headers={'Authorization':'Bearer '+secret('metrics-token')})
        assert metrics.status_code==200 and 'triage_policy_events_total' in metrics.text
        # Exercise the actual ingress hop and its separate HMAC credential.
        external=body|{'ticket_ref':'webhook-'+str(uuid4()),'request_id':str(uuid4())}
        assert client.post('/internal/snapshots',json={'input_version':1,'input':external},headers={'Authorization':'Bearer '+secret('service_token')}).status_code==200
        event={'event_id':str(uuid4()),'event_type':'ticket.created','ticket_ref':external['ticket_ref'],
            'input_version':1,'occurred_at':'2026-10-27T00:00:00Z','kb_version':'v1'}
        raw=json.dumps(event).encode();signature=hmac.new(secret('event_secret').encode(),raw,hashlib.sha256).hexdigest()
        response=client.post('http://127.0.0.1:'+str(app_port+1)+'/events',content=raw,headers={'X-Event-Signature':signature})
        assert response.status_code==202,response.text
        assert ready(response.json()['job_id'])['state']=='ready'
        def endpoint(offset,path,**kwargs):
            response=client.get('http://127.0.0.1:'+str(monitor_port+offset)+path,**kwargs)
            response.raise_for_status();return response.json()
        grafana_auth=('admin',secret('grafana-password'))
        board=endpoint(1,'/api/dashboards/uid/lab04-triage',auth=grafana_auth)
        assert len(board['dashboard']['panels'])==8
        found={}
        for _ in range(50):
            targets=endpoint(0,'/api/v1/targets')['data']['activeTargets']
            found['prometheus']=bool(targets) and all(t['health']=='up' for t in targets)
            alerts=endpoint(2,'/api/v2/alerts')
            found['alertmanager']=any(a['labels'].get('alertname')=='TriageSpendAnomaly' for a in alerts)
            logs=endpoint(3,'/loki/api/v1/query_range',params={'query':'{job="support-triage"}','limit':200})['data']['result']
            content=' '.join(line for stream in logs for _,line in stream['values'])
            found['loki']='model_attempt' in content and 'http_request' in content
            assert operator not in content and body['ticket_text'] not in content
            traces=endpoint(4,'/api/search',params={'q':'{name="model.attempt"}','limit':20}).get('traces',[])
            found['tempo']=bool(traces)
            if all(found.values()): break
            time.sleep(1)
        assert all(found.values()),found
        subprocess.run(command+['restart','app','ai','worker','ingress'],check=True,capture_output=True)
        for _ in range(60):
            try:
                saved=client.get(path,headers=headers)
                if saved.status_code==200:
                    assert saved.json()['reviews'][0]['reason']=='Synthetic staging review';break
            except httpx.HTTPError:pass
            time.sleep(.5)
        else:raise AssertionError('Review did not survive restart')
        return {'scope':'local_extractively_processed_synthetic_staging', 'checks':found,
            'dashboard_panels':len(board['dashboard']['panels']),'card_review_restart':True,
            'signed_ingress':True,'secret_leak_observed':False,'trace_id':traces[0]['traceID'],
            'proposal_id':row['proposal_id'],'public_production_verified':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--project',default='lab04-d015-audit');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assert not args.output.exists(),'Preserve existing evidence'
    result=audit(args.project);args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Staging metrics/logs/traces/alert/ingress/review/restart PASS; no secrets printed')
