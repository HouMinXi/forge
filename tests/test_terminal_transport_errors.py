# SPDX-License-Identifier: Apache-2.0
"""Exercise terminal error handling through real local HTTP requests."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _invoke_vertex, llm_invoke


@contextmanager
def endpoint(body, *, status=200, stream=False, headers=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            calls.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(status)
            self.send_header('Content-Type', 'text/event-stream' if stream else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/v1', calls
        finally:
            server.shutdown()
            thread.join(timeout=2)


@pytest.mark.parametrize('error', [
    {'code': 'context_length_exceeded', 'message': 'too many tokens'},
    'context_length_exceeded',
    {'message': 'max_context_length_exceeded'},
    {'code': '400', 'message': "Requested token count exceeds the model's maximum context length of 524288 tokens."},
    {'message': "Requested token count exceeds the model's maximum context length of 524288 tokens."},
    "Requested token count exceeds the model's maximum context length of 524288 tokens.",
])
@pytest.mark.parametrize('stream', [False, True, 'partial'])
def test_context_error_sends_once(error, stream, monkeypatch):
    body = json.dumps({'error': error}).encode()
    if stream:
        body = b'data: ' + body + b'\n\ndata: [DONE]\n\n'
    if stream == 'partial':
        partial = json.dumps({'choices': [{'delta': {'content': '{"findings":'}}]}).encode()
        body = b'data: ' + partial + b'\n\n' + body
    monkeypatch.setenv('FORGE_CONTEXT_TEST_KEY', 'fixture')
    monkeypatch.setattr('code_forge.llm_invoke.time.sleep', lambda _: None)
    with endpoint(body, stream=bool(stream)) as (url, calls):
        backend = BackendConfig(name='agnes-test', type='api', format='openai',
                                model='fixture', base_url=url, stream=bool(stream),
                                api_key_env='FORGE_CONTEXT_TEST_KEY')
        with pytest.raises(LLMInvokeError) as failure:
            llm_invoke('prompt', backend=backend, max_attempts=3)
        assert len(calls) == 1
        assert failure.value.retryable is False


@pytest.mark.parametrize('status', [400, 401, 403, 404, 429, 500, 502, 503, 504])
def test_vertex_status_over_real_http(status):
    pytest.importorskip('google.auth')
    credentials = MagicMock()
    credentials.token = 'local-fixture'
    backend = BackendConfig(
        name='vertex-fixture', type='api', format='vertex',
        model='claude-fixture', project_id='fixture', region='global',
    )
    with endpoint(b'{"error":{"message":"fixture"}}', status=status,
                  headers={'Retry-After': '7'}) as (url, calls):
        with (
            patch('google.auth.default', return_value=(credentials, 'fixture')),
            patch('google.auth.transport.requests.Request'),
            patch('code_forge.llm_invoke._build_vertex_url', return_value=url),
            pytest.raises(LLMInvokeError) as failure,
        ):
            _invoke_vertex('fixture prompt', backend, 2)
        assert len(calls) == 1
        assert failure.value.exit_code == status
        assert failure.value.retryable is (status in {429, 500, 502, 503, 504})
        assert failure.value.retry_after == 7
