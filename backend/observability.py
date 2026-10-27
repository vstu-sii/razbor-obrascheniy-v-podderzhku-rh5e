"""Payload-free JSON logs, low-cardinality metrics and OTLP/HTTP spans."""
from collections import Counter
from contextlib import contextmanager
import contextvars
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time
import httpx

TRACE = contextvars.ContextVar('trace', default=None)
SPAN = contextvars.ContextVar('span', default=None)
LOCK = threading.Lock()
REQUESTS = Counter()
LATENCY = Counter()
BUCKETS = (.01,.05,.1,.5,1,5,30,float('inf'))
LOG = logging.getLogger('triage.telemetry')


def configure():
    if LOG.handlers:
        return
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    formatter = logging.Formatter('%(message)s')
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOG.addHandler(console)
    directory = os.environ.get('LOG_DIRECTORY')
    if directory:
        Path(directory).mkdir(parents=True,exist_ok=True)
        service = re.sub('[^a-z0-9_-]', '', os.environ.get('SERVICE_NAME','app')) or 'app'
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(Path(directory)/(service+'.jsonl'),maxBytes=5000000,backupCount=2,encoding='utf-8')
        handler.setFormatter(formatter)
        LOG.addHandler(handler)


def emit(name, **fields):
    configure()
    safe = {key:value for key,value in fields.items() if key in ('outcome','reason','route','status','duration_ms','provider','attempt')}
    LOG.info(json.dumps({'event':name,'service':os.environ.get('SERVICE_NAME','app'),
                         'timestamp':time.time(),'trace_id':TRACE.get(),**safe}))


@contextmanager
def span(name, attributes=None, trace_id=None, parent_id=None):
    trace_id = trace_id or TRACE.get() or secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    marker = TRACE.set(trace_id)
    parent_id = parent_id or SPAN.get()
    span_marker = SPAN.set(span_id)
    started = time.time_ns()
    try:
        yield '00-'+trace_id+'-'+span_id+'-01'
    finally:
        endpoint = os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT', '').rstrip('/')
        if endpoint:
            attrs = [{'key':key,'value':{'stringValue':str(value)}} for key,value in (attributes or {}).items()]
            payload = {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':os.environ.get('SERVICE_NAME','app')}}]},
                       'scopeSpans':[{'scope':{'name':'support-triage'},'spans':[{'traceId':trace_id,'spanId':span_id,
                       'name':name,'kind':1,'startTimeUnixNano':str(started),'endTimeUnixNano':str(time.time_ns()),'attributes':attrs}]}]}]}
            if parent_id: payload['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['parentSpanId']=parent_id
            try:
                with httpx.Client(timeout=2,trust_env=False) as client:
                    client.post(endpoint+'/v1/traces',json=payload).raise_for_status()
            except Exception:
                emit('telemetry_export', outcome='unavailable')
        TRACE.reset(marker)
        SPAN.reset(span_marker)


class TelemetryMiddleware:
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope,receive,send)
        headers = dict(scope.get('headers', []))
        incoming = headers.get(b'traceparent',b'').decode('ascii',errors='ignore')
        trace = incoming.split('-')[1] if re.fullmatch(r'00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]', incoming) else secrets.token_hex(16)
        status = 500
        async def observe(message):
            nonlocal status
            if message['type']=='http.response.start': status=message['status']
            await send(message)
        start = time.perf_counter()
        # Spans omit paths, params, headers, bodies and exception strings.
        attributes={}
        with span('http.request', attributes, trace_id=trace, parent_id=incoming.split('-')[2] if len(incoming.split('-'))==4 else None):
            try:
                await self.app(scope,receive,observe)
            finally:
                route = getattr(scope.get('route'),'path','unmatched')
                attributes.update({'http.route':route,'http.status_code':status})
                elapsed = time.perf_counter()-start
                with LOCK:
                    REQUESTS[(route,str(status))] += 1
                    LATENCY[('sum',route)] += elapsed
                    LATENCY[('count',route)] += 1
                    for bound in BUCKETS:
                        if elapsed<=bound: LATENCY[(str(bound),route)] += 1
                emit('http_request',route=route,status=status,duration_ms=round(elapsed*1000,3))


def prometheus():
    lines = ['# TYPE triage_http_requests_total counter','# TYPE triage_http_duration_seconds histogram']
    with LOCK:
        for (route,status), count in REQUESTS.items():
            lines.append('triage_http_requests_total{route='+json.dumps(route)+',status='+json.dumps(status)+'} '+str(count))
        for route in {r for r,_ in REQUESTS}:
            for bound in BUCKETS:
                label = '+Inf' if bound==float('inf') else str(bound)
                lines.append('triage_http_duration_seconds_bucket{route='+json.dumps(route)+',le="'+label+'"} '+str(LATENCY[(str(bound),route)]))
            for part in ('sum','count'):
                lines.append('triage_http_duration_seconds_'+part+'{route='+json.dumps(route)+'} '+str(LATENCY[(part,route)]))
    return lines
