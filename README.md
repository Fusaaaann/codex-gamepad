# Codex Model Knob

Select a Codex CLI model and reasoning effort with a VIA/QMK rotary encoder.
The switcher follows the focused Codex terminal in Konsole or VS Code on X11,
including terminals in VS Code editor groups.

Turn to choose a preset. After 500 ms without rotation, the switcher applies the
selection and confirms it with the local Codex daemon. The overlay shows the
candidate without taking keyboard focus.

## Setup

Requires Python 3 with Tk, an X11 desktop, and Codex CLI using its shared local
daemon. The protocol was tested with Codex 0.164.0.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-knob.txt
```

With the shared Codex daemon running, install the session title and startup hook:

```bash
.venv/bin/python install_startup.py
```

The installer backs up the existing configuration and preserves other hooks.
Edit the two absolute paths in `codex-shell.sh` to match your checkout, then
source it in the terminal's parent shell:

```bash
source /path/to/gamepad-codex/codex-shell.sh
codex
```

VS Code also requires the local focus adapter:

```bash
python3 vscode-adapter/package-vsix.py
code --install-extension vscode-adapter/gamepad-codex-focus-0.1.0.vsix
```

See the [setup guide](docs/model-knob.md#automatic-session-binding) for shell
startup configuration, explicit launcher commands, and troubleshooting.

## Usage

Start the switcher from another terminal in the same desktop session:

```bash
.venv/bin/python model-knob.py
```

Map encoder rotation to `KC_F13` / `KC_F14` in VIA and bind desktop shortcuts to
`python3 /path/to/gamepad-codex/model-knob.py prev` / `next`. Optionally map the
encoder press to `KC_F15` and the `cancel` command. Edit `model-presets.json` to
change the ordered model/effort pairs.

For preview without changing a session:

```bash
.venv/bin/python model-knob.py serve
```

Stop either mode with `python3 model-knob.py stop`. Live updates affect subsequent
turns and do not start inference. Wayland, tmux/screen, remote terminals, and
embedded Codex servers are outside automatic binding support.

## Documentation

- [Model knob setup and usage](docs/model-knob.md)
- [Focus and session binding maintenance notes](docs/agent-notes/focus-binding.md)
- [Desktop integration diagnostics](experiments/README.md)

## Development

```bash
.venv/bin/python -m unittest discover -p 'test_*.py'
```

Tests use local Unix sockets and PTYs. Desktop integration checks and their
additional dependencies are described in the diagnostics guide.
