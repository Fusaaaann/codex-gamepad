#!/usr/bin/env bash
# codex-bridge — routes Agent Key controls to the Codex CLI.
#
# One executable, one argument = one intent. AntiMicroX execute-slots call
# this with a verb; the bridge turns the verb into a real `codex` invocation.
# Direct commands, not keystrokes: nothing here fakes Ctrl+C or types into a
# window — each action is a concrete process the agent runs.
#
#   codex-bridge.sh <verb> [args...]
#
# Verbs map 1:1 to the physical controls (see agent-key.gamecontroller.amgp).
# Requires: codex CLI on PATH. Optional: notify-send for on-screen feedback.

set -euo pipefail

# ---------------------------------------------------------------- config
CODEX="${CODEX_BIN:-codex}"                 # override with CODEX_BIN=/path/to/codex
WORKDIR="${CODEX_WORKDIR:-$PWD}"            # where agents run; set per-project
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/agent-key"
REASON_FILE="$STATE_DIR/reasoning"          # persists the dial's current level
LOG="$STATE_DIR/bridge.log"
mkdir -p "$STATE_DIR"

# Reasoning tiers the dial steps through (low -> high).
TIERS=(low medium high)

notify() { command -v notify-send >/dev/null 2>&1 && notify-send -t 2000 "Agent Key" "$1" || true; }
log()    { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" >>"$LOG"; }
have()   { command -v "$CODEX" >/dev/null 2>&1; }

reasoning() {
  [ -f "$REASON_FILE" ] && cat "$REASON_FILE" || echo "medium"
}

# codex exec = non-interactive, runs a prompt to completion and prints result.
# We pass the current reasoning level through -c so the dial actually bites.
run() {
  local prompt="$1"
  local level; level="$(reasoning)"
  log "run [$level] $prompt"
  notify "$2"
  cd "$WORKDIR"
  "$CODEX" exec -c model_reasoning_effort="$level" --skip-git-repo-check "$prompt"
}

verb="${1:-}"; shift || true

case "$verb" in
  # -- Skill triggers (joystick flicks + lightning/sparkle keys) -----------
  review)   run "Review the current staged changes as a pull request. Summarize risks, then list concrete review comments by file and line." "Reviewing changes…" ;;
  debug)    run "Reproduce and diagnose the most recent error in this project. Propose the smallest fix and explain the root cause." "Debugging…" ;;
  refactor) run "Refactor the file I currently have open for clarity and safety without changing behavior. Show a diff." "Refactoring…" ;;
  test)     run "Run the test suite. If anything fails, fix it and rerun until green." "Running tests…" ;;
  code)     run "Implement the task described in the top TODO comment of the current file. Apply the change." "Coding…" ;;
  push)     run "Commit the current changes with a clear conventional-commit message, then push to the current branch." "Committing + pushing…" ;;

  # -- Core actions --------------------------------------------------------
  # Direct interrupt of the running codex process, not a synthesized Ctrl+C.
  reject)   pkill -INT -f "$CODEX exec" 2>/dev/null && notify "Cancelled current action" || notify "Nothing running"; log "reject" ;;
  accept)   run "Apply your last proposed change to disk as-is." "Applying change…" ;;
  edit)     run "Apply your last proposed change, but let me adjust it: show the patch and apply it." "Applying with edits…" ;;
  new)      cd "$WORKDIR"; setsid "$CODEX" >/dev/null 2>&1 < /dev/null & notify "New session started"; log "new session pid $!" ;;

  # -- Push-to-talk (touch sensor) ----------------------------------------
  # Hold sends 'talk start' (begin dictation), release sends 'talk stop'.
  talk)
    case "${1:-toggle}" in
      start) command -v codex-voice >/dev/null 2>&1 && codex-voice start & notify "Listening…"; log "talk start" ;;
      stop)  command -v codex-voice >/dev/null 2>&1 && codex-voice stop  & notify "Sent"; log "talk stop" ;;
      *)     notify "Push-to-talk"; log "talk toggle" ;;
    esac ;;

  # -- Reasoning dial ------------------------------------------------------
  reason-up)
    cur="$(reasoning)"; i=0
    for t in "${TIERS[@]}"; do [ "$t" = "$cur" ] && break; i=$((i+1)); done
    [ "$i" -lt $((${#TIERS[@]}-1)) ] && i=$((i+1))
    echo "${TIERS[$i]}" >"$REASON_FILE"; notify "Reasoning: ${TIERS[$i]}"; log "reason-up ${TIERS[$i]}" ;;
  reason-down)
    cur="$(reasoning)"; i=0
    for t in "${TIERS[@]}"; do [ "$t" = "$cur" ] && break; i=$((i+1)); done
    [ "$i" -gt 0 ] && i=$((i-1))
    echo "${TIERS[$i]}" >"$REASON_FILE"; notify "Reasoning: ${TIERS[$i]}"; log "reason-down ${TIERS[$i]}" ;;

  # -- Agent selector (nav dpad) ------------------------------------------
  # Cycles the focused agent session; codex-session is Work Louder's helper.
  agent-next) command -v codex-session >/dev/null 2>&1 && codex-session next || notify "Next agent"; log "agent-next" ;;
  agent-prev) command -v codex-session >/dev/null 2>&1 && codex-session prev || notify "Prev agent"; log "agent-prev" ;;

  # -- Rotary knob (added later) ------------------------------------------
  # Physical encoder, separate from the reasoning boost. Default binding is
  # reasoning level, but kept as its own verb so you can repoint it (scroll,
  # history scrub, temperature) without touching the reasoning keys.
  rotate+)  "$0" reason-up;   log "rotate+" ;;
  rotate-)  "$0" reason-down; log "rotate-" ;;

  # -- Arcade stick = intuitive navigation --------------------------------
  # A real lever, not an analog stick: 4 cardinal directions -> nav intents.
  # Repoint these to whatever "move focus" means in your Codex UI.
  nav-up)    command -v codex-session >/dev/null 2>&1 && codex-session focus up    || notify "Nav up";    log "nav-up" ;;
  nav-down)  command -v codex-session >/dev/null 2>&1 && codex-session focus down  || notify "Nav down";  log "nav-down" ;;
  nav-left)  command -v codex-session >/dev/null 2>&1 && codex-session focus left  || notify "Nav left";  log "nav-left" ;;
  nav-right) command -v codex-session >/dev/null 2>&1 && codex-session focus right || notify "Nav right"; log "nav-right" ;;

  # -- Shortcuts -----------------------------------------------------------
  chat)     cd "$WORKDIR"; setsid "$CODEX" >/dev/null 2>&1 < /dev/null & notify "Opening chat"; log "chat" ;;
  docs)     xdg-open "https://developers.openai.com/codex" >/dev/null 2>&1 & log "docs" ;;
  expand)   command -v codex-session >/dev/null 2>&1 && codex-session expand || notify "Expand panel"; log "expand" ;;

  '' | help)
    echo "codex-bridge <verb>"
    echo "  skills : review debug refactor test code push"
    echo "  core   : reject accept edit new"
    echo "  voice  : talk start|stop"
    echo "  dial   : reason-up reason-down   (current: $(reasoning))"
    echo "  knob   : rotate+ rotate-"
    echo "  stick  : nav-up nav-down nav-left nav-right"
    echo "  agent  : agent-next agent-prev"
    echo "  misc   : chat docs expand" ;;
  *)
    echo "unknown verb: $verb" >&2; exit 2 ;;
esac
