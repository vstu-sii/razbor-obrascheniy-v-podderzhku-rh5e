"""Shared constant-time token verification, including malformed Unicode input."""
import secrets
from fastapi import HTTPException


def require_token(credentials, expected):
    if not expected or not credentials or not secrets.compare_digest(credentials.credentials.encode('utf-8'), expected.encode('utf-8')):
        raise HTTPException(401, 'Authentication required')
