#!/usr/bin/env python3
"""Launch Codex with automatic model-knob binding."""
import sys
from terminal_launcher import main

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        sys.exit(f'codex-knob: {exc}')
