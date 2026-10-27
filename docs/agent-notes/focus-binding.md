# Focus and session binding maintenance notes

These notes describe the binding architecture and regression constraints for
maintainers and coding agents. User setup is in the
[model knob guide](../model-knob.md).

## Data flow and ownership

```text
X11 window focus + host metadata + terminal focus + session identity
                               ↓
                        switcher binding
                               ↓
                     overlay / settle timer
                               ↓
                     guarded daemon update
```

| Component | Responsibility |
|---|---|
| `focus_binding.py` | X11 provider, report transport, host adapters, unique loaded-ID resolution, and binding epochs. |
| `terminal_launcher.py`, `codex-knob.py` | PTY ownership, byte/signal/resize forwarding, focus/title observation, and launch identity reports. |
| `startup_registration.py` | Join daemon `SessionStart` payloads to live launch ownership records and atomically register full session IDs. |
| `install_startup.py` | Install the user-layer title setting and trusted registration hook, with backups and existing hooks preserved. |
| `codex-shell.sh` | Route supported interactive launches through the producer and pass other invocations to Codex. |
| `vscode-adapter/` | Report window focus and active terminal process metadata using public VS Code APIs. |
| `model-knob.py` | Binding lifecycle, preset selection, overlay, debounce, and asynchronous work. |
| `model-presets.json` | Ordered model/effort pairs. |
| `codex_session.py` | Daemon transport and exact-session settings confirmation. |

## Focus routing

The X11 provider reads `_NET_ACTIVE_WINDOW` and that client's `WM_CLASS`,
`_NET_WM_PID`, and `WM_WINDOW_ROLE`. It polls on the approximately 20 ms controller
tick and reads again immediately before a settings mutation. Unknown hosts or
missing metadata disconnect.

Konsole uses inherited `WINDOWID` to identify the exact client. Terminal focus
reports select the tab or pane. Because the tested Konsole does not emit initial
FocusIn when DECSET 1004 is enabled, unknown initial focus can be bootstrapped
through D-Bus: the exact `MainWindow#N` role maps to `/Windows/N`, its current
session matches `KONSOLE_DBUS_SESSION`, and the foreground PID matches the
launcher. Explicit focus-out is never overridden by this bootstrap. Unique and
named bus services are supported; `/Windows/1` is not assumed.

VS Code's `activeTerminal` remembers the last terminal even after editor focus.
The extension therefore reports window focus, the active terminal's shell PID,
and an X11 client ID captured with `xprop` on focus-in and retained until blur.
Exactly one fresh extension connection must match the active OS window. Launcher
ancestry must contain both the terminal shell PID and the window's Electron PID;
a positive terminal focus report is also required. This combination supports
terminal editor groups and distinguishes windows that share an Electron PID.

Reports use the Unix stream `binding.sock` in the shared runtime directory.
The directory is owned by the current user with mode 0700; the socket is 0600
and accepts same-UID peers only. Producers heartbeat every 300 ms and report
changes immediately. Reports expire after two seconds, and disconnect removes
them. Independent connection revisions and sequence numbers preserve observed
focus transitions; a heartbeat alone does not advance the binding epoch.

## PTY and session identity

The launcher owns the PTY and forwards every input/output byte without logging
terminal contents. It observes DECSET 1004 FocusIn/FocusOut, including split
sequences, while ignoring apparent reports inside bracketed paste. Disabling
focus reporting disconnects until reporting is enabled again.

Bounded nonblocking buffers support both directions. The launcher forwards
resizing and termination signals, propagates exit status, and restores terminal
settings. Child suspension restores the outer terminal and suspends the
launcher; continuation re-enters raw mode. Exit removes the registration.

The normal user-layer configuration sets `tui.terminal_title=["thread-id"]`.
The launcher injects no CLI config flags. Its private ownership record includes
the exact child PID, Linux process start time, and current title hint. The
user-layer `SessionStart` hook joins its full session ID to a matching live owner
and writes the registration atomically. The reported PID may be the Node CLI
shim rather than the underlying native executable.

Codex 0.164 can defer the hook until the first turn, so OSC 0/2 titles supply
idle-chat identity. Accepted hints are a full UUID or the tested truncated shape
of 29 UUID characters plus `...`, resolving to exactly one already-loaded daemon
thread. Title changes invalidate a previous hook identity immediately. Unknown
or cleared titles disconnect. Paths, chat names, and window PIDs are not thread
identity substitutes.

For normal Konsole shell launches, the outer PTY owner's Linux process name is
`Codex`, which labels the tab's `%n` field without altering the OSC identity.
Direct `konsole -e python ...` launches can retain Konsole's cached initial name.
Existing launchers require relaunch for process-name changes.

## Update invariants

1. Changes to window, pane, identity, report revision, or connection invalidate
   binding and pending rotation. A selection cannot transfer from A to B or
   survive A → away → A.
2. Inspection reads settings without changing them. Async results belong to the
   originating binding epoch and observation.
