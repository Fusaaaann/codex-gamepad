// Public API metadata only. No terminal output, commands, or session settings.
const vscode = require('vscode');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const {execFile} = require('node:child_process');

exports.activate = function (context) {
  if (process.platform !== 'linux') return;
  const uid = os.userInfo().uid;
  const userRuntime = `/run/user/${uid}`;
  const base = process.env.XDG_RUNTIME_DIR || (fs.existsSync(userRuntime) ? userRuntime : '/tmp');
  const socketPath = path.join(base, `codex-model-knob-${uid}`, 'binding.sock');
  let socket;
  let stopped = false;
  let retry;
  let sequence = 0;
  let request = 0;
  let terminalPid = null;
  let lastTerminal;
  let windowId = null;
  let windowRequest = 0;

  function send() {
    if (socket && !socket.destroyed && !socket.connecting && socket.writableLength < 16384) {
      socket.write(JSON.stringify({kind: 'vscode', focused: vscode.window.state.focused,
        window: windowId, terminalPid, sequence}) + '\n');
    }
  }
  function refreshWindow() {
    const revision = ++windowRequest;
    windowId = null;
    sequence++;
    send();
    if (!vscode.window.state.focused) return;
    // Capture this host's exact X11 client at focus-in. Keep that identity until
    // blur: a heartbeat must not relabel an old focused claim as a new window.
    execFile('xprop', ['-root', '_NET_ACTIVE_WINDOW'], {timeout: 1000}, (error, stdout) => {
      if (stopped || revision !== windowRequest || !vscode.window.state.focused) return;
      const match = !error && stdout.match(/0x[0-9a-f]+/i);
      windowId = match ? parseInt(match[0], 16) : null;
      send();
    });
  }
  async function refresh() {
    const revision = ++request;
    const terminal = vscode.window.activeTerminal;
    // Invalidate immediately, before waiting for processId (it is asynchronous).
    terminalPid = null;
    sequence++;
    send();
    const pid = terminal ? await terminal.processId : undefined;
    if (stopped || revision !== request || terminal !== vscode.window.activeTerminal) return;
    terminalPid = pid || null;
    lastTerminal = terminal;
    send();
  }
  function connect() {
    if (stopped) return;
    socket = net.createConnection(socketPath);
    socket.on('connect', send);
    socket.on('error', () => {});
    socket.on('close', () => {
      if (!stopped) retry = setTimeout(connect, 500);
    });
  }
  const interval = setInterval(() => {
    if (vscode.window.state.focused && windowId === null) refreshWindow();
    if (lastTerminal !== vscode.window.activeTerminal) refresh().catch(() => {});
    else send();
  }, 300);
  context.subscriptions.push(
    vscode.window.onDidChangeWindowState(refreshWindow),
    vscode.window.onDidChangeActiveTerminal(() => { refresh().catch(() => {}); }),
    vscode.window.onDidCloseTerminal(() => { refresh().catch(() => {}); }),
    {dispose() {
      stopped = true;
      request++;
      windowRequest++;
      clearInterval(interval);
      clearTimeout(retry);
      socket?.destroy();
    }},
  );
  connect();
  refreshWindow();
  refresh().catch(() => {});
};
