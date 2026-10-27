"""Protocol boundary tests with a real local WebSocket fixture, no model calls."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest

from codex_session import CodexSession, SessionError
from websockets.sync.server import unix_serve


@contextmanager
def daemon(mode='confirm'):
    requests = []
    preset = {'model': 'gpt-6.1-sol', 'reasoning': 'high'}

    def handle(ws):
        for raw in ws:
            request = json.loads(raw)
            requests.append(request)
            method = request['method']
            if 'id' not in request:
                continue
            response = {'id': request['id'], 'result': {}}
            if method == 'thread/loaded/list':
                response['result'] = {'data': [] if mode == 'unloaded' else ['target'], 'nextCursor': None}
            elif method == 'thread/resume':
                response['result'] = {'thread': {'id': 'target', 'status': {'type': 'idle'}},
                    'model': 'gpt-6.1-sol', 'reasoningEffort': 'high' if mode == 'unchanged' else 'medium',
                    'cwd': '/tmp/knob-fixture'}
            elif method == 'thread/settings/update':
                if mode == 'reject':
                    response = {'id': request['id'], 'error': {'code': -32602, 'message': 'model unavailable'}}
                if mode == 'before-response':
                    ws.send(json.dumps({'method': 'thread/settings/updated', 'params': {
                        'threadId': 'target', 'threadSettings': {'model': preset['model'], 'effort': 'high'}}}))
                ws.send(json.dumps(response))
                if mode in ('confirm', 'wrong-thread', 'wrong-effort'):
                    ws.send(json.dumps({'method': 'thread/settings/updated', 'params': {
                        'threadId': 'other' if mode == 'wrong-thread' else 'target',
                        'threadSettings': {'model': preset['model'],
                            'effort': 'low' if mode == 'wrong-effort' else 'high'}}}))
                continue
            ws.send(json.dumps(response))

    with tempfile.TemporaryDirectory(prefix='knob-') as directory:
        path = str(Path(directory) / 'daemon.sock')
        with unix_serve(handle, path=path, compression=None) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield path, requests, preset
            finally:
                server.shutdown()
                thread.join(timeout=2)


class SessionTests(unittest.TestCase):
    def test_focus_guard_runs_after_inspection_and_blocks_mutation(self):
        with daemon() as (path, requests, preset):
            def lost_focus():
                self.assertEqual(requests[-1]['method'], 'thread/resume')
                raise RuntimeError('focus changed')
            with CodexSession(sock=path) as session:
                with self.assertRaisesRegex(RuntimeError, 'focus changed'):
                    session.apply('target', preset, before_send=lost_focus)
            self.assertFalse(any(r['method'] == 'thread/settings/update' for r in requests))

    def test_confirmed_update_is_session_scoped_and_does_not_start_turn(self):
        for mode in ('confirm', 'before-response'):
            with self.subTest(mode=mode), daemon(mode) as (path, requests, preset):
                with CodexSession(sock=path, timeout=0.5) as session:
                    result = session.apply('target', preset)
                self.assertEqual((result['threadId'], result['model'], result['effort']),
                                 ('target', 'gpt-6.1-sol', 'high'))
                update = next(r for r in requests if r['method'] == 'thread/settings/update')
                self.assertEqual(update['params'], {'threadId': 'target', 'model': 'gpt-6.1-sol', 'effort': 'high'})
                self.assertEqual(set(r['method'] for r in requests), {
                    'initialize', 'initialized', 'thread/loaded/list', 'thread/resume', 'thread/settings/update'})

    def test_ack_alone_wrong_thread_and_wrong_effort_do_not_confirm(self):
        for mode in ('ack-only', 'wrong-thread', 'wrong-effort'):
            with self.subTest(mode=mode), daemon(mode) as (path, _, preset):
                with CodexSession(sock=path, timeout=0.15) as session:
                    with self.assertRaisesRegex(SessionError, 'unconfirmed'):
                        session.apply('target', preset)

    def test_rejection_is_reported(self):
        with daemon('reject') as (path, _, preset):
            with CodexSession(sock=path, timeout=0.5) as session:
                with self.assertRaisesRegex(SessionError, 'model unavailable'):
                    session.apply('target', preset)

    def test_unloaded_target_is_never_resumed_or_changed(self):
        with daemon('unloaded') as (path, requests, preset):
            with CodexSession(sock=path, timeout=0.5) as session:
                with self.assertRaisesRegex(SessionError, 'not loaded'):
                    session.apply('target', preset)
            self.assertNotIn('thread/resume', [r['method'] for r in requests])
            self.assertNotIn('thread/settings/update', [r['method'] for r in requests])

    def test_already_matching_live_state_needs_no_update_notification(self):
        with daemon('unchanged') as (path, requests, preset):
            with CodexSession(sock=path, timeout=0.5) as session:
                self.assertEqual(session.apply('target', preset)['effort'], 'high')
            self.assertNotIn('thread/settings/update', [r['method'] for r in requests])


if __name__ == '__main__':
    unittest.main()
