"""A PTY owner, never a second reader attached to an existing Codex terminal."""
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import socket
import sys
import termios
import threading
import time
import tty
import tempfile

from focus_binding import runtime_directory, valid_hint
from startup_registration import Registration


class FocusParser:
    """Observe reports without changing bytes or treating bracketed paste as focus."""
    def __init__(self, callback):
        self.callback = callback
        self.pending = b''
        self.pasting = False

    def feed(self, data):
        data = self.pending + data
        self.pending = b''
        tokens = (b'\x1b[201~',) if self.pasting else (b'\x1b[I', b'\x1b[O', b'\x1b[200~')
        while data:
            matches = [(data.find(t), t) for t in tokens if data.find(t) >= 0]
            if not matches:
                for size in range(min(len(data), 5), 0, -1):
                    if any(t.startswith(data[-size:]) for t in tokens):
                        self.pending = data[-size:]
                        break
                return
            index, token = min(matches)
            data = data[index + len(token):]
            if token == b'\x1b[200~':
                self.pasting = True
            elif token == b'\x1b[201~':
                self.pasting = False
            else:
                self.callback(token == b'\x1b[I')
            tokens = (b'\x1b[201~',) if self.pasting else (b'\x1b[I', b'\x1b[O', b'\x1b[200~')


class TitleParser:
    """Observe OSC 0/2 only; all output is still forwarded unchanged."""
    def __init__(self, callback):
        self.callback = callback
        self.state = 'text'
        self.payload = bytearray()

    def feed(self, data):
        for byte in data:
            if self.state == 'text':
                if byte == 27:
                    self.state = 'escape'
            elif self.state == 'escape':
                self.state = 'osc' if byte == 93 else ('escape' if byte == 27 else 'text')
                self.payload.clear()
            elif self.state in ('osc', 'osc-escape'):
                if byte == 7 or (self.state == 'osc-escape' and byte == 92):
                    payload = bytes(self.payload)
                    if payload.startswith((b'0;', b'2;')):
                        title = payload[2:].decode('utf-8', errors='replace').strip()
                        self.callback(title if valid_hint(title) else None)
                    self.state = 'text'
                    self.payload.clear()
                elif byte == 27:
                    self.state = 'osc-escape'
                elif len(self.payload) < 4096:
                    if self.state == 'osc-escape':
                        self.payload.append(27)
                    self.payload.append(byte)
                    self.state = 'osc'
                else:
                    if self.payload.startswith((b'0;', b'2;')):
                        self.callback(None)
                    self.state = 'discard'
                    self.payload.clear()
            elif self.state == 'discard':
                if byte == 7:
                    self.state = 'text'
                elif byte == 27:
                    self.state = 'discard-escape'
            elif self.state == 'discard-escape':
                self.state = 'text' if byte in (7, 92) else 'discard'


class FocusModeParser:
    """Disconnect while a child has disabled focus reporting (e.g. external editor)."""
    def __init__(self, callback):
        self.callback = callback
        self.pending = b''

    def feed(self, data):
        data = self.pending + data
        for match in re.finditer(rb'\x1b\[\?([0-9;]+)([hl])', data):
            if b'1004' in match[1].split(b';'):
                self.callback(match[2] == b'h')
        # Only retain an incomplete bounded CSI; never terminal contents.
        match = re.search(rb'\x1b(?:\[(?:\?[0-9;]{0,100})?)?$', data)
        self.pending = match[0] if match else b''


