"""Real nested PTYs: forwarding, resize, exit status, reports and terminal restore."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from focus_binding import ReportHub
from test_focus_binding import THREAD, until


class LauncherTests(unittest.TestCase):
    def test_child_suspend_disconnects_and_restores_terminal_until_continue(self):
        with tempfile.TemporaryDirectory(prefix='knob-stop-') as directory:
            directory = Path(directory)
            child = directory / 'stop.py'
            child.write_text('''import os,signal,time,tty
tty.setraw(0)
os.write(1,b'\\x1b]0;' + b'01a17215-77af-74a2-9202-99cb92543740' + b'\\x07READY')
os.read(0,1)
os.write(1,b'BEFORE\\x1b[?1004l')
os.kill(os.getpid(),signal.SIGSTOP)
os.write(1,b'AFTER')
time.sleep(.1)
''')
            hub = ReportHub(directory / 'binding.sock')
            master, slave = pty.openpty()
            original = termios.tcgetattr(slave)
            env = {**os.environ, 'KONSOLE_DBUS_SERVICE': ':1.123'}
            for key in ('TERM_PROGRAM', 'TMUX', 'STY'):
                env.pop(key, None)
            process = subprocess.Popen([sys.executable, '-c',
                'import sys;from terminal_launcher import launch;sys.exit(launch(sys.argv[2:],sys.argv[1]))',
                str(hub.path), sys.executable, str(child)],
                stdin=slave, stdout=slave, stderr=slave, env=env)
            output = b''
            try:
                deadline = time.monotonic() + 5
                while b'READY' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'READY', output)
                until(lambda: bool(hub.snapshot()))
                os.write(master, b'g')
                stopped = False
                while time.monotonic() < deadline:
                    waited, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
                    if waited and os.WIFSTOPPED(status):
                        stopped = True
                        break
                    time.sleep(.01)
                self.assertTrue(stopped)
                self.assertEqual(termios.tcgetattr(slave), original)
                until(lambda: not hub.snapshot())
                os.kill(process.pid, signal.SIGCONT)
                self.assertEqual(process.wait(timeout=5), 0)
                while select.select([master], [], [], .05)[0]:
                    output += os.read(master, 65536)
                self.assertIn(b'BEFORE', output)
                self.assertIn(b'AFTER', output)
                self.assertEqual(termios.tcgetattr(slave), original)
                until(lambda: not hub.snapshot())
            finally:
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGCONT)
                    process.terminate()
                    process.wait(timeout=3)
                os.close(master)
                os.close(slave)
                hub.close()

    def test_large_full_duplex_paste_does_not_deadlock_or_drop_bytes(self):
        with tempfile.TemporaryDirectory(prefix='knob-duplex-') as directory:
            child = Path(directory) / 'echo.py'
            payload = b'0123456789abcdef' * (128 * 1024)
            child.write_text('''import os,sys,tty
tty.setraw(0)
os.write(1,b'BEGIN')
remaining=int(sys.argv[1])
while remaining:
    data=os.read(0,min(4096,remaining))
    remaining-=len(data)
    while data:
        sent=os.write(1,data)
        data=data[sent:]
os.write(1,b'END')
''')
            master, slave = pty.openpty()
            env = {**os.environ, 'KONSOLE_DBUS_SERVICE': ':1.123'}
            for key in ('TERM_PROGRAM', 'TMUX', 'STY'):
                env.pop(key, None)
            process = subprocess.Popen([sys.executable, '-c',
                'import sys;from terminal_launcher import launch;sys.exit(launch(sys.argv[2:],sys.argv[1]))',
                str(Path(directory) / 'absent.sock'), sys.executable, str(child), str(len(payload))],
                stdin=slave, stdout=slave, stderr=slave, env=env)
            os.set_blocking(master, False)
            sent, output = 0, bytearray()
            try:
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    writable = [master] if b'BEGIN' in output and sent < len(payload) else []
                    ready, write, _ = select.select([master], writable, [], 0.05)
                    if master in ready:
                        output.extend(os.read(master, 65536))
                    if master in write:
                        sent += os.write(master, payload[sent:sent + 65536])
                    if b'END' in output and process.poll() is not None:
                        break
                self.assertEqual(process.wait(timeout=1), 0)
                self.assertEqual(sent, len(payload))
                body = bytes(output).split(b'BEGIN', 1)[1].split(b'END', 1)[0]
                self.assertEqual(body, payload)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                os.close(master)
                os.close(slave)

    def test_pty_preserves_bytes_resize_exit_and_terminal_state(self):
        with tempfile.TemporaryDirectory(prefix='knob-pty-') as directory:
            directory = Path(directory)
            child = directory / 'child.py'
            received = directory / 'received.json'
            payload = b'\x1b[I\x1b[200~pasted\x1b[O\x1b[201~\x00\xff\r\n'
            child.write_text('''import fcntl,json,os,struct,sys,termios,tty
tty.setraw(0)
os.write(1, b'\\x1b[?1004h\\x1b]0;' + sys.argv[2].encode() + b'\\x07READY')
data=b''
while len(data)<int(sys.argv[3]):
    data+=os.read(0, 4096)
size=struct.unpack('HHHH', fcntl.ioctl(0,termios.TIOCGWINSZ,b'\\0'*8))
with open(sys.argv[1],'w') as f: json.dump({'hex':data.hex(),'size':size[:2]},f)
os.write(1,b'CHILD-OUTPUT\\x00\\xff')
sys.exit(7)
''')
            hub = ReportHub(directory / 'binding.sock')
            master, slave = pty.openpty()
            original = termios.tcgetattr(slave)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 33, 99, 0, 0))
            environment = {**os.environ, 'KONSOLE_DBUS_SERVICE': 'org.kde.konsole-123',
                           'KONSOLE_DBUS_SESSION': '/Sessions/1', 'WINDOWID': '10'}
            environment.pop('TERM_PROGRAM', None)
            environment.pop('TMUX', None)
            environment.pop('STY', None)
            code = ('import sys; from terminal_launcher import launch; '
                    'sys.exit(launch(sys.argv[2:], sys.argv[1]))')
            process = subprocess.Popen([sys.executable, '-c', code, str(hub.path),
                sys.executable, str(child), str(received), THREAD, str(len(payload))],
                stdin=slave, stdout=slave, stderr=slave, env=environment)
            output = b''
            try:
                deadline = time.monotonic() + 5
                while b'READY' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'READY', output)
                until(lambda: any(r[2].get('hint') == THREAD for r in hub.snapshot()))
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 48, 130, 0, 0))
                os.kill(process.pid, signal.SIGWINCH)
                time.sleep(0.03)
                os.write(master, payload)
                self.assertEqual(process.wait(timeout=5), 7)
                while select.select([master], [], [], 0.05)[0]:
                    output += os.read(master, 65536)
                result = json.loads(received.read_text())
                self.assertEqual(bytes.fromhex(result['hex']), payload)
                self.assertEqual(result['size'], [48, 130])
                self.assertIn(b'CHILD-OUTPUT\x00\xff', output)
                self.assertTrue(output.endswith(b'\x1b[?1004l'))
                self.assertEqual(termios.tcgetattr(slave), original)
                until(lambda: not hub.snapshot())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                os.close(master)
                os.close(slave)
                hub.close()


if __name__ == '__main__':
    unittest.main()
