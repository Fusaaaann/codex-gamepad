"""Version-specific local Codex daemon client. Never starts inference or a daemon."""
import json
import os
from pathlib import Path
import time


class SessionError(RuntimeError):
    pass


class CodexSession:
    def __init__(self, timeout=8, sock=None):
        try:
            from websockets.sync.client import unix_connect
        except ImportError as exc:
            raise SessionError("install requirements-knob.txt and run with .venv/bin/python") from exc
        self.timeout = timeout
        self.sequence = 0
        self.notifications = []
        path = sock or str(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) /
                           "app-server-control/app-server-control.sock")
        try:
            self.connection_context = unix_connect(path, open_timeout=timeout, close_timeout=1,
                                                   compression=None, max_size=8 * 1024 * 1024)
            self.connection = self.connection_context.__enter__()
        except Exception as exc:
            raise SessionError(f"cannot connect to local daemon at {path}: {exc}") from exc
        try:
            self.request("initialize", {
                "clientInfo": {"name": "codex-model-knob", "version": "0.2"},
                "capabilities": {"experimentalApi": True},
            })
            self.write({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def write(self, value):
        try:
            self.connection.send(json.dumps(value))
        except Exception as exc:
            raise SessionError(f"daemon disconnected: {exc}") from exc

    def receive(self, deadline):
        try:
            return json.loads(self.connection.recv(timeout=max(0, deadline - time.monotonic())))
        except TimeoutError as exc:
            raise SessionError("daemon confirmation timed out; outcome unconfirmed") from exc
        except Exception as exc:
            raise SessionError(f"daemon receive failed: {exc}") from exc

    def request(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.write({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            value = self.receive(deadline)
            if value.get("id") == request_id:
                if "error" in value:
                    raise SessionError(f"{method}: {value['error'].get('message', value['error'])}")
                return value["result"]
            if value.get("method") == "thread/settings/updated":
                self.notifications.append(value["params"])
                self.notifications = self.notifications[-256:]
            elif "id" in value and "method" in value:
                # Never become an approval/tool handler for the attached session.
                self.write({"id": value["id"], "error": {
                    "code": -32601, "message": "model knob does not handle server requests"}})

    def loaded_ids(self):
        cursor = None
        result = []
        while True:
            page = self.request("thread/loaded/list", {"cursor": cursor, "limit": 100})
            result.extend(page["data"])
            cursor = page.get("nextCursor")
            if not cursor:
                return result

    def inspect(self, thread_id):
        if thread_id not in self.loaded_ids():
            raise SessionError("target is not loaded on this daemon; refusing to start/resume an inactive session")
        # Rejoin an already loaded thread with NO config overrides or history.
        state = self.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        if state["thread"]["id"] != thread_id:
            raise SessionError("daemon returned a different thread")
        return {"threadId": thread_id, "model": state["model"],
                "effort": state.get("reasoningEffort"), "cwd": state.get("cwd"),
                "status": state["thread"].get("status")}

    def apply(self, thread_id, preset, before_send=None):
        state = self.inspect(thread_id)
        # Inspection can block. Revalidate the originating focus/session AFTER it,
        # immediately before the setting mutation (also guard a no-op confirmation).
        if before_send is not None:
            before_send()
        if state["model"] == preset["model"] and state["effort"] == preset["reasoning"]:
            return state
        self.notifications.clear()
        self.request("thread/settings/update", {
            "threadId": thread_id, "model": preset["model"], "effort": preset["reasoning"],
        })
        deadline = time.monotonic() + self.timeout
        while True:
            if self.notifications:
                params = self.notifications.pop(0)
            else:
                value = self.receive(deadline)
                if value.get("method") != "thread/settings/updated":
                    continue
                params = value["params"]
            settings = params.get("threadSettings", {})
            if (params.get("threadId") == thread_id and settings.get("model") == preset["model"]
                    and settings.get("effort") == preset["reasoning"]):
                return {"threadId": thread_id, "model": settings["model"],
                        "effort": settings["effort"], "cwd": settings.get("cwd")}

    def close(self):
        self.connection_context.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def apply_preset(thread_id, preset, sock=None, before_send=None):
    with CodexSession(sock=sock) as session:
        return session.apply(thread_id, preset, before_send)
