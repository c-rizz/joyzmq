"""Web publisher: a page that reads a gamepad (through the browser's Gamepad API) or the keyboard, and publishes the
state over ZMQ as the joystick and keyboard publishers do. Next to the controls it can show camzmq cameras.

The gamepad is the one plugged into the machine running the browser, so it needs no /dev/input permissions on the
machine running this server. Browsers expose gamepads only to secure contexts: open the page as http://localhost
(from another machine, through `ssh -L`).

Needs only pyzmq and the standard library; the camera panel also needs camzmq.
"""

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import zmq

from .layout import AXES, BUTTONS, neutral_state


def sanitize_state(state):
    """A canonical state from what a page sent: known names only, axes clamped to [-1, 1], anything missing or
    malformed at rest. Raises ValueError if `state` is not a dict."""
    if not isinstance(state, dict):
        raise ValueError(f"expected a state dict, got {type(state).__name__}")
    out = neutral_state()
    axes = state.get("axes")
    buttons = state.get("buttons")
    if isinstance(axes, dict):
        for name in AXES:
            v = axes.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out["axes"][name] = max(-1.0, min(1.0, float(v)))
    if isinstance(buttons, dict):
        for name in BUTTONS:
            out["buttons"][name] = buttons.get(name) is True
    return out


class WebPublisher:
    """Publishes the gamepad state that a web page sends.

    The page posts its full state on every change and at a steady rate. The state of the controlling page is
    published on every post and at least `rate` times per second. Once that page has been silent for `deadman_s`
    (closed, disconnected, or throttled in a background tab), the neutral state is published instead, within
    1/rate seconds, so subscribers do not keep acting on the last command of an operator who is gone.

    One page controls at a time. Another one becomes the controller when it asks to take over, or when it posts
    after the controller has been silent for deadman_s; until then its posts are refused.
    """

    def __init__(self, bind="tcp://*:5666", topic="joy", rate=20.0, deadman_s=0.3, clock=time.monotonic,
                 context=None):
        self.topic = topic
        self.rate = rate
        self.deadman_s = deadman_s
        self._clock = clock
        self._sock = (context or zmq.Context.instance()).socket(zmq.PUB)
        self._sock.bind(bind)
        self.endpoint = self._sock.getsockopt_string(zmq.LAST_ENDPOINT)
        self._lock = threading.Lock()
        self._state = neutral_state()
        self._controller = None
        self._last_input = -math.inf
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="joyzmq-web", daemon=True)
        self._thread.start()

    def submit(self, client, state, take=False):
        """Input from the page `client`. Returns whether that page controls, and the state now published."""
        state = sanitize_state(state)
        with self._lock:
            now = self._clock()
            if client == self._controller or take or not self._controller_active(now):
                self._controller = client
                self._state = state
                self._last_input = now
                self._wake.set()
                controlling = True
            else:
                controlling = False
            return {"controlling" : controlling, "state" : self._published(now)}

    def published_state(self):
        """The state being published now."""
        with self._lock:
            return self._published(self._clock())

    def _controller_active(self, now):
        return self._controller is not None and now - self._last_input <= self.deadman_s

    def _published(self, now):
        if not self._controller_active(now):
            return neutral_state()
        return {"axes" : dict(self._state["axes"]), "buttons" : dict(self._state["buttons"])}

    def _run(self):
        period = 1.0 / self.rate
        while not self._stop.is_set():
            self._wake.wait(period)
            self._wake.clear()
            if self._stop.is_set():
                break
            self._sock.send_string(f"{self.topic} {json.dumps(self.published_state())}")

    def close(self):
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2.0)
        self._sock.close(linger=0)


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>joyzmq</title>
<style>
:root { color-scheme: light dark; --bg: #fff; --fg: #1b1b1b; --muted: #666; --card: #f1f1f1; --line: #c8c8c8;
        --on: #2f7de1; --warn: #c0392b; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161616; --fg: #e8e8e8; --muted: #9a9a9a; --card: #222; --line: #444; --on: #5a9cf0; --warn: #e2665a; }
}
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px system-ui, sans-serif; }
.card { background: var(--card); border-radius: 8px; padding: 12px; margin: 0 0 16px; max-width: 1100px; }
h2 { margin: 0 0 8px; font-size: 16px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
/* Each section collapses by its title */
summary { list-style: none; cursor: pointer; user-select: none; -webkit-user-select: none; }
summary::-webkit-details-marker { display: none; }
summary h2::before { content: '\25BE'; color: var(--muted); }
details:not([open]) > summary h2::before { content: '\25B8'; }
details:not([open]) > summary h2 { margin-bottom: 0; }
.mono { font-family: ui-monospace, monospace; }
.muted { color: var(--muted); }
#role.on { color: var(--on); }
#role.off { color: var(--warn); }
button { font: inherit; }
/* The pad fills the width of its section, and everything in it is sized in units of that width (cqw) */
.pad-scroll { container-type: inline-size; overflow-x: auto; margin: 10px 0; }
/* Laid out as an Xbox pad: shoulders on top, left stick and face buttons above, d-pad and right stick below */
.gamepad { --stick: 19cqw; --button: 6.8cqw; --gap: 2cqw;
           display: grid; grid-template-columns: repeat(4, 1fr);
           grid-template-areas: "lshoulder . . rshoulder" "lstick . . face" ". dpad rstick .";
           gap: var(--gap); align-items: center; justify-items: center; box-sizing: border-box; width: 100%;
           padding: var(--gap) calc(var(--gap) * 1.5); border: 2px solid var(--line); border-radius: 6cqw;
           font-size: max(10px, 2.4cqw); }
.shoulder { display: flex; gap: 1cqw; }
.shoulder.left { grid-area: lshoulder; justify-self: start; }
.shoulder.right { grid-area: rshoulder; justify-self: end; }
#stick-l { grid-area: lstick; }
#stick-r { grid-area: rstick; }
.cross { display: grid; grid-template-areas: ". n ." "w . e" ". s ."; gap: 2px; }
.cross.dpad { grid-area: dpad; }
.cross.face { grid-area: face; }
.cross .n { grid-area: n; }
.cross .w { grid-area: w; }
.cross .e { grid-area: e; }
.cross .s { grid-area: s; }
.stick { position: relative; width: var(--stick); height: var(--stick); border: 2px solid var(--line); border-radius: 50%; }
.stick .dot { position: absolute; width: 18%; height: 18%; transform: translate(-50%, -50%); border-radius: 50%;
              background: var(--on); left: 50%; top: 50%; }
.gamepad span { box-sizing: border-box; min-width: var(--button); height: var(--button); display: flex;
                align-items: center; justify-content: center; border: 1px solid var(--line);
                font-family: ui-monospace, monospace; }
.shoulder span { padding: 0 1.2cqw; border-radius: 1.8cqw 1.8cqw 0.6cqw 0.6cqw; }
.dpad span { border-radius: 4px; }
.face span { border-radius: 50%; }
.gamepad span.on { background: var(--on); border-color: var(--on); color: #fff; }
.stick, .gamepad span { touch-action: none; user-select: none; -webkit-user-select: none; cursor: pointer; }
@media (max-width: 600px) {
  body { padding: 8px; }
  .card { padding: 8px; }
}
.views { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.views figure { margin: 0; }
.views img { display: block; width: 100%; image-rendering: pixelated; background: #000; }
figcaption { color: var(--muted); margin-top: 4px; }
.status { font-family: ui-monospace, monospace; color: var(--muted); margin-top: 8px; white-space: pre-wrap; }
</style>
</head>
<body>
<div id="cams"></div>
<details class="card" id="control" open>
  <summary><h2>joyzmq <span id="role" class="muted">connecting</span> <button id="take" hidden>take control</button></h2></summary>
  <div id="gamepad" class="muted"></div>
  <div class="pad-scroll"><div class="gamepad">
    <div class="shoulder left"><span id="button-lb">LB</span><span id="button-lt">LT</span></div>
    <div class="shoulder right"><span id="button-rt">RT</span><span id="button-rb">RB</span></div>
    <div class="stick" id="stick-l"><div class="dot"></div></div>
    <div class="cross face">
      <span class="n" id="button-y">Y</span><span class="w" id="button-x">X</span>
      <span class="e" id="button-b">B</span><span class="s" id="button-a">A</span>
    </div>
    <div class="cross dpad">
      <span class="n" id="button-dpad_up">&#9650;</span><span class="w" id="button-dpad_left">&#9664;</span>
      <span class="e" id="button-dpad_right">&#9654;</span><span class="s" id="button-dpad_down">&#9660;</span>
    </div>
    <div class="stick" id="stick-r"><div class="dot"></div></div>
  </div></div>
  <div id="published" class="mono"></div>
  <div class="muted">keyboard: W/S/A/D left stick, arrows right stick, I/K/J/L y/a/x/b, T/G/F/H d-pad,
    E/Q/O/U lt/lb/rb/rt. On screen: drag the sticks, hold the buttons.</div>
</details>
<script>
const $ = (sel, el) => (el || document).querySelector(sel);
const fmt = (v, digits) => (v === null || v === undefined) ? '-' : v.toFixed(digits);
const AXES = ['lx', 'ly', 'rx', 'ry'];
const BUTTONS = ['a', 'b', 'x', 'y', 'dpad_up', 'dpad_down', 'dpad_left', 'dpad_right', 'lb', 'rb', 'lt', 'rt'];
const AXIS_KEYS = {KeyW: ['ly', 1], KeyS: ['ly', -1], KeyA: ['lx', -1], KeyD: ['lx', 1],
                   ArrowUp: ['ry', 1], ArrowDown: ['ry', -1], ArrowLeft: ['rx', -1], ArrowRight: ['rx', 1]};
const BUTTON_KEYS = {KeyI: 'y', KeyK: 'a', KeyJ: 'x', KeyL: 'b', KeyT: 'dpad_up', KeyG: 'dpad_down',
                     KeyF: 'dpad_left', KeyH: 'dpad_right', KeyE: 'lt', KeyQ: 'lb', KeyO: 'rb', KeyU: 'rt'};
// The browser's "standard" gamepad mapping
const GAMEPAD_BUTTONS = {a: 0, b: 1, x: 2, y: 3, lb: 4, rb: 5, lt: 6, rt: 7,
                         dpad_up: 12, dpad_down: 13, dpad_left: 14, dpad_right: 15};
const client = Math.random().toString(36).slice(2) + Date.now().toString(36);
const held = new Set();
const keyboardAxes = {lx: 0, ly: 0, rx: 0, ry: 0};
let config = null, take = false, inFlight = false, lastSent = null, lastSentT = 0, lastTick = performance.now();

addEventListener('keydown', e => {
  if (e.code in AXIS_KEYS || e.code in BUTTON_KEYS) { held.add(e.code); e.preventDefault(); }
});
addEventListener('keyup', e => held.delete(e.code));
addEventListener('blur', () => held.clear()); // the key releases would not arrive
$('#take').onclick = (e) => { e.preventDefault(); take = true; }; // the button is in the title: do not collapse

// Sections remember being collapsed, in this browser
function collapsible(el, key) {
  try { if (localStorage.getItem('joyzmq.closed.' + key) === '1') el.open = false; } catch (e) {}
  el.addEventListener('toggle', () => {
    try { localStorage.setItem('joyzmq.closed.' + key, el.open ? '0' : '1'); } catch (e) {}
  });
}
collapsible($('#control'), 'control');

// On-screen controls, for touch screens and the mouse: a stick follows the pointer dragging it and springs back
// when released, a button is pressed while held. Each pointer is tracked on its own, so several fingers work at once.
const touchAxes = {lx: 0, ly: 0, rx: 0, ry: 0};
const touchButtons = new Map(); // pointerId -> button name

function bindStick(stick, ax, ay) {
  let pointer = null;
  const move = e => {
    const r = stick.getBoundingClientRect();
    let x = (e.clientX - (r.left + r.width / 2)) / (r.width / 2);
    let y = -(e.clientY - (r.top + r.height / 2)) / (r.height / 2);
    const norm = Math.hypot(x, y);
    if (norm > 1) { x /= norm; y /= norm; }
    touchAxes[ax] = x;
    touchAxes[ay] = y;
  };
  const release = e => {
    if (e.pointerId !== pointer) return;
    pointer = null;
    touchAxes[ax] = 0;
    touchAxes[ay] = 0;
  };
  stick.addEventListener('pointerdown', e => {
    pointer = e.pointerId;
    stick.setPointerCapture(pointer);
    move(e);
    e.preventDefault();
  });
  stick.addEventListener('pointermove', e => { if (e.pointerId === pointer) move(e); });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) stick.addEventListener(type, release);
}
bindStick($('#stick-l'), 'lx', 'ly');
bindStick($('#stick-r'), 'rx', 'ry');

for (const b of BUTTONS) {
  const span = $('#button-' + b);
  span.addEventListener('pointerdown', e => {
    touchButtons.set(e.pointerId, b);
    span.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    span.addEventListener(type, e => touchButtons.delete(e.pointerId));
  }
}
addEventListener('blur', () => {
  touchButtons.clear();
  for (const a of AXES) touchAxes[a] = 0;
});

function gamepad() {
  if (!navigator.getGamepads) return null;
  const pads = [...navigator.getGamepads()].filter(p => p && p.connected);
  return pads.find(p => p.mapping === 'standard') || pads[0] || null;
}

function readState(dt) {
  // Keyboard sticks ramp toward their target, as joyzmq-keyboard's do
  const target = {lx: 0, ly: 0, rx: 0, ry: 0};
  for (const code of held) if (code in AXIS_KEYS) target[AXIS_KEYS[code][0]] += AXIS_KEYS[code][1];
  const step = config.ramp * dt;
  for (const a of AXES) {
    const t = Math.max(-1, Math.min(1, target[a]));
    keyboardAxes[a] += Math.max(-step, Math.min(step, t - keyboardAxes[a]));
  }
  const state = {axes: {...keyboardAxes}, buttons: {}};
  for (const b of BUTTONS) state.buttons[b] = false;
  for (const code of held) if (code in BUTTON_KEYS) state.buttons[BUTTON_KEYS[code]] = true;
  const gp = gamepad();
  if (gp) {
    const g = {lx: gp.axes[0] || 0, ly: -(gp.axes[1] || 0), rx: gp.axes[2] || 0, ry: -(gp.axes[3] || 0)};
    for (const a of AXES) if (Math.abs(g[a]) > Math.abs(state.axes[a])) state.axes[a] = g[a];
    for (const [b, i] of Object.entries(GAMEPAD_BUTTONS)) {
      const button = gp.buttons[i];
      if (button && (button.pressed || button.value > 0.5)) state.buttons[b] = true;
    }
  }
  for (const a of AXES) if (Math.abs(touchAxes[a]) > Math.abs(state.axes[a])) state.axes[a] = touchAxes[a];
  for (const b of touchButtons.values()) state.buttons[b] = true;
  return [state, gp];
}

function moveDot(stick, x, y) {
  const dot = $('.dot', stick);
  dot.style.left = (50 + 45 * x) + '%';
  dot.style.top = (50 - 45 * y) + '%';
}

function showInput(state, gp) {
  moveDot($('#stick-l'), state.axes.lx, state.axes.ly);
  moveDot($('#stick-r'), state.axes.rx, state.axes.ry);
  for (const b of BUTTONS) $('#button-' + b).classList.toggle('on', state.buttons[b]);
  let text;
  if (!window.isSecureContext) {
    text = 'gamepads need a secure context: open this page as http://localhost (from another machine, through ssh -L)';
  } else if (!navigator.getGamepads) {
    text = 'this browser has no Gamepad API';
  } else if (gp) {
    text = 'gamepad: ' + gp.id + (gp.mapping === 'standard' ? '' : '  (not in the standard mapping: buttons may differ)');
  } else {
    text = 'no gamepad: connect one and press one of its buttons';
  }
  $('#gamepad').textContent = text;
}

function formatState(s) {
  const axes = AXES.map(a => a + ':' + (s.axes[a] >= 0 ? '+' : '') + s.axes[a].toFixed(2)).join(' ');
  return 'published  ' + axes + '  |  ' + BUTTONS.filter(b => s.buttons[b]).join(' ');
}

function showRole(reply) {
  const role = $('#role');
  if (reply === null) {
    role.textContent = 'disconnected';
    role.className = 'off';
    $('#take').hidden = true;
    return;
  }
  role.textContent = reply.controlling ? 'controlling' : 'watching: another page controls';
  role.className = reply.controlling ? 'on' : 'off';
  $('#take').hidden = reply.controlling;
  $('#published').textContent = formatState(reply.state);
}

async function tick() {
  const now = performance.now();
  const dt = Math.min((now - lastTick) / 1000, 0.1);
  lastTick = now;
  const [state, gp] = readState(dt);
  showInput(state, gp);
  // A hidden page stops sending, so that the server's deadman publishes the neutral state
  if (document.hidden || inFlight) return;
  const key = JSON.stringify(state);
  if (key === lastSent && !take && now - lastSentT < 1000 / config.rate) return;
  inFlight = true;
  try {
    const reply = await (await fetch('api/input', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                   body: JSON.stringify({client, state, take})})).json();
    take = false;
    lastSent = key;
    lastSentT = now;
    showRole(reply);
  } catch (e) {
    showRole(null);
  } finally {
    inFlight = false;
  }
}

function buildCamera(cam) {
  const el = document.createElement('details');
  el.className = 'card';
  el.open = true;
  el.innerHTML = '<summary><h2></h2></summary><div class="views">'
    + '<figure class="rgb"><img alt="rgb"><figcaption>rgb</figcaption></figure>'
    + '<figure class="depth"><img alt="depth"><figcaption></figcaption></figure></div>'
    + '<div class="status frame"></div><div class="status stats"></div>';
  $('h2', el).textContent = cam.name + '  ' + cam.height + 'x' + cam.width;
  $('.rgb', el).hidden = !cam.rgb_channels;
  $('.depth', el).hidden = !cam.has_depth;
  return el;
}

function showFrame(el, d) {
  if (d.rgb) $('.rgb img', el).src = 'data:image/png;base64,' + d.rgb;
  if (d.depth) {
    $('.depth img', el).src = 'data:image/png;base64,' + d.depth;
    $('.depth figcaption', el).textContent = 'depth  near ' + fmt(d.depth_range[0], 2) + ' m, far '
      + fmt(d.depth_range[1], 2) + ' m';
  }
  let text = 'seq ' + d.seq + '  age ' + fmt(d.age_ms, 0) + ' ms';
  if (d.depth_stats) text += '  depth valid ' + fmt(d.depth_stats.valid_pct, 0) + '%';
  $('.frame', el).textContent = text;
}

async function pollFrames(cam, el) {
  let shown = null;
  for (;;) {
    if (!el.open) { // collapsed: no frames are fetched
      shown = null;
      await new Promise(r => setTimeout(r, 200));
      continue;
    }
    try {
      const url = 'api/frame/' + encodeURIComponent(cam.name) + (shown ? '?after=' + shown.seq : '');
      const d = await (await fetch(url)).json();
      if (d.seq === null) {
        $('.frame', el).textContent = 'no frame yet';
      } else {
        shown = d.unchanged ? {...shown, age_ms: d.age_ms} : d;
        showFrame(el, shown);
      }
      await new Promise(r => setTimeout(r, 20));
    } catch (e) {
      $('.frame', el).textContent = 'disconnected';
      shown = null;
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

async function pollStats(cam, el) {
  for (;;) {
    try {
      const s = await (await fetch('api/stats/' + encodeURIComponent(cam.name))).json();
      const lat = s.latency_ms.capture_to_arrival;
      $('.stats', el).textContent = 'last ' + s.window_s + ' s: ' + fmt(s.received_fps, 1) + ' fps  missed '
        + fmt(s.missed_pct, 1) + '%  capture -> arrival p50 ' + (lat ? fmt(lat.p50, 1) : '-') + ' p95 '
        + (lat ? fmt(lat.p95, 1) : '-') + ' max ' + (lat ? fmt(lat.max, 1) : '-') + ' ms';
    } catch (e) {
      $('.stats', el).textContent = '';
    }
    await new Promise(r => setTimeout(r, 1000));
  }
}

(async () => {
  config = await (await fetch('api/config')).json();
  setInterval(tick, 20);
  for (const cam of (config.cameras || [])) {
    const el = buildCamera(cam);
    collapsible(el, 'camera.' + cam.name);
    document.getElementById('cams').appendChild(el);
    pollFrames(cam, el);
    pollStats(cam, el);
  }
})();
</script>
</body>
</html>
"""


def _make_handler(publisher, view, ramp):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            try:
                if url.path == "/":
                    self._send(_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif url.path == "/api/config":
                    config = {"topic" : publisher.topic, "endpoint" : publisher.endpoint, "rate" : publisher.rate,
                              "deadman_s" : publisher.deadman_s, "ramp" : ramp,
                              "cameras" : None if view is None else view.cameras()["cameras"]}
                    self._send_json(config)
                elif url.path.startswith("/api/frame/") and view is not None:
                    after = parse_qs(url.query).get("after", [None])[0]
                    self._send_json(view.frame(unquote(url.path[len("/api/frame/"):]),
                                               None if after is None else int(after)))
                elif url.path.startswith("/api/stats/") and view is not None:
                    self._send_json(view.stats(unquote(url.path[len("/api/stats/"):])))
                else:
                    self.send_error(404)
            except KeyError:
                self.send_error(404, "unknown camera")
            except ValueError:
                self.send_error(400)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            url = urlsplit(self.path)
            try:
                if url.path != "/api/input":
                    self.send_error(404)
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                if not isinstance(body, dict) or not isinstance(body.get("client"), str):
                    raise ValueError("expected {client, state, take}")
                self._send_json(publisher.submit(body["client"], body.get("state"), body.get("take") is True))
            except ValueError: # json.JSONDecodeError is one
                self.send_error(400)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_json(self, payload):
            self._send(json.dumps(payload).encode("utf-8"), "application/json")

        def _send(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return Handler


def serve(publisher, bind="127.0.0.1", port=8090, view=None, ramp=2.0):
    """An HTTP server for the page; the caller runs serve_forever() and server_close().

    `view` is a camzmq.gui.FrameView of the cameras to show, or None for the controls only. `ramp` is the speed of
    the keyboard sticks, in units per second.
    """
    server = ThreadingHTTPServer((bind, port), _make_handler(publisher, view, ramp))
    server.daemon_threads = True
    return server


class MultiView:
    """The camera views of several camzmq publishers (camzmq.gui.FrameView each), as one: each camera is answered by
    the view that has it. Raises ValueError if two views have a camera with the same name."""

    def __init__(self, views):
        self._views = list(views)
        self._by_camera = {}
        for view in self._views:
            for cam in view.cameras()["cameras"]:
                if cam["name"] in self._by_camera:
                    raise ValueError(f"camera {cam['name']!r} is published by more than one camzmq publisher")
                self._by_camera[cam["name"]] = view

    def cameras(self):
        described = [view.cameras() for view in self._views]
        return {"cameras" : [cam for d in described for cam in d["cameras"]],
                "colorbar" : described[0]["colorbar"] if described else ""}

    def frame(self, camera, after=None):
        return self._by_camera[camera].frame(camera, after)

    def stats(self, camera):
        return self._by_camera[camera].stats(camera)


def run_web(bind="tcp://*:5666", topic="joy", rate=20.0, deadman_s=0.3, ramp=2.0, http_bind="127.0.0.1",
            http_port=8090, cameras_urls=None, cameras=None, depth_range=None):
    """Serve the page and publish what it sends, until interrupted.

    With cameras_urls (the metadata endpoints of one or more camzmq publishers), the page also shows their cameras:
    all of them, or those named in `cameras`, whichever publisher has them. Each name must be published by one of
    them, and by only one.
    """
    subscribers = []
    view = None
    publisher = None
    server = None
    try:
        if cameras_urls:
            try:
                from camzmq import CameraSubscriber, request_cameras_info
                from camzmq.gui import FrameView
            except ImportError:
                raise RuntimeError("showing cameras needs camzmq: install it in this environment") from None
            wanted = None if cameras is None else list(cameras)
            found = []
            for url in cameras_urls:
                names = [c for c in request_cameras_info(url) if wanted is None or c in wanted]
                if not names:
                    print(f"[joyzmq] none of the requested cameras is published at {url}", flush=True)
                    continue
                found.extend(names)
                subscribers.append((url, CameraSubscriber(url, cameras=names)))
            missing = [] if wanted is None else [c for c in wanted if c not in found]
            if missing:
                raise ValueError(f"cameras {missing} are not published at any of {list(cameras_urls)}")
            view = MultiView([FrameView(sub, depth_range) for _, sub in subscribers])
        publisher = WebPublisher(bind, topic, rate, deadman_s)
        server = serve(publisher, http_bind, http_port, view, ramp)
        print(f"[joyzmq] publishing on {publisher.endpoint} (topic '{topic}'), page on "
              f"http://{http_bind}:{server.server_address[1]}", flush=True)
        for url, sub in subscribers:
            print(f"[joyzmq]   cameras {list(sub.cameras)} from {url}", flush=True)
        server.serve_forever()
    finally:
        if server is not None:
            server.server_close()
        if publisher is not None:
            publisher.close()
        for _, sub in subscribers:
            sub.close()