def ancestors(pid):
    result = []
    for _ in range(100):
        if pid <= 1 or pid in result:
            break
        result.append(pid)
        try:
            status = Path(f'/proc/{pid}/stat').read_text()
            pid = int(status[status.rfind(')') + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return result


class Reporter:
    def __init__(self, state, path=None):
        self.state = state
        self.path = path or runtime_directory() / 'binding.sock'
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def update(self, **values):
        with self.lock:
            if any(self.state.get(k) != v for k, v in values.items()):
                self.state.update(values)
                # Included in heartbeats so out -> in between receiver reads still
                # invalidates a pending selection even if the final state is equal.
                self.state['sequence'] = self.state.get('sequence', 0) + 1
                self.changed.set()

    def _run(self):
        while not self.stopped.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(0.5)
                    client.connect(str(self.path))
                    while not self.stopped.is_set():
                        self.changed.clear()
                        with self.lock:
                            value = dict(self.state)
                        client.sendall(json.dumps(value).encode() + b'\n')
                        self.changed.wait(0.3)
            except OSError:
                self.stopped.wait(0.3)

    def close(self):
        self.stopped.set()
        self.changed.set()
        self.thread.join(timeout=1.5)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        view = view[count:]


def launch(command, report_path=None, startup_hook=False):
    if not os.isatty(0) or not os.isatty(1):
        raise RuntimeError('launch Codex from an interactive terminal')
    if os.environ.get('TMUX') or os.environ.get('STY'):
        raise RuntimeError('tmux/screen need their own host adapter; use a direct Konsole or VS Code terminal')
    host = 'vscode' if os.environ.get('TERM_PROGRAM') == 'vscode' else (
        'konsole' if os.environ.get('KONSOLE_DBUS_SERVICE') else None)
    if host is None:
        raise RuntimeError('supported terminal hosts: Konsole and VS Code')
    if host == 'konsole':
        # Konsole's %n tab field reads the outer foreground process's comm.
        # Keep OSC titles available for session identity; label the PTY owner.
        Path('/proc/self/comm').write_text('Codex\n')
    try:
        window = int(os.environ.get('WINDOWID', '0'), 0)
    except ValueError:
        window = 0
    original = termios.tcgetattr(0)
    output_flags = fcntl.fcntl(1, fcntl.F_GETFL)
    registration_directory = tempfile.TemporaryDirectory(prefix='launch-', dir=runtime_directory()) if startup_hook else None
    registration_path = Path(registration_directory.name) / 'session.json' if startup_hook else None
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execvp(command[0], command)
        except OSError as exc:
            os.write(2, f'codex-knob: {exc}\r\n'.encode())
            os._exit(127)
    reporter = Reporter({'kind': 'terminal', 'host': host, 'window': window,
        'service': os.environ.get('KONSOLE_DBUS_SERVICE'),
        'session': os.environ.get('KONSOLE_DBUS_SESSION'), 'pid': os.getpid(),
        'childPid': pid, 'ancestors': ancestors(os.getpid()),
        'focused': None, 'hint': None, 'reporting': True, 'sequence': 0}, report_path)
    registration = Registration(pid, registration_path) if startup_hook else None
    focus = FocusParser(lambda value: reporter.update(focused=value))
    def title_changed(value):
        # A /new or /resume title transition must invalidate the previous hook
        # identity before the next SessionStart arrives.
        current = reporter.state.get('sessionId')
        matches = current and value and current.startswith(value.removesuffix('...'))
        if registration:
            registration.set_hint(value)
        reporter.update(hint=value, sessionId=current if matches else None)
    title = TitleParser(title_changed)
    modes = FocusModeParser(lambda enabled: reporter.update(reporting=enabled, focused=None))
    handlers = {}

    def resize(*_):
        try:
            fcntl.ioctl(master, termios.TIOCSWINSZ, fcntl.ioctl(0, termios.TIOCGWINSZ, b'\0' * 8))
        except OSError:
            pass

    def forward(signum, _):
        try:
            os.killpg(pid, signum)
        except ProcessLookupError:
            pass

    def enter():
        tty.setraw(0)
        resize()
        write_all(1, b'\x1b[?1004h')

    def leave():
        try:
            write_all(1, b'\x1b[?1004l')
        finally:
            termios.tcsetattr(0, termios.TCSANOW, original)

    child_status = None
    try:
        for sig in (signal.SIGWINCH, signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT):
            handlers[sig] = signal.signal(sig, resize if sig == signal.SIGWINCH else forward)
        enter()
        os.set_blocking(master, False)
        os.set_blocking(1, False)
        pending_input, pending_output = bytearray(), bytearray()
        master_eof = False
        input_eof = False
        while True:
            if registration:
                identity = registration.poll()
                if identity:
                    reporter.update(sessionId=identity, sessionPid=pid)
            readers = []
            if not input_eof and not master_eof and len(pending_input) < 1024 * 1024:
                readers.append(0)
            if not master_eof and len(pending_output) < 1024 * 1024:
                readers.append(master)
            writers = ([master] if pending_input and not master_eof else []) + ([1] if pending_output else [])
            ready, writable, _ = select.select(readers, writers, [], 0.05)
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except BlockingIOError:
                    data = None
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    data = b''
                if data == b'':
                    master_eof = True
                    pending_input.clear()
                elif data:
                    title.feed(data)
                    modes.feed(data)
                    pending_output.extend(data)
            if 0 in ready:
                try:
                    data = os.read(0, 65536)
                except BlockingIOError:
                    data = None
                if data == b'':
                    input_eof = True
                    forward(signal.SIGHUP, None)
                elif data:
                    focus.feed(data)
                    pending_input.extend(data)
            for fd, buffer in ((master, pending_input), (1, pending_output)):
                if fd in writable and buffer:
                    try:
                        count = os.write(fd, buffer)
                        del buffer[:count]
                    except BlockingIOError:
                        pass
                    except OSError as exc:
                        if fd != master or exc.errno != errno.EIO:
                            raise
                        pending_input.clear()
            if master_eof and not pending_output:
                break
            if child_status is None:
                waited, status = os.waitpid(pid, os.WNOHANG | os.WUNTRACED)
                if waited:
                    if os.WIFSTOPPED(status):
                        reporter.update(focused=None, reporting=False)
                        reporter.close()  # FIN before stopping; a heartbeat cannot run while stopped.
                        fcntl.fcntl(1, fcntl.F_SETFL, output_flags)
                        write_all(1, pending_output)
                        pending_output.clear()
                        leave()
                        os.kill(os.getpid(), signal.SIGSTOP)
                        os.killpg(pid, signal.SIGCONT)
                        reporter = Reporter({**reporter.state, 'focused': None, 'reporting': True}, report_path)
                        enter()
                        os.set_blocking(1, False)
                    else:
                        child_status = status
                        reporter.close()
        if child_status is None:
            forward(signal.SIGHUP, None)
            _, child_status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(child_status)
        return code if code >= 0 else 128 - code
    finally:
        reporter.close()
        try:
            os.close(master)
        finally:
            try:
                if child_status is None:
                    forward(signal.SIGHUP, None)
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline:
                        waited, _ = os.waitpid(pid, os.WNOHANG)
                        if waited:
                            break
                        time.sleep(0.02)
                    else:
                        forward(signal.SIGKILL, None)
                        os.waitpid(pid, 0)
                fcntl.fcntl(1, fcntl.F_SETFL, output_flags)
                leave()
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
                if registration_directory:
                    registration_directory.cleanup()


def main():
    args = sys.argv[1:]
    shell_mode = bool(args and args[0] == '--shell')
    if shell_mode:
        args = args[1:]
    if not shell_mode and args == ['--help']:
        print('Usage: codex-knob.py [Codex arguments]\nLaunches Codex with focus/session reporting. Use inside Konsole or VS Code.')
        return 0
    executable = os.environ.get('CODEX_KNOB_EXECUTABLE', 'codex')
    if shell_mode and not interactive_tui(args):
        os.execvp(executable, [executable, *args])
    # Even a UI-only -c override disables implicit shared-daemon reuse in Codex
    # 0.164. Title and hook settings belong in the normal user config layer.
    return launch([executable, *args], startup_hook=True)


def interactive_tui(args):
    """Wrap only interactive TUI invocations in supported local terminals."""
    if (not os.isatty(0) or not os.isatty(1) or os.environ.get('TMUX') or os.environ.get('STY')
            or not (os.environ.get('KONSOLE_DBUS_SERVICE') or os.environ.get('TERM_PROGRAM') == 'vscode')):
        return False
    options_with_value = {'-c', '--config', '-m', '--model', '-p', '--profile', '-s', '--sandbox',
                          '-a', '--ask-for-approval', '-C', '--cd', '--add-dir', '-i', '--image',
                          '--enable', '--disable', '--remote-auth-token-env', '--service-tier', '--oss-provider'}
    commands = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'mcp-server', 'app-server',
                'app', 'completion', 'sandbox', 'debug', 'apply', 'a', 'cloud', 'features',
                'help', 'update', 'exec-server', 'agents', 'plugin', 'remote-control',
                'doctor', 'queue', 'archive', 'delete', 'migrate-rollouts', 'migrate-nfs-db', 'unarchive'}
    before_separator = args[:args.index('--')] if '--' in args else args
    if any(arg in ('--help', '-h', '--version', '-V') for arg in before_separator):
        return False
    if any(arg == '--remote' or arg.startswith('--remote=') for arg in before_separator):
        return False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--':
            break
        if arg in ('--help', '-h', '--version', '-V'):
            return False
        if arg in options_with_value:
            index += 2
            continue
        if not arg.startswith('-'):
            return arg not in commands
        index += 1
    return True
