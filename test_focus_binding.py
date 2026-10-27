import json
from pathlib import Path
import socket
import tempfile
import time
import unittest

from focus_binding import Binding, FocusRouter, ReportHub, Window, resolve_hint
from terminal_launcher import FocusParser, FocusModeParser, TitleParser

THREAD = '01a17215-77af-74a2-9202-99cb92543740'
PREFIX = THREAD[:29] + '...'


def until(predicate, timeout=1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('condition timed out')


class Provider:
    def __init__(self):
        self.window = Window(10, ('konsole',), 100)
        self.generation = 1

    def snapshot(self):
        return self.generation, self.window


class Reports:
    def __init__(self):
        self.data = [(1, 1, {'kind': 'terminal', 'host': 'konsole', 'window': 10,
                            'focused': True, 'reporting': True, 'hint': PREFIX})]

    def snapshot(self):
        return self.data


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.provider, self.reports = Provider(), Reports()
        self.router = FocusRouter(self.provider, self.reports)

    def test_exact_identity_only_loaded_and_unique(self):
        self.assertEqual(resolve_hint(PREFIX, [THREAD]), THREAD)
        self.assertEqual(resolve_hint(THREAD, [THREAD]), THREAD)
        for hint, loaded in [(PREFIX, []), (PREFIX, [THREAD, THREAD[:-1] + '1']),
                             ('same-project', [THREAD]), (THREAD, [])]:
            with self.subTest(hint=hint, loaded=loaded), self.assertRaises(RuntimeError):
                resolve_hint(hint, loaded)

    def test_os_gate_wrong_window_conflicting_terminals_and_unknown_title_disconnect(self):
        self.assertIsNotNone(self.router.observe())
        self.provider.window = Window(11, ('konsole',), 100)
        self.assertIsNone(self.router.observe())
        self.provider.window = Window(10, ('firefox',), 100)
        self.assertIsNone(self.router.observe())
        self.provider.window = Window(10, ('konsole',), 100)
        self.reports.data.append((2, 1, dict(self.reports.data[0][2])))
        self.assertIsNone(self.router.observe())
        self.reports.data.pop()
        self.reports.data[0][2]['hint'] = None
        self.assertIsNone(self.router.observe())

    def test_vscode_needs_window_metadata_shell_ancestry_and_terminal_focus(self):
        self.provider.window = Window(20, ('code',), 500)
        terminal = {'kind': 'terminal', 'host': 'vscode', 'focused': True,
                    'hint': THREAD, 'reporting': True, 'ancestors': [700, 600, 500]}
        host = {'kind': 'vscode', 'focused': True, 'terminalPid': 600, 'window': 20}
        self.reports.data = [(1, 1, terminal), (2, 1, host)]
        self.assertIsNotNone(self.router.observe())
        self.provider.window = Window(21, ('code',), 500)  # same process, another window
        self.assertIsNone(self.router.observe())
        self.provider.window = Window(20, ('code',), 500)
        terminal['focused'] = False  # editor focused, activeTerminal still populated
        self.assertIsNone(self.router.observe())
        terminal['focused'] = True
        host['terminalPid'] = 601  # another split/tab/plain shell
        self.assertIsNone(self.router.observe())
        host['terminalPid'] = 600
        self.reports.data.append((3, 1, dict(host)))  # stale/conflicting host focus
        self.assertIsNone(self.router.observe())

    def test_disabled_reporting_cannot_leave_a_stale_terminal_binding(self):
        self.reports.data[0][2]['reporting'] = False
        self.assertIsNone(self.router.observe())
        events = []
        parser = FocusModeParser(events.append)
        for byte in b'output\x1b[?1004h\x1b[?1004l\x1b[?1;1004h':
            parser.feed(bytes([byte]))
        self.assertEqual(events, [True, False, True])

    def test_stale_inspect_completion_and_a_away_a_cannot_restore_old_epoch(self):
        binding = Binding(self.router)
        self.assertTrue(binding.refresh())
        old = binding.token()
        state = {'threadId': THREAD}
        self.assertTrue(binding.accept(old, state))
        binding.require(old)
        self.provider.window = None
        with self.assertRaises(RuntimeError):
            binding.require(old)  # worker guard sees focus loss before UI tick
        binding.refresh()
        self.provider.window = Window(10, ('konsole',), 100)
        binding.refresh()
        self.assertFalse(binding.accept(old, state))
        with self.assertRaises(RuntimeError):
            binding.require(old)
        self.assertTrue(binding.accept(binding.token(), state))
        key, rev, record = self.reports.data[0]
        self.reports.data[0] = (key, rev + 1, record)  # pane out/in between ticks
        self.assertTrue(binding.refresh())
        self.assertIsNone(binding.current)

    def test_report_disconnect_expiry_and_heartbeat_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = ReportHub(Path(directory) / 'reports.sock', ttl=0.12)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(hub.path))
                payload = json.dumps(self.reports.data[0][2]).encode() + b'\n'
                client.sendall(payload[:10])
                client.sendall(payload[10:])
                until(lambda: len(hub.snapshot()) == 1)
                first = hub.snapshot()[0][:2]
                client.sendall(payload)
                time.sleep(0.025)
                self.assertEqual(hub.snapshot()[0][:2], first)
                until(lambda: not hub.snapshot())
                client.sendall(payload)
                until(lambda: len(hub.snapshot()) == 1)
                self.assertGreater(hub.snapshot()[0][1], first[1])
                client.close()
                until(lambda: not hub.snapshot())
            finally:
                client.close()
                hub.close()

    def test_split_reports_and_pasted_escape_sequences(self):
        events = []
        parser = FocusParser(events.append)
        stream = b'ordinary\x1b[I\x1b[200~paste\x1b[O\x1b[I\x1b[201~\x1b[O'
        for byte in stream:
            parser.feed(bytes([byte]))
        self.assertEqual(events, [True, False])

    def test_title_updates_new_resume_clear_and_oversize(self):
        events = []
        parser = TitleParser(events.append)
        stream = (b'ordinary\x1b]0;' + PREFIX.encode() + b'\x07' +
                  b'\x1b]2;' + THREAD.encode() + b'\x1b\\' + b'\x1b]0;\x07' +
                  b'\x1b]0;not-a-session\x07' + b'\x1b]0;' + b'x' * 10000 + b'\x1b\\' +
                  b'\x1b]0;' + THREAD.encode() + b'\x07')
        for byte in stream:
            parser.feed(bytes([byte]))
        self.assertEqual(events, [PREFIX, THREAD, None, None, None, THREAD])


if __name__ == '__main__':
    unittest.main()
