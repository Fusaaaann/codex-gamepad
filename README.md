# Agent Key → Codex profile for AntiMicroX

Turns any SDL gamepad into the Work Louder × OpenAI **Agent Key**: every control
runs a **direct Codex command** through a small bridge script — no keyboard
shortcuts are synthesized, each button launches a real `codex` process.

## Files

| File | Purpose |
|---|---|
| `agent-key.gamecontroller.amgp` | Profile for the flat arcade fightstick (current hardware) |
| `bin/codex-bridge.sh` | Gamepad→Codex bridge; one verb per control |
| `install.sh` | Stamps the real script path into every profile |
| `*.installed.gamecontroller.amgp` | Generated — load these into AntiMicroX |

The original profile for a standard SDL gamepad is archived in `.vscode/` (gitignored, kept locally for reference).

## Setup

```bash
./install.sh                     # writes agent-key.installed.gamecontroller.amgp
export CODEX_WORKDIR=~/your/repo  # where agents run (per session)
```

Then in AntiMicroX: pick your controller → Load → the installed `.amgp`.

Override the Codex binary if needed: `export CODEX_BIN=/path/to/codex`.

## Mapping — arcade fightstick (current hardware)

Recoded so **color = action category**. The red/white split on the hardware now
carries meaning: what you're looking at tells you the stakes before you press.

- **RED = decide / commit** — spends a decision, often irreversible.
- **WHITE = invoke / navigate** — fires a skill or moves; safe to repeat.

Reject lives on the isolated big thumb button, as far from Accept as the board
allows, so the two highest-stakes actions can't be confused by feel. The white
skill row runs in workflow order: review → test → debug → refactor.

```
 RED = decide            WHITE = invoke / navigate
 b1  b2  b3       b6  b7  b8  b9      <- skills, workflow order
     b4           b10 b11 b12 b13     <- run / talk / chat / docs
    [b5] reject

 knob: rotate- / rotate+     stick: nav up/down/left/right
```

| Control | Button | Category | Codex command |
|---|---|---|---|
| Accept / apply | b1 | commit | `accept` |
| Edit | b2 | commit | `edit` |
| Push (commit + push) | b3 | commit | `push` |
| New session | b4 | commit | `new` |
| Reject / interrupt | b5 (thumb) | commit | `reject` |
| Review PR | b6 | invoke | `review` |
| Run tests | b7 | invoke | `test` |
| Debug | b8 | invoke | `debug` |
| Refactor | b9 | invoke | `refactor` |
| Code / cloud run | b10 | invoke | `code` |
| Push-to-talk (hold) | b11 | invoke | `talk start` → `talk stop` |
| ChatGPT | b12 | navigate | `chat` |
| Docs | b13 | navigate | `docs` |
| **Knob rotate −** | b14 | dial | `rotate-` (reasoning down) |
| **Knob rotate +** | b15 | dial | `rotate+` (reasoning up) |
| **Stick ↑ ↓ ← →** | dpad 1 | navigate | `nav-up` / `nav-down` / `nav-left` / `nav-right` |

The knob and stick rows are placeholders until the hardware arrives — press each
in AntiMicroX to confirm its index (encoders emit two momentary buttons; the
lever shows up as a dpad/hat) and re-drag if the numbers differ.

## Mapping — standard SDL gamepad (original)

Original Agent Key layout mapped onto a gamepad with analog sticks.

| Agent Key control | Gamepad input | Codex command |
|---|---|---|
| Joystick flick ↑ | Left stick up | `review` — review staged changes as a PR |
| Joystick flick → | Left stick right | `refactor` — refactor open file |
| Joystick flick ↓ | Left stick down | `debug` — reproduce + diagnose last error |
| Joystick flick ← | Left stick left | `test` — run suite, fix until green |
| Reasoning dial + | Right stick right | `reason-up` — step reasoning higher |
| Reasoning dial − | Right stick left | `reason-down` — step reasoning lower |
| Agent selector | D-pad ↑ / ↓ | `agent-prev` / `agent-next` |
| Touch sensor | Right trigger (hold) | `talk start` on press, `talk stop` on release |
| Lightning key | LB | `review` |
| Sparkle key | RB | `refactor` |
| Accept | A | `accept` — apply last change |
| Reject | B / left trigger | `reject` — interrupt running action |
| Edit | X | `edit` — apply with edits |
| ChatGPT key | Y | `chat` — open assistant |
| New chat | Back | `new` — new session |
| Code / cloud run | Start | `code` — implement top TODO |
| Brain boost | L3 | `reason-up` |
| Cloud push | R3 | `push` — commit + push |
| Expand / resize | Guide / D-pad → | `expand` |
| Docs | D-pad ← | `docs` |

## How the bridge stays "direct"

`codex-bridge.sh` calls `codex exec` with a concrete prompt and passes the
dial's current reasoning level via `-c model_reasoning_effort=…`. `reject`
sends `SIGINT` straight to the running `codex exec` process rather than faking
Ctrl+C. Push-to-talk fires real start/stop verbs on press and release.

Run `bin/codex-bridge.sh help` to see every verb and the current dial value.

## Customize

Edit the prompt strings in `codex-bridge.sh` to change what each control does,
or point verbs at Work Louder helpers (`codex-session`, `codex-voice`) if you
have them installed. The profile's `<actionname>` tags document each binding in
the AntiMicroX UI.
