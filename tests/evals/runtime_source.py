"""Verify a separately checked-out implementation without publishing other roles."""
import hashlib
import json
import os
from pathlib import Path

def checked_runtime():
    value=os.environ.get('LAB04_RUNTIME_ROOT')
    if not value:raise RuntimeError('Set LAB04_RUNTIME_ROOT to the pinned SIISKI project directory; see tests/README.md')
    project=Path(value).resolve()
    manifest=json.loads((Path(__file__).resolve().parents[1]/'runtime-manifest.json').read_text(encoding='utf-8'))
    for name,expected in manifest['files'].items():
        path=(project/name).resolve()
        if not path.is_relative_to(project) or not path.is_file():raise ValueError('Missing runtime source: '+name)
        actual=hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
        if actual!=expected:raise ValueError('Wrong runtime version: '+name)
    return project
