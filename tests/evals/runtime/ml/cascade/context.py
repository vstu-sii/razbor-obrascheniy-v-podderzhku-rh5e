"""Persist a bounded, immutable approved knowledge snapshot before inference."""
import copy
import hashlib
import json

MAX_SNAPSHOT_BYTES = 262144
CONTEXT_VERSION = 'semantic-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def make_context(kb, request):
    fields = ('source_id', 'version', 'status', 'title', 'excerpt')
    snapshot = [{key: source[key] for key in fields} for source in kb
                if source.get('status') == 'approved' and source.get('version') == request['kb_version']]
    snapshot.sort(key=lambda source: source['source_id'])
    if (len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > MAX_SNAPSHOT_BYTES
            or len({source['source_id'] for source in snapshot}) != len(snapshot)):
        raise ValueError('Knowledge snapshot is too large or ambiguous')
    return {'context_version': CONTEXT_VERSION, 'input_hash': digest(request),
            'kb_version': request['kb_version'], 'kb_hash': digest(snapshot),
            'kb_snapshot': snapshot, 'sources': [], 'questions': [], 'retrieval': {}}


def checked_context(context, request):
    """Reject mixed input/KB snapshots; never fetch context from caller text."""
    if (not isinstance(context, dict) or context.get('context_version') != CONTEXT_VERSION
            or context.get('input_hash') != digest(request)
            or context.get('kb_version') != request['kb_version']):
        raise ValueError('Cascade context does not match the reserved input')
    snapshot = context.get('kb_snapshot')
    if not isinstance(snapshot, list):
        raise ValueError('Knowledge snapshot missing')
    canonical = make_context(snapshot, request)
    if canonical['kb_hash'] != context.get('kb_hash') or canonical['kb_snapshot'] != snapshot:
        raise ValueError('Knowledge snapshot mismatch')
    allowed = {source['source_id']: source for source in snapshot}
    sources = context.get('sources', [])
    questions = context.get('questions', [])
    if (not isinstance(sources, list) or len(sources) > len(snapshot)
            or any(not isinstance(source, dict)
                   or not isinstance(source.get('source_id'), str)
                   or allowed.get(source['source_id']) != source
                   for source in sources)
            or len({(source['source_id'], source['version']) for source in sources}) != len(sources)
            or not isinstance(questions, list) or len(questions) > 4
            or any(not isinstance(question, dict) for question in questions)
            or not isinstance(context.get('retrieval', {}), dict)):
        raise ValueError('Invalid stage context')
    return copy.deepcopy(context)
