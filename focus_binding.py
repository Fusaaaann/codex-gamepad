"""OS focus -> host adapter -> terminal observation. No model-setting policy here."""
import ctypes as C
from ctypes.util import find_library
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import threading
import time


def runtime_directory():
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base:
        candidate = Path('/run/user') / str(os.getuid())
        base = candidate if candidate.is_dir() else '/tmp'
    directory = Path(base) / f'codex-model-knob-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f'runtime directory must be owned by you with mode 0700: {directory}')
    return directory


@dataclass(frozen=True)
class Window:
    id: int
    classes: tuple
    pid: int
    role: str = ''


class X11Focus:
    """Read the exact EWMH client window, including a generation for A -> B -> A."""
    def __init__(self):
        self.lock = threading.RLock()
        self.x = C.CDLL(find_library('X11') or 'libX11.so.6')
        self.x.XInitThreads()
        self.x.XOpenDisplay.argtypes = [C.c_char_p]
        self.x.XOpenDisplay.restype = C.c_void_p
        self.x.XDefaultRootWindow.argtypes = [C.c_void_p]
        self.x.XDefaultRootWindow.restype = C.c_ulong
        self.x.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
        self.x.XInternAtom.restype = C.c_ulong
        self.x.XGetWindowProperty.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong,
            C.c_long, C.c_long, C.c_int, C.c_ulong, C.POINTER(C.c_ulong),
            C.POINTER(C.c_int), C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
            C.POINTER(C.POINTER(C.c_ubyte))]
        self.x.XFree.argtypes = [C.c_void_p]
        self.x.XCloseDisplay.argtypes = [C.c_void_p]
        # A window may disappear between the root property and its metadata read.
        self.error_handler = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda *_: 0)
        self.x.XSetErrorHandler.argtypes = [C.c_void_p]
        self.x.XSetErrorHandler(self.error_handler)
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('automatic binding needs an X11 DISPLAY; start it in your desktop session')
        self.root = self.x.XDefaultRootWindow(self.display)
        self.previous = None
        self.generation = 0

    def _property(self, window, name):
        atom = self.x.XInternAtom(self.display, name.encode(), 0)
        actual, fmt, size, after = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong()
        data = C.POINTER(C.c_ubyte)()
        result = self.x.XGetWindowProperty(self.display, window, atom, 0, 1024,
            0, 0, C.byref(actual), C.byref(fmt), C.byref(size), C.byref(after), C.byref(data))
        try:
            if result or not data:
                return b''
            if fmt.value == 32:
                return tuple(C.cast(data, C.POINTER(C.c_ulong))[i] for i in range(size.value))
            return C.string_at(data, size.value) if fmt.value == 8 else b''
        finally:
            if data:
                self.x.XFree(data)

    def snapshot(self):
        with self.lock:
            window = None
            active = self._property(self.root, '_NET_ACTIVE_WINDOW')
            if active and active[0]:
                wid = active[0]
                classes = self._property(wid, 'WM_CLASS')
                pid = self._property(wid, '_NET_WM_PID')
                role = self._property(wid, 'WM_WINDOW_ROLE')
                if isinstance(classes, bytes) and pid:
                    window = Window(wid, tuple(x.lower() for x in classes.decode(errors='replace').split('\0') if x),
                                    pid[0], role.decode(errors='replace').rstrip('\0') if isinstance(role, bytes) else '')
            if window != self.previous:
                self.previous = window
                self.generation += 1
            return self.generation, window

    def close(self):
        with self.lock:
            self.x.XCloseDisplay(self.display)


class ReportHub:
    """Same-user stream connections; disconnects remove observations immediately."""
    def __init__(self, path=None, ttl=2):
        self.path = Path(path) if path else runtime_directory() / 'binding.sock'
        self.ttl = ttl
        self.lock = threading.RLock()
        self.records = {}
        self.counter = 0
        self.stopped = threading.Event()
        self.clients = set()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.server.bind(str(self.path))
            os.chmod(self.path, 0o600)
            self.server.listen(32)
            self.server.settimeout(0.2)
        except Exception:
            self.server.close()
            raise
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                client.close()
                continue
            with self.lock:
                self.counter += 1
                key = self.counter
                self.clients.add(client)
            threading.Thread(target=self._read, args=(key, client), daemon=True).start()

    def _read(self, key, client):
        try:
            with client.makefile('rb') as stream:
                while not self.stopped.is_set():
                    line = stream.readline(16385)
                    if not line or len(line) > 16384 or not line.endswith(b'\n'):
                        break
                    value = json.loads(line)
                    if not valid_report(value):
                        break
                    with self.lock:
                        old = self.records.get(key)
                        revision = (old[1] if old else 0) + (not old or old[0] != value or time.monotonic() - old[2] >= self.ttl)
                        self.records[key] = (value, revision, time.monotonic())
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.records.pop(key, None)
                self.clients.discard(client)
            client.close()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            return [(key, revision, value) for key, (value, revision, received) in self.records.items()
                    if now - received < self.ttl]

    def close(self):
        self.stopped.set()
        self.server.close()
        with self.lock:
            for client in self.clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.thread.join(timeout=1)
        self.path.unlink(missing_ok=True)