3. Apply revalidates epoch and live focus after daemon inspection and immediately
   before `thread/settings/update`.
4. Confirmation matches the exact thread/model/effort. Stale completions cannot
   advance another target's selection or display Applied for it.
5. Missing, expired, or ambiguous focus/identity information disconnects; there
   is no last-target or project fallback.
6. The overlay does not request focus. An already-sent request cannot be revoked,
   and OS focus and the daemon write cannot be atomic. Transitions entirely
   between X11 polls may be missed.

Pinned `--thread` mode deliberately ignores focus. Preview needs no daemon.

## Corrective history and regression guards

| Observed failure | Cause and current constraint | Regression boundary |
|---|---|---|
| Later plain `codex` launches had no reports | They bypassed the producer. Supported interactive launches use the shell function or explicit launcher. | Shell argument routing and PTY producer tests. |
| Sourcing integration did not affect a later launch | A Codex tool child cannot modify its parent shell. Setup requires sourcing in the terminal shell or opening a configured new shell. | Parent-shell setup instructions; GUI launch checks used interactive Bash. |
| A wrapped resume had a valid title but was absent from the shared daemon | In Codex 0.164, any CLI config override prevents implicit shared-daemon reuse. Title/hook configuration lives in the user layer. | No injected `-c` regression and actual-TUI shared-daemon check. |
| A hook's parent PID did not identify the terminal | Hooks execute in the daemon. Full IDs join to live PID/start-time/title ownership records. | Stale/reused PID, ambiguous prefix, registration, and late-receiver tests. |
| VS Code showed a terminal ID but never bound | Terminal reports alone omit the focused window and active terminal metadata. The extension is also required. | Host/pane mismatch tests and desktop integration driver. |

Earlier fake-TUI and direct hook-payload checks did not detect the embedded-server
failure. `verify-startup-hook.py` remains a narrow payload/trust diagnostic;
`verify-shared-startup.py` covers actual resumed-TUI daemon selection.

## Validation record and limits

Recorded development checks, not checks rerun by this documentation cleanup:

- On 2026-10-27, all 26 automated tests passed. Coverage includes debounce,
  cancellation, unique IDs, report expiry/disconnect, binding epochs, stale
  inspection, guarded sends, daemon confirmation, registration, PTY forwarding,
  resize, exit, restoration, suspension, and 2 MiB full-duplex traffic.
- An actual Codex 0.164 TUI loaded an isolated thread on the shared daemon and
  registered its full ID/PID through the normal trusted hook. Model/effort stayed
  unchanged and the sentinel provider received zero requests. The first fixture
  attempt failed before TUI launch due to a directory-creation error; a corrected
  run passed. Recorded result: `/tmp/knob-shared-s_o7g0l_/result.json`.
- Disposable Konsole and VS Code 1.121.0 windows passed the integrated desktop
  fixture for focus cancellation/rebinding, confirmed updates, exit, panel
  splits, terminal editor groups, normal editor focus, and no overlay focus
  theft. Earlier driver focus timeouts and a canceled update were failures;
  later runs waited for terminal creation and stable binding and explicitly
  activated the test window. The initial timeout's cause was not proven.
  Recorded logs: `/tmp/knob-binding-s_3xb9d3`.
- A real resumed Konsole session bound through its unique loaded title identity
  with unchanged settings. Its registration file was absent, so that observation
  does not establish a fresh `SessionStart` invocation. The isolated actual-TUI
  check supplies hook coverage. The user subsequently reported working binding
  in both Konsole and VS Code tabs.
- An interactive Bash/Konsole launch displayed `gamepad-codex : Codex`; the
  existing three PTY tests passed after that label change. A direct
  `konsole -e python ...` check retained `python` and is outside this guarantee.

Temporary log paths are historical references and may no longer exist. They are
not prerequisites for running the checks in the
[diagnostics guide](../../experiments/README.md).

Remaining manual integration coverage belongs to desktop integration
maintenance: actual Codex `/new` and fork transitions, two VS Code windows
sharing one process, Konsole split/moved-tab layouts, and Codex-specific job
suspension/resumption. Generic identity/epoch and PTY tests cover their component
boundaries but do not establish those full GUI lifecycles. Fixture confirmation
also does not prove inference entitlement or a completed model response.

## References

- [EWMH window metadata](https://specifications.freedesktop.org/wm/latest-single/)
- [Konsole D-Bus window API](https://github.com/KDE/konsole/blob/master/src/ViewManager.h)
- [Konsole session API](https://github.com/KDE/konsole/blob/master/src/session/Session.h)
- [VS Code 1.121.0 public API](https://github.com/microsoft/vscode/blob/1.121.0/src/vscode-dts/vscode.d.ts)
- [xterm.js supported sequences](https://xtermjs.org/docs/api/vtfeatures/)
- [XTerm focus reporting](https://www.invisible-island.net/xterm/ctlseqs/ctlseqs.html#h3-FocusIn_FocusOut)
