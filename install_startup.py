#!/usr/bin/env python3
"""Install model-knob's user-layer title/startup hook, preserving other hooks."""
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import time

from codex_session import CodexSession
from startup_registration import hook_definition


def install(session, home=None):
    home = (Path(home) if home else Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))).expanduser().resolve()
    path, config = home / 'hooks.json', home / 'config.toml'
    command = shlex.join([sys.executable, str(Path(__file__).with_name('startup_registration.py').resolve()),
                          '--discover-launch'])
    group, digest = hook_definition(command)
    value = json.loads(path.read_text()) if path.exists() else {'hooks': {}}
    groups = value.setdefault('hooks', {}).setdefault('SessionStart', [])
    index = next((i for i, item in enumerate(groups) if item == group), len(groups))
    if index == len(groups):
        groups.append(group)
    key = f'{path}:session_start:{index}:0'
    backups = []
    for source in (path, config):
        if source.exists():
            backup = source.with_name(source.name + '.model-knob-' + str(time.time_ns()) + '.bak')
            shutil.copy2(source, backup)
            backups.append(str(backup))
    fd, temporary = tempfile.mkstemp(prefix='hooks-', dir=home)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    session.request('config/batchWrite', {
        'filePath': str(config), 'reloadUserConfig': True,
        'edits': [
            {'keyPath': 'tui.terminal_title', 'value': ['thread-id'], 'mergeStrategy': 'upsert'},
            {'keyPath': 'hooks.state.' + json.dumps(key) + '.trusted_hash',
             'value': digest, 'mergeStrategy': 'upsert'},
        ],
    })
    return {'hooksFile': str(path), 'configFile': str(config), 'hookKey': key, 'backups': backups}


if __name__ == '__main__':
    with CodexSession() as session:
        print(json.dumps(install(session), indent=2))
