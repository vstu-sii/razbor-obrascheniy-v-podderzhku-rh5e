"""Regression checks for downstream AI errors; no real API or model required."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location(
    'backend_stub', Path(__file__).resolve().parents[1] / 'services/stub/main.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class BackendErrors(unittest.TestCase):
    def check_error(self, body, expected_code, expected_payload):
        handler = Mock()
        handler.path = '/v1/analyze'
        handler.headers = {'Content-Length': '2'}
        handler.rfile = io.BytesIO(b'{}')
        failure = HTTPError('http://ai/v1/analyze', 502, 'Bad gateway', {}, io.BytesIO(body))
        with patch.object(backend, 'SERVICE', 'backend'), \
             patch.dict(backend.os.environ, {'AI_URL': 'http://ai'}), \
             patch.object(backend.request, 'urlopen', side_effect=failure):
            backend.Handler.do_POST(handler)
        handler.respond.assert_called_once_with(expected_code, expected_payload)

    def test_html_error_falls_back_to_manual_review(self):
        self.check_error(b'<html>Bad gateway</html>', 503,
                         {'error': 'ai_unavailable', 'next_step': 'manual_review'})

    def test_json_error_keeps_status_and_payload(self):
        self.check_error(json.dumps({'error': 'upstream_failure'}).encode(), 502,
                         {'error': 'upstream_failure'})


if __name__ == '__main__':
    unittest.main()
