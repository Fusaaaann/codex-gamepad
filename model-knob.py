#!/usr/bin/env python3
"""X11 model switcher.

No arguments start live auto-focus mode. Global shortcuts send next/prev events.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from codex_session import CodexSession, apply_preset
from focus_binding import Binding, FocusRouter, ReportHub, X11Focus, inspect_observation, runtime_directory


def load_presets(path):
    presets = json.loads(Path(path).read_text())
    if not isinstance(presets, list) or not presets:
        raise ValueError("preset list must be nonempty")
    for p in presets:
        if not isinstance(p, dict) or not all(
            isinstance(p.get(k), str) and re.fullmatch(r"[A-Za-z0-9_.-]+", p[k])
            for k in ("model", "reasoning")
        ):
            raise ValueError("each preset needs a plain model ID and reasoning level")
        p.setdefault("label", f"{p['model']} · {p['reasoning']}")
        if not isinstance(p['label'], str):
            raise ValueError("preset labels must be strings")
    return presets


def command(preset):
    return f"/model {preset['model']} --reasoning {preset['reasoning']}"


class Selection:
    """Debounce chooses a candidate; only an acknowledgement commits it."""
    def __init__(self, size, delay):
        self.size, self.delay = size, delay
        self.committed = self.pending = None
        self.deadline = None

    def rotate(self, direction, now):
        base = self.pending if self.pending is not None else self.committed
        self.pending = (base + direction) % self.size if base is not None else (
            0 if direction > 0 else self.size - 1
        )
        self.deadline = now + self.delay

    def cancel(self):
        self.pending = self.deadline = None

    def settle(self, now):
        if self.deadline is None or now < self.deadline:
            return None
        selected = self.pending
        self.cancel()
        return selected


def socket_path():
    return runtime_directory() / "control.sock"


def serve(args, presets):
    import tkinter as tk
    path = socket_path()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    # Fail on a stale socket too: never steal another switcher's address.
    server.bind(str(path))
    os.chmod(path, 0o600)
    server.setblocking(False)
    hub = provider = binding = None
    try:
        if args.auto:
            provider = X11Focus()
            hub = ReportHub()
            binding = Binding(FocusRouter(provider, hub))
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#e9bcae", padx=2, pady=2)
        panel = tk.Frame(root, bg="#242321", padx=20, pady=16)
        panel.pack(fill="both", expand=True)
        tk.Label(panel, text="MODEL / REASONING", fg="#e9bcae", bg="#242321",
                 font=("sans", 10, "bold")).pack(anchor="w", pady=(0, 10))
        rows = []
        for p in presets:
            row = tk.Label(panel, text="  " + p["label"], anchor="w", padx=12, pady=9,
                           bg="#242321", fg="#b9b5b1", font=("sans", 15))
            row.pack(fill="x", pady=2)
            rows.append(row)
        footer = tk.Label(panel, bg="#242321", fg="#aaa5a0", font=("sans", 10))
        footer.pack(anchor="w", pady=(12, 0))
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry(f"{w}x{h}+{(root.winfo_screenwidth()-w)//2}+{(root.winfo_screenheight()-h)//2}")
        selection = Selection(len(presets), args.delay)
        if args.send and not args.auto:
            selection.committed = next((i for i, p in enumerate(presets)
                if p['model'] == args.current['model'] and p['reasoning'] == args.current['effort']), None)
        executor = ThreadPoolExecutor(max_workers=1)
        applying = None
        applying_index = None
        applying_token = None
        applying_thread = None
        inspecting = None
        inspecting_token = None
        retry_at = 0
        hide_at = None

        def target():
            return binding.current['threadId'] if binding and binding.current else (
                args.thread if not args.auto else None)

        def current_token():
            return binding.token() if binding else None

        def binding_status():
            state = binding.current if binding else (getattr(args, 'current', None))
            return {'mode': 'auto' if args.auto else ('pinned' if args.send else 'preview'),
                    'connected': bool(state), 'epoch': binding.epoch if binding else None,
                    'threadId': state['threadId'] if state else None,
                    'model': state['model'] if state else None,
                    'effort': state['effort'] if state else None}

        def label():
            if binding and binding.current:
                return Path(binding.current.get('cwd') or '.').name + ' · ' + target()[:8]
            return target()[:8] if args.send and target() else 'PREVIEW'

        def show(message):
            nonlocal hide_at
            hide_at = None
            for i, row in enumerate(rows):
                active = i == (selection.pending if selection.pending is not None else (
                    applying_index if applying is not None and applying_token == current_token() else selection.committed))
                row.configure(bg="#e9bcae" if active else "#242321",
                              fg="#242321" if active else "#b9b5b1")
            footer.configure(text=message)
            root.deiconify()  # override-redirect popup: do not request keyboard focus

        def tick():
            nonlocal applying, applying_index, applying_token, applying_thread, hide_at
            nonlocal inspecting, inspecting_token, retry_at
            now = time.monotonic()
            if binding:
                if binding.refresh():
                    selection.cancel()
                    selection.committed = None
                    root.withdraw()
                    hide_at = None
                    if inspecting is not None:
                        inspecting.cancel()
                        inspecting = None
                    retry_at = 0
                    print('BINDING ' + json.dumps(binding_status()), flush=True)
                if inspecting is not None and inspecting.done():
                    try:
                        state = inspecting.result()
                        if binding.accept(inspecting_token, state):
                            selection.committed = next((i for i, p in enumerate(presets)
                                if p['model'] == state['model'] and p['reasoning'] == state['effort']), None)
                            print('BINDING ' + json.dumps(binding_status()), flush=True)
                    except Exception as exc:
                        print('BINDING_UNAVAILABLE ' + str(exc), file=sys.stderr, flush=True)
                    inspecting = None
                    retry_at = now + 2
                if binding.observation is not None and binding.current is None and inspecting is None and now >= retry_at:
                    inspecting_token = binding.token()
                    inspecting = executor.submit(inspect_observation, binding.observation, args.sock)
            try:
                while True:
                    raw, sender = server.recvfrom(256)
                    event = raw.decode('ascii', errors='replace')
                    now = time.monotonic()
                    if event in ('binding', 'diagnostics'):
                        if sender:
                            state = binding_status()
                            if event == 'diagnostics' and binding:
                                generation, window = provider.snapshot()
                                state.update(window=window.__dict__ if window else None,
                                             focusGeneration=generation, reports=hub.snapshot())
                            server.sendto(json.dumps(state).encode(), sender)
                        continue
                    if event == "stop":
                        root.destroy()
                        return
                    if event == "cancel":
                        selection.cancel()
                        root.withdraw()
                    elif event in ("next", "prev"):
                        if binding and binding.current is None:
                            continue
                        if selection.pending is None and applying is not None and applying_token == current_token():
                            selection.pending = applying_index
                        selection.rotate(1 if event == "next" else -1, now)
                        show("Stop turning to apply · " + label())
            except BlockingIOError:
                pass
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                selection.cancel()
                print('CONTROL_ERROR ' + str(exc), file=sys.stderr, flush=True)
                if not binding or binding.current is not None:
                    show(str(exc))
                    hide_at = time.monotonic() + 2
            now = time.monotonic()
            if applying is not None and applying.done():
                valid = applying_token == current_token()
                if binding:
                    try:
                        binding.require(applying_token)
                    except RuntimeError:
                        valid = False
                try:
                    confirmed = applying.result()
                    print("APPLIED " + json.dumps(confirmed), flush=True)
                    if valid:
                        selection.committed = applying_index
                        if binding:
                            binding.current.update({k: v for k, v in confirmed.items() if v is not None})
                        else:
                            args.current.update({k: v for k, v in confirmed.items() if v is not None})
                    message = "Applied · " + applying_thread[:8] + " · subsequent turns"
                except Exception as exc:
                    message = "Unconfirmed · see server log"
                    print("UNCONFIRMED " + str(exc), file=sys.stderr, flush=True)
                applying = None
                applying_index = None
                if valid and selection.pending is None:
                    show(message)
                    hide_at = now + 1.5
            index = selection.settle(now) if applying is None else None
            if index is not None:
                text = command(presets[index])
                if args.send:
                    thread_id = target()
                    if not thread_id:
                        return root.after(20, tick)
                    applying_index = index
                    applying_token = current_token()
                    applying_thread = thread_id
                    token = applying_token
                    guard = (lambda: binding.require(token)) if binding else None
                    applying = executor.submit(apply_preset, thread_id, presets[index], args.sock, guard)
                    show("Applying · " + label())
                else:
                    selection.committed = index
                    print("PREVIEW " + text, flush=True)
                    show("Selected · preview only")
                    hide_at = now + 0.7
            if hide_at is not None and now >= hide_at:
                root.withdraw()
                hide_at = None
            root.after(20, tick)

        root.after(20, tick)
        print("Model knob ready · " + ('AUTO FOCUS' if args.auto else (
            "PINNED " + args.thread if args.send else "PREVIEW")), flush=True)
        root.mainloop()
    finally:
        if 'executor' in locals():
            executor.shutdown(wait=False, cancel_futures=True)
        if hub:
            hub.close()
        if provider:
            # An already-running apply worker can still be finishing its guard.
            # Keep X11 open until the executor's running operation has returned.
            if 'executor' in locals():
                executor.shutdown(wait=True, cancel_futures=True)
            provider.close()
        server.close()
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "next", "prev", "cancel", "stop", "list", "sessions", "status", "apply", "binding", "diagnostics"])
    parser.add_argument("--presets", default=Path(__file__).with_name("model-presets.json"))
    parser.add_argument("--delay", type=float, default=0.5, help="seconds of idle rotation before selecting")
    parser.add_argument("--send", action="store_true", help="apply through the local daemon")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--thread", help="pin to this exact session ID")
    mode.add_argument("--auto", action="store_true", help="follow the focused Codex terminal (codex shell integration or codex-knob.py)")
    parser.add_argument("--sock", help="optional Codex daemon socket path")
    parser.add_argument("--index", type=int, help="1-based preset number for an explicit apply")
    argv = sys.argv[1:]
    if not argv:
        argv = ["serve", "--send", "--auto"]
    args = parser.parse_args(argv)
    if not 0.1 <= args.delay <= 5:
        parser.error("--delay must be between 0.1 and 5 seconds")
    if args.auto and (args.action != 'serve' or not args.send):
        parser.error('--auto requires serve --send')
    if (args.send or args.action in ('status', 'apply')) and not (args.thread or args.auto):
        parser.error('choose --auto or --thread SESSION_ID')
    try:
        if args.action == 'sessions':
            with CodexSession(sock=args.sock) as session:
                for thread_id in session.loaded_ids():
                    info = session.request('thread/read', {'threadId': thread_id})['thread']
                    print(json.dumps({'threadId': thread_id, 'name': info.get('name'),
                                      'cwd': info.get('cwd'), 'status': info.get('status')}))
        elif args.action == 'status':
            with CodexSession(sock=args.sock) as session:
                print(json.dumps(session.inspect(args.thread), indent=2))
        elif args.action == 'apply':
            presets = load_presets(args.presets)
            if args.index is None or not 1 <= args.index <= len(presets):
                parser.error('--index must select a preset, starting from 1')
            print('APPLIED ' + json.dumps(apply_preset(args.thread, presets[args.index - 1], args.sock)))
        elif args.action in ("serve", "list"):
            presets = load_presets(args.presets)
            if args.action == "serve":
                if args.send and not args.auto:
                    with CodexSession(sock=args.sock) as session:
                        args.current = session.inspect(args.thread)
                    print('CURRENT ' + json.dumps(args.current), flush=True)
                serve(args, presets)
            else:
                for p in presets:
                    print(p["label"] + "  →  " + command(p))
        else:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                if args.action in ('binding', 'diagnostics'):
                    import tempfile
                    with tempfile.TemporaryDirectory(prefix='query-', dir=runtime_directory()) as directory:
                        client.bind(str(Path(directory) / 'reply.sock'))
                        client.settimeout(2)
                        client.sendto(args.action.encode(), str(socket_path()))
                        print(json.dumps(json.loads(client.recv(65536)), indent=2))
                else:
                    client.sendto(args.action.encode(), str(socket_path()))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"model-knob: {exc}\n")


if __name__ == "__main__":
    main()
