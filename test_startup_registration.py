"""A normal daemon launch joins its title hint, startup ID, and exact live PID."""
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from focus_binding import Binding, FocusRouter, ReportHub, valid_report
from startup_registration import Registration, discover_launch, process_start, register
from terminal_launcher import interactive_tui, main
from test_focus_binding import THREAD, Provider, until

OTHER = THREAD[:-1] + '1'


class StartupTests(unittest.TestCase):
    def test_plain_resume_does_not_inject_configuration_that_forces_embedded_mode(self):
        with patch('terminal_launcher.launch', return_value=0) as launch, \
                patch('sys.argv', ['codex-knob.py', '--shell', 'resume', THREAD]), \
                patch('os.isatty', return_value=True), \
                patch.dict(os.environ, {'KONSOLE_DBUS_SERVICE': ':1.1'}, clear=True):
            self.assertEqual(main(), 0)
            launch.assert_called_once_with(['codex', 'resume', THREAD], startup_hook=True)

    def test_registration_rejects_wrong_event_partial_id_and_reused_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            pid, start = os.getpid(), process_start(os.getpid())
            payload = {'hook_event_name': 'SessionStart', 'session_id': THREAD, 'source': 'startup'}
            for invalid in ({**payload, 'session_id': THREAD[:29] + '...'},
                            {**payload, 'hook_event_name': 'SubagentStart'}):
                with self.assertRaises(ValueError):
                    register(invalid, pid, start, path)
            with self.assertRaises(ValueError):
                register(payload, pid, 'wrong-start-time', path)
            first, second = Registration(pid, path), Registration(pid, path.with_name('other.json'))
            first.set_hint(THREAD)
            register(payload, pid, start, path)
            self.assertEqual(first.poll(), THREAD)
            self.assertIsNone(first.poll())
            self.assertIsNone(second.poll())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            register({**payload, 'session_id': OTHER}, pid, start, path)
            first.set_hint(OTHER)
            self.assertEqual(first.poll(), OTHER)
            report = {'kind': 'terminal', 'host': 'konsole', 'sessionId': THREAD,
                      'sessionPid': pid, 'childPid': pid}
            self.assertTrue(valid_report(report))
            self.assertFalse(valid_report({**report, 'sessionPid': pid + 1}))
            self.assertFalse(valid_report({**report, 'sessionPid': None}))

    def test_global_hook_joins_only_live_matching_launch_and_rejects_prefix_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one, two = root / 'launch-one', root / 'launch-two'
            one.mkdir(); two.mkdir()
            first, second = Registration(os.getpid(), one / 'session.json'), Registration(os.getpid(), two / 'session.json')
            first.set_hint(THREAD[:29] + '...')
            second.set_hint(OTHER)
            payload = {'hook_event_name': 'SessionStart', 'session_id': THREAD, 'source': 'resume'}
            self.assertEqual(discover_launch(payload, root, timeout=0, loaded_ids=[THREAD, OTHER]), 0)
            self.assertFalse(first.output.exists())
            self.assertEqual(discover_launch(payload, root, timeout=0, loaded_ids=[THREAD]), 1)
            self.assertEqual(first.poll(), THREAD)
            self.assertFalse(second.output.exists())
            value = json.loads((one / 'owner.json').read_text())
            (one / 'owner.json').write_text(json.dumps({**value, 'processStart': 'stale'}))
            self.assertEqual(discover_launch(payload, root, timeout=0, loaded_ids=[THREAD]), 0)

    def test_shell_routes_noninteractive_commands_without_a_pty(self):
        with patch('os.isatty', return_value=True), patch.dict(os.environ, {'KONSOLE_DBUS_SERVICE': ':1.1'}, clear=True):
            for args in ([], ['resume', THREAD], ['fork'], ['hello'], ['-c', 'x="exec"', 'resume']):
                self.assertTrue(interactive_tui(args), args)
            for args in (['--version'], ['exec', 'hello'], ['-c', 'x=1', 'exec', 'hello'],
                         ['app-server'], ['resume', '--help'], ['--enable', 'hooks', 'login']):
                self.assertFalse(interactive_tui(args), args)
        with patch('os.isatty', return_value=False):
            self.assertFalse(interactive_tui([]))

    def test_late_receiver_gets_hook_identity_then_new_session_and_exit_disconnect(self):
        with tempfile.TemporaryDirectory(prefix='knob-startup-') as directory:
            directory = Path(directory)
            child = directory / 'fake-codex.py'
            child.write_text('#!' + sys.executable + '\n' + '''import json,os,subprocess,sys,tty
tty.setraw(0)
assert '-c' not in sys.argv and '--config' not in sys.argv, 'launcher must preserve implicit daemon reuse'
def hook(identity):
    os.write(1,b'\\x1b]0;'+identity.encode()+b'\\x07')
    payload={'session_id':identity,'hook_event_name':'SessionStart','source':'startup'}
    # Daemon hooks have no TUI environment or useful PPID.
    subprocess.run([sys.executable,sys.argv[1],'--discover-launch'],input=json.dumps(payload).encode(),check=True,
                   env={'PATH':os.environ['PATH'],'XDG_RUNTIME_DIR':os.environ['XDG_RUNTIME_DIR']})
hook('01a17215-77af-74a2-9202-99cb92543740')
os.write(1,b'READY')
while os.read(0,1)!=b'n': pass
hook('01a17215-77af-74a2-9202-99cb92543741')
os.write(1,b'CHANGED')
while os.read(0,1)!=b't': pass
os.write(1,b'\\x1b]0;not-a-session\\x07')
while os.read(0,1)!=b'q': pass
''')
            child.chmod(0o700)
            runtime = directory / 'runtime'
            runtime.mkdir(mode=0o700)
            env = {**os.environ, 'XDG_RUNTIME_DIR': str(runtime), 'KONSOLE_DBUS_SERVICE': ':1.123', 'WINDOWID': '10'}
            for key in ('TERM_PROGRAM', 'TMUX', 'STY'):
                env.pop(key, None)
            path = directory / 'binding.sock'
            master, slave = pty.openpty()
            code = 'import sys;from terminal_launcher import launch;sys.exit(launch(sys.argv[2:],sys.argv[1],startup_hook=True))'
            process = subprocess.Popen([sys.executable, '-c', code, str(path), str(child),
                                        str(Path(__file__).with_name('startup_registration.py'))],
                                       stdin=slave, stdout=slave, stderr=slave, env=env)
            hub = None
            output = b''
            try:
                deadline = time.monotonic() + 5
                while b'READY' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'READY', output)
                # The SessionStart happened before the receiver even existed.
                hub = ReportHub(path)
                os.write(master, b'\x1b[I')
                until(lambda: any(r.get('sessionId') == THREAD and r.get('focused') for _, _, r in hub.snapshot()), timeout=3)
                record = hub.snapshot()[0][2]
                self.assertEqual(record['hint'], THREAD)
                self.assertEqual(record['sessionPid'], record['childPid'])
                self.assertNotEqual(record['sessionPid'], process.pid)
                self.assertTrue(valid_report(record))
                binding = Binding(FocusRouter(Provider(), hub))
                binding.refresh()
                self.assertEqual(binding.observation[3], THREAD)
                token = binding.token()
                os.write(master, b'n')
                until(lambda: hub.snapshot()[0][2].get('sessionId') == OTHER)
                self.assertTrue(binding.refresh())
                self.assertNotEqual(binding.token(), token)
                self.assertEqual(binding.observation[3], OTHER)
                os.write(master, b't')
                until(lambda: hub.snapshot()[0][2].get('sessionId') is None)
                self.assertTrue(binding.refresh())
                self.assertIsNone(binding.observation)
                os.write(master, b'q')
                self.assertEqual(process.wait(timeout=3), 0)
                until(lambda: not hub.snapshot())
                self.assertEqual(list(runtime.glob('codex-model-knob-*/launch-*')), [])
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                if hub:
                    hub.close()
                os.close(master)
                os.close(slave)


if __name__ == '__main__':
    unittest.main()
