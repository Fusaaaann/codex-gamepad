# Source this file from an interactive Bash/Zsh startup file.
# Python resolves the executable through PATH; shell functions cannot recurse.
codex() {
    /home/user/File-System/Bots/gamepad-codex/.venv/bin/python \
        /home/user/File-System/Bots/gamepad-codex/codex-knob.py --shell "$@"
}
