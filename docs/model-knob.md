# Model knob for Codex CLI

Use a VIA/QMK rotary encoder to select a model and reasoning effort for the
focused Codex terminal. Automatic binding supports local Konsole and VS Code
terminals on X11, including VS Code terminals in editor groups.

Each detent moves one row through the presets, wrapping at either end. The
centered overlay highlights the candidate without taking keyboard focus. After
500 ms without rotation, the switcher applies the final selection. Preview stays
visible for 700 ms; a confirmed live update stays visible for 1.5 seconds.
An optional encoder press cancels a pending selection.

## Requirements

- Python 3 with Tk (`python3-tk` on Debian/Ubuntu).
- An X11 desktop session; the overlay and automatic binding require X11.
- Codex CLI using its shared local daemon for live updates.
- Konsole, or VS Code with the local focus adapter for automatic binding.
- `xprop` for the VS Code adapter and `gdbus` for Konsole focus discovery.

The daemon protocol and hook format were tested with Codex 0.164.0; compatibility
with other versions is unverified. Wayland, tmux/screen, remote terminals,
`--no-daemon`, and CLI `-c` config overrides are outside automatic binding support.

Commands below run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-knob.txt
```

## Encoder and desktop shortcuts

Map the encoder in VIA, then bind its keys to desktop global shortcuts. Replace
`/path/to/gamepad-codex` with the absolute path to your checkout.

| Encoder action | VIA Any keycode | Desktop shortcut command |
|---|---|---|
| Counterclockwise | `KC_F13` | `python3 /path/to/gamepad-codex/model-knob.py prev` |
| Clockwise | `KC_F14` | `python3 /path/to/gamepad-codex/model-knob.py next` |
| Press, optional | `KC_F15` | `python3 /path/to/gamepad-codex/model-knob.py cancel` |

Rotation needs no modifier or Enter key. Firmware must expose encoder mappings
in VIA. An equivalent QMK mapping for one encoder on one layer is:

```c
// rules.mk: ENCODER_MAP_ENABLE = yes
#if defined(ENCODER_MAP_ENABLE)
const uint16_t PROGMEM encoder_map[][NUM_ENCODERS][NUM_DIRECTIONS] = {
    [0] = { ENCODER_CCW_CW(KC_F13, KC_F14) }
};
#endif
```

Map the push switch to `KC_F15` in the normal matrix keymap. See the QMK
[keycode reference](https://docs.qmk.fm/keycodes) and
[encoder map documentation](https://docs.qmk.fm/features/encoders) for board setup.

## Preview

```bash
.venv/bin/python model-knob.py serve
```

Turn the knob, or run `python3 model-knob.py next` in another terminal. Preview
shows the overlay and prints the selected `/model` command without sending it.
`serve --delay 1` increases the settle delay to one second.

```bash
python3 model-knob.py stop
```

Only one switcher server runs per user. Passing `serve` explicitly selects
preview; a bare invocation starts live automatic binding.

## Automatic session binding

With the shared Codex daemon running, install the user-layer title setting and
startup hook once:

```bash
.venv/bin/python install_startup.py
```

The installer backs up `config.toml` and `hooks.json` in `CODEX_HOME` (default
`~/.codex`), sets `tui.terminal_title=["thread-id"]`, and adds and trusts the exact
local registration command. Existing hooks are preserved. It does not change
model defaults or permission profiles. Rerun it if the checkout or Python
environment moves, or after checking compatibility with a new Codex version.

Edit the two absolute paths in `codex-shell.sh` to match your checkout, then
source it **in the terminal shell**, after exiting any running Codex:

```bash
source /path/to/gamepad-codex/codex-shell.sh
type -t codex  # Bash: should print "function"
codex
# Or: codex resume SESSION_ID
```

Add the source command to your interactive shell startup file for future
terminals. Sourcing through a Codex tool only changes its child shell; it cannot
install the function in the parent terminal. Existing Codex processes need to
be relaunched after setup.

The function wraps interactive local Konsole/VS Code launches. Noninteractive
commands, help/version, remote connections, and unsupported hosts pass through
to the executable. `command codex` bypasses the function. To launch explicitly:

```bash
.venv/bin/python codex-knob.py
.venv/bin/python codex-knob.py resume SESSION_ID
```

`CODEX_KNOB_EXECUTABLE=/absolute/path/to/codex` selects a custom executable.
The launcher preserves Codex arguments and uses a PTY to report focus and session
identity. In normal Konsole shell launches its process name is `Codex`, so a tab
format such as `%d : %n` displays `project : Codex`. Direct `konsole -e python ...`
launches may retain Konsole's cached initial process name.

For VS Code, also install the local focus adapter:

```bash
python3 vscode-adapter/package-vsix.py
code --install-extension vscode-adapter/gamepad-codex-focus-0.1.0.vsix
```

Reload the VS Code window if the adapter has not activated. It uses window-focus
and terminal-process metadata without reading terminal output. Panel terminals,
splits, and terminal editor groups are supported. A normal text editor is not a
target, even if VS Code remembers an active terminal.

Start the switcher from the X11 desktop session:

```bash
.venv/bin/python model-knob.py
# Equivalent: .venv/bin/python model-knob.py serve --send --auto --delay 0.5
```

Stop a running preview server first with `python3 model-knob.py stop`. A missing
socket means no server is running. F13/F14 shortcuts continue using `prev` and
`next`; only the server needs the virtual environment.

Focus a registered Codex terminal to control that chat. Moving to another
registered terminal changes the target. Leaving it clears the binding, hides
the overlay, and cancels pending rotation. Missing or ambiguous identity also
disconnects. No per-chat bind command or session-ID copy is needed.

## Pinned sessions and explicit updates

To control one loaded session regardless of desktop focus, copy its ID from
Codex `/status`:

```bash
.venv/bin/python model-knob.py serve --send --thread SESSION_ID
.venv/bin/python model-knob.py status --thread SESSION_ID
```

Restart with a different ID to change the pinned target. The `status` command
reads effective settings without sending a prompt.

Other commands:

```bash
.venv/bin/python model-knob.py sessions
.venv/bin/python model-knob.py apply --thread SESSION_ID --index 1
```

Preset indexes start at 1. `sessions` lists loaded IDs and basic metadata without
conversation contents. `--sock PATH` selects another daemon socket; the default
uses `CODEX_HOME`, falling back to `~/.codex`.

## Presets and update behavior

Edit `model-presets.json` to change the ordered model/effort pairs. The bundled
presets are Sol xhigh, high, and medium (`gpt-6.1-sol`), then Luna xhigh and high
(`gpt-6-luna`). These IDs reflect the local model cache used during development;
availability depends on the Codex installation and account.

Preview starts at the first row on a clockwise detent and the last on a
counterclockwise detent. Live mode reads the target's current pair and rotates
from a matching preset. Failed or unconfirmed updates do not advance the
committed selection.

The overlay shows **Applying**, then **Applied** after daemon confirmation of
the exact thread, model, and effort. An already-matching snapshot confirms a
no-op. Acknowledgement alone does not confirm a change. Failure or timeout shows
**Unconfirmed** and prints the reason in the server terminal; inspect `/status`
before retrying, since an unconfirmed update may have taken effect.

Settings affect subsequent turns, not an in-progress request. The switcher does
not start inference. Confirmation verifies configuration, not model access or a
completed response. A request already sent cannot be canceled; rotation during
an update keeps only the latest pending candidate. Manual changes after binding
are not continuously synchronized to the overlay; restart to reread them.

## Troubleshooting

Inspect routing without a prompt or settings change:

```bash
python3 model-knob.py binding
python3 model-knob.py diagnostics
```

Diagnostics contain the active X11 window and producer metadata, not terminal
contents.

| Symptom | Check |
|---|---|
| No terminal reports | Check `type -t codex` in the parent Bash shell, then relaunch through the function or explicit launcher. |
| VS Code terminal report but no host report | Install or enable the focus adapter; both report types are required. |
| Identity visible but session unavailable | Check that the thread is loaded on the shared daemon. CLI `-c` options and `--no-daemon` can select an inaccessible embedded server. |
| No startup registration file yet | Codex 0.164 can defer `SessionStart` until the first turn; a unique loaded title hint supports idle binding. |
| Session environment variables exist but no binding | `CODEX_THREAD_ID`/`CODEX_SESSION_ID` inside a tool do not register the parent terminal. Check the launcher and host reports. |

On some X11 keymaps, F13/F14 encoder events appear as `XF86Tools` and
`XF86Launch5`. If an event inspector confirms keycodes 191/192 on your system,
a session-scoped remap is:

```bash
xmodmap -e 'keycode 191 = F13' -e 'keycode 192 = F14'
```

KDE shortcut schemes configure actions rather than this key translation.
Record each shortcut by rotating once while its recorder is active.

If a crash leaves a stale socket, confirm the switcher has exited before
removing it. Runtime sockets are `control.sock` and `binding.sock` under
`$XDG_RUNTIME_DIR/codex-model-knob-UID`. Without `XDG_RUNTIME_DIR`, the directory
uses `/run/user/UID` if available, otherwise `/tmp`.

## Development

Run the automated suite from the repository root:

```bash
.venv/bin/python -m unittest discover -p 'test_*.py'
```

The tests use local Unix sockets and PTYs. See the
[binding maintenance notes](agent-notes/focus-binding.md) for architecture,
regression constraints, and validation limits, and the
[desktop diagnostics](../experiments/README.md) for integration checks.