class KonsoleAdapter:
    def __init__(self):
        self.initial_cache = {}

    def matches(self, window):
        return 'konsole' in window.classes

    def _initial_focus(self, window, record):
        # Konsole does not send an initial FocusIn when 1004 is enabled. Bootstrap
        # only with an exact WINDOWID + D-Bus window role/session/foreground match.
        service, session = record.get('service') or '', record.get('session') or ''
        role = re.fullmatch(r'MainWindow#(\d+)', window.role)
        if not role or not re.fullmatch(r'(?:org\.kde\.konsole(?:-\d+)?|:\d+\.\d+)', service) or not re.fullmatch(r'/Sessions/\d+', session):
            return False
        key = (window, service, session, record.get('pid'), record.get('childPid'))
        cached = self.initial_cache.get(key)
        if cached and time.monotonic() - cached[0] < 0.15:
            return cached[1]
        def call(path, method):
            out = subprocess.check_output(['gdbus', 'call', '--session', '--dest', service,
                '--object-path', path, '--method', method], text=True, timeout=0.3,
                stderr=subprocess.DEVNULL)
            return int(re.search(r'\((-?\d+),?\)', out).group(1))
        try:
            active = call('/Windows/' + role[1], 'org.kde.konsole.Window.currentSession')
            foreground = call(session, 'org.kde.konsole.Session.foregroundProcessId')
            result = active == int(session.rsplit('/', 1)[1]) and foreground == record.get('pid')
        except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
            result = False
        self.initial_cache = {key: (time.monotonic(), result)}
        return result

    def candidates(self, window, reports):
        result = []
        for key, revision, r in reports:
            if (r.get('kind') != 'terminal' or r.get('host') != 'konsole' or r.get('window') != window.id
                    or not r.get('reporting')):
                continue
            if r.get('focused') is True or (r.get('focused') is None and self._initial_focus(window, r)):
                result.append(((key, revision), r))
        return result


class VSCodeAdapter:
    def matches(self, window):
        return bool(set(window.classes) & {'code', 'code-oss', 'code-insiders'})

    def candidates(self, window, reports):
        # Public API window.state.focused identifies the host instance. PID alone
        # cannot: all VS Code windows may share one Electron main process.
        hosts = [(key, rev, r) for key, rev, r in reports
                 if r.get('kind') == 'vscode' and r.get('focused') is True and r.get('window') == window.id]
        if len(hosts) != 1:
            return []
        host_key, host_rev, host = hosts[0]
        result = []
        for key, rev, r in reports:
            if (r.get('kind') == 'terminal' and r.get('host') == 'vscode' and r.get('focused') is True and r.get('reporting')
                    and host.get('terminalPid') in r.get('ancestors', [])
                    and window.pid in r.get('ancestors', [])):
                result.append(((host_key, host_rev, key, rev), r))
        return result


def valid_report(value):
    if not isinstance(value, dict) or (value.get('focused') is not None and type(value['focused']) is not bool):
        return False
    if value.get('kind') == 'vscode':
        return all(value.get(k) is None or type(value[k]) is int for k in ('terminalPid', 'window'))
    if value.get('kind') != 'terminal' or value.get('host') not in ('konsole', 'vscode'):
        return False
    parents = value.get('ancestors', [])
    return (isinstance(parents, list) and len(parents) <= 100
            and all(type(pid) is int and pid > 0 for pid in parents)
            and all(value.get(k) is None or isinstance(value[k], str) for k in ('service', 'session'))
            and all(value.get(k) is None or type(value[k]) is int for k in ('pid', 'childPid', 'window'))
            and (value.get('sessionId') is None or (valid_hint(value['sessionId']) and not value['sessionId'].endswith('...')))
            and (value.get('sessionPid') is None or (type(value['sessionPid']) is int and value['sessionPid'] == value.get('childPid')))
            and (value.get('sessionId') is None or (type(value.get('sessionPid')) is int and value['sessionPid'] > 0))
            and (value.get('hint') is None or valid_hint(value['hint'])))


class FocusRouter:
    def __init__(self, provider, hub):
        self.provider, self.hub = provider, hub
        self.adapters = (KonsoleAdapter(), VSCodeAdapter())
        self.lock = threading.RLock()

    def observe(self):
        with self.lock:
            generation, window = self.provider.snapshot()
            if window is None:
                return None
            for adapter in self.adapters:
                if adapter.matches(window):
                    candidates = adapter.candidates(window, self.hub.snapshot())
                    if len(candidates) != 1:
                        return None
                    revision, record = candidates[0]
                    hint = record.get('sessionId') or record.get('hint')
                    if not valid_hint(hint):
                        return None
                    return (generation, window, revision, hint)
            return None

    def require(self, observation):
        if self.observe() != observation:
            raise RuntimeError('terminal focus/session changed; selection discarded')


UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
PREFIX = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{5}\.\.\.'


def valid_hint(hint):
    return isinstance(hint, str) and re.fullmatch(f'(?:{UUID}|{PREFIX})', hint) is not None


def resolve_hint(hint, loaded):
    if not valid_hint(hint):
        raise RuntimeError('missing or invalid Codex session identity')
    matches = [item for item in loaded if item.startswith(hint[:-3])] if hint.endswith('...') else [item for item in loaded if item == hint]
    if len(matches) != 1:
        raise RuntimeError('Codex session identity is unloaded or ambiguous')
    return matches[0]


def inspect_observation(observation, sock=None):
    from codex_session import CodexSession
    with CodexSession(sock=sock) as session:
        return session.inspect(resolve_hint(observation[3], session.loaded_ids()))


class Binding:
    """Epoch guards async inspection and sends, even across A -> None -> A."""
    def __init__(self, router):
        self.router = router
        self.observation = None
        self.epoch = 0
        self.current = None

    def refresh(self):
        observation = self.router.observe()
        if observation == self.observation:
            return False
        self.observation = observation
        self.epoch += 1
        self.current = None
        return True

    def token(self):
        return self.epoch, self.observation

    def accept(self, token, state):
        if token != self.token() or token[1] is None:
            return False
        try:
            self.router.require(token[1])
        except RuntimeError:
            return False
        self.current = state
        return True

    def require(self, token):
        if token != self.token() or token[1] is None or self.current is None:
            raise RuntimeError('binding changed; selection discarded')
        self.router.require(token[1])
