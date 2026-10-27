"""Pair a daemon SessionStart ID with a live PTY launcher's PID and title hint.

The normal user hook reads private launcher ownership records. Injecting hook
configuration with CLI -c flags would make Codex 0.164 use an embedded server,
which the switcher cannot reach. Never use hook PPID as the TUI's PID.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time

from focus_binding import resolve_hint, runtime_directory, valid_hint


def process_start(pid):
    value = Path(f'/proc/{pid}/stat').read_text()
    return value[value.rfind(')') + 2:].split()[19]


def hook_definition(command):
    handler = {'type': 'command', 'command': command, 'timeout': 3, 'async': False}
    identity = {'event_name': 'session_start', 'hooks': [handler]}
    canonical = json.dumps(identity, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    digest = 'sha256:' + hashlib.sha256(canonical.encode()).hexdigest()
    return {'hooks': [handler]}, digest


def hook_flags(pid, start, output):
    """Direct-invocation fixture helper; production launches must not inject these."""
    command = shlex.join([sys.executable, str(Path(__file__).resolve()),
                          '--pid', str(pid), '--start', start, '--output', str(output)])
    _, digest = hook_definition(command)
    group = '[{ hooks = [{ type = "command", command = ' + json.dumps(command) + ', timeout = 3 }] }]'
    key = '/<session-flags>/config.toml:session_start:0:0'
    return ['-c', 'hooks.SessionStart=' + group,
            '-c', 'hooks.state={' + json.dumps(key) + '={trusted_hash=' + json.dumps(digest) + '}}']


def register(payload, pid, start, output):
    if (not isinstance(payload, dict) or payload.get('hook_event_name') != 'SessionStart'
            or not valid_hint(payload.get('session_id')) or payload['session_id'].endswith('...')):
        raise ValueError('expected a SessionStart payload with a full Codex session ID')
    if process_start(pid) != start or Path(f'/proc/{pid}').stat().st_uid != os.getuid():
        raise ValueError('launch process exited or PID was reused')
    value = {'sessionId': payload['session_id'], 'pid': pid, 'processStart': start,
             'source': payload.get('source')}
    output = Path(output)
    fd, temporary = tempfile.mkstemp(prefix=output.name + '.', dir=output.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Registration:
    def __init__(self, pid, output):
        self.pid, self.output = pid, Path(output)
        self.start = process_start(pid)
        self.previous = None
        self.hint = None
        self.set_hint(None)

    def set_hint(self, hint):
        self.hint = hint if valid_hint(hint) else None
        owner = self.output.with_name('owner.json')
        value = {'pid': self.pid, 'processStart': self.start, 'hint': self.hint}
        temporary = owner.with_suffix('.tmp')
        temporary.write_text(json.dumps(value))
        temporary.chmod(0o600)
        temporary.replace(owner)

    def poll(self):
        try:
            raw = self.output.read_bytes()
            if raw == self.previous:
                return None
            value = json.loads(raw)
            if (value.get('pid') != self.pid or value.get('processStart') != self.start
                    or process_start(self.pid) != self.start
                    or not valid_hint(value.get('sessionId')) or value['sessionId'].endswith('...')
                    or not self.hint or not value['sessionId'].startswith(self.hint.removesuffix('...'))):
                return None
            self.previous = raw
            return value['sessionId']
        except (OSError, ValueError, AttributeError):
            return None


def discover_launch(payload, directory=None, timeout=1, loaded_ids=None):
    if (not isinstance(payload, dict) or payload.get('hook_event_name') != 'SessionStart'
            or not valid_hint(payload.get('session_id')) or payload['session_id'].endswith('...')):
        raise ValueError('expected a SessionStart payload with a full Codex session ID')
    directory = Path(directory) if directory else runtime_directory()
    deadline = time.monotonic() + timeout
    while True:
        matches = []
        for owner in directory.glob('launch-*/owner.json'):
            try:
                value = json.loads(owner.read_text())
                if not isinstance(value, dict):
                    continue
                hint, pid, start = value.get('hint'), value['pid'], value['processStart']
                if (not valid_hint(hint) or type(pid) is not int or pid <= 1
                        or process_start(pid) != start):
                    continue
                if hint.endswith('...'):
                    if loaded_ids is None:
                        from codex_session import CodexSession
                        with CodexSession(timeout=0.5) as session:
                            loaded_ids = session.loaded_ids()
                    identity = resolve_hint(hint, loaded_ids)
                else:
                    identity = hint
                if identity == payload['session_id']:
                    matches.append((owner, pid, start))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                continue
        for owner, pid, start in matches:
            register(payload, pid, start, owner.with_name('session.json'))
        if matches or time.monotonic() >= deadline:
            return len(matches)
        time.sleep(0.025)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover-launch', action='store_true')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--start')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if args.discover_launch:
            discover_launch(payload)
        else:
            if args.pid is None or args.start is None or args.output is None:
                parser.error('provide --discover-launch or all of --pid, --start, --output')
            register(payload, args.pid, args.start, args.output)
    except (OSError, ValueError) as exc:
        print(f'codex-knob registration: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
