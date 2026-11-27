"""User identity is bound to a verified credential, never an input/header actor ID."""
import json
import os
import re
import secrets
from fastapi import HTTPException


def identities(default_token):
    value = os.environ.get('OPERATOR_TOKENS', '')
    if not value:
        return {'demo-operator':default_token}
    try:
        result = json.loads(value)
        if not isinstance(result, dict) or not result:
            raise ValueError()
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key) or not isinstance(token,str) or len(token)<24 for key,token in result.items()):
            raise ValueError()
        if len(set(result.values())) != len(result):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise RuntimeError('Invalid OPERATOR_TOKENS configuration') from None


def authenticate(credentials, tokens):
    if credentials:
        for actor, expected in tokens.items():
            if secrets.compare_digest(credentials.credentials.encode(), expected.encode()):
                return actor
    raise HTTPException(401, 'Operator authentication required')
