"""Joystick subscriber: receives gamepad state over ZMQ.

Ways to consume the stream:

- `run_client`      -- CLI printer.
- `recv_states`     -- generator yielding every `GamepadState` (you own the loop).
- `GamepadClient`   -- poll-style: a background thread keeps the latest state and
                       remembers which buttons were pressed since your last poll,
                       so you never block and never miss a transient press.
- `GamepadTeleop`   -- thin convenience over `GamepadClient`: `poll()` returns the
                       latest state and stashes the presses so you can query them
                       by name with `pressed_edge(...)` from anywhere in a loop.

The two front (thumb) clusters are exposed by compass direction:

    LN/LE/LS/LW   left cluster  (d-pad up/right/down/left)
    RN/RE/RS/RW   right cluster (face buttons y/b/a/x -- Xbox/PS positions)
"""

import json
import threading

import zmq

from .layout import neutral_state

# Compass aliases -> canonical button names, for the two front clusters.
FRONT_BUTTONS = {
    "LN": "dpad_up", "LE": "dpad_right", "LS": "dpad_down", "LW": "dpad_left",
    "RN": "y", "RE": "b", "RS": "a", "RW": "x",
}
_FRONT_REVERSE = {canonical: alias for alias, canonical in FRONT_BUTTONS.items()}


class GamepadState:
    """Attribute-style, read-only view over a canonical state dict.

    Front buttons are reachable by compass name, e.g. `pad.RS` (the south face
    button, A/cross) or `pad.LW` (d-pad left). Canonical names also work
    directly: sticks `pad.lx/ly/rx/ry`, the four on top `pad.lb/rb/lt/rt`, and
    the raw face/d-pad names (`pad.a`, `pad.dpad_up`, ...). Every accessor
    returns a bool (buttons) or float (axes).

        for pad in recv_states():
            if pad.RS and pad.lx < -0.5:
                ...
    """

    __slots__ = ("axes", "buttons")

    def __init__(self, state):
        self.axes = state["axes"]
        self.buttons = state["buttons"]

    def __getattr__(self, name):
        # reached only for names that aren't slots; guard against recursion
        if name in ("axes", "buttons"):
            raise AttributeError(name)
        if name in FRONT_BUTTONS:
            return self.buttons[FRONT_BUTTONS[name]]
        if name in self.buttons:
            return self.buttons[name]
        if name in self.axes:
            return self.axes[name]
        raise AttributeError(name)

    def pressed(self):
        """List of pressed buttons; front-cluster ones by compass name."""
        return [_FRONT_REVERSE.get(n, n) for n, v in self.buttons.items() if v]

    def __repr__(self):
        axes = " ".join(f"{n}:{v:+.2f}" for n, v in self.axes.items())
        return f"<GamepadState {axes} | {' '.join(self.pressed())}>"


def _parse(msg):
    """Split a 'topic {json}' message into a GamepadState."""
    _topic, _, payload = msg.partition(" ")
    return GamepadState(json.loads(payload))


def recv_states(connect="tcp://localhost:5666", topic="joy"):
    """Yield `GamepadState` objects as they arrive. The caller owns the loop.

    Note: a SUB socket queues messages, so a slow consumer reads stale, backed-up
    states. For real-time "latest state" use `GamepadClient` instead.

    Closing the generator (or letting it be garbage-collected) tears down the
    ZMQ socket.
    """
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.connect(connect)
    sock.setsockopt_string(zmq.SUBSCRIBE, topic)
    try:
        while True:
            yield _parse(sock.recv_string())
    finally:
        sock.close(linger=0)


class GamepadClient:
    """Poll-style gamepad subscriber for real-time control loops.

    A daemon thread receives *every* published state (so no button press is
    missed even when they arrive faster than you poll), while keeping only the
    most recent one for axes/current button levels. `poll()` returns that latest
    state together with the set of buttons pressed (rising edge) since the
    previous `poll()`, then clears the press accumulator.

        gp = GamepadClient()
        while running:
            pad, pressed = gp.poll()      # never blocks
            throttle = pad.ly             # current stick value (latest)
            if "RS" in pressed:           # tapped at least once since last poll
                jump()
        gp.close()

    The `pressed` set holds canonical names *and*, for front buttons, their
    compass alias -- so both `"a" in pressed` and `"RS" in pressed` work.
    """

    def __init__(self, connect="tcp://localhost:5666", topic="joy", recv_timeout_ms=100):
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.connect(connect)
        sock.setsockopt_string(zmq.SUBSCRIBE, topic)
        sock.setsockopt(zmq.RCVTIMEO, recv_timeout_ms)  # so the thread can check _stop
        self._sock = sock
        self._lock = threading.Lock()
        self._latest = GamepadState(neutral_state())
        self._prev = dict(self._latest.buttons)
        self._pressed = set()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="joyzmq-client", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop:
            try:
                msg = self._sock.recv_string()
            except zmq.Again:
                continue  # idle: just re-check the stop flag
            except zmq.ZMQError:
                break  # socket/context closing
            pad = _parse(msg)
            with self._lock:
                for name, value in pad.buttons.items():
                    if value and not self._prev.get(name, False):
                        self._pressed.add(name)
                        alias = _FRONT_REVERSE.get(name)
                        if alias is not None:
                            self._pressed.add(alias)
                self._prev = pad.buttons
                self._latest = pad

    def poll(self):
        """Return (latest GamepadState, set of buttons pressed since last poll)."""
        with self._lock:
            pad = self._latest
            pressed = self._pressed
            self._pressed = set()
        return pad, pressed

    def close(self):
        self._stop = True
        self._thread.join(timeout=1.0)
        self._sock.close(linger=0)


class GamepadTeleop:
    """Convenience facade over `GamepadClient` for control loops.

    `poll()` refreshes the state and returns the latest `GamepadState`, stashing
    the buttons pressed since the previous poll; `pressed_edge(name)` then reports
    whether a given button was pressed, so you can check several buttons in
    different places in the loop without juggling the press set yourself. Names
    may be canonical (`"a"`) or compass aliases (`"RS"`).

        gp = GamepadTeleop()
        while running:
            pad = gp.poll()
            steer = pad.lx
            if gp.pressed_edge("RS"):
                jump()
        gp.close()

    Works with either joyzmq publisher: a real joystick (``joyzmq-joystick``) or
    the keyboard (``joyzmq-keyboard``).
    """

    def __init__(self, connect="tcp://localhost:5666", topic="joy"):
        self._client = GamepadClient(connect, topic)
        self._pressed = set()

    def poll(self):
        """Return the latest gamepad state and refresh the press set."""
        pad, self._pressed = self._client.poll()
        return pad

    def pressed_edge(self, name):
        """True if button `name` was pressed since the previous poll()."""
        return name in self._pressed

    def close(self):
        self._client.close()


def _format(pad):
    axes_str = " ".join(f"{n}:{v:+.2f}" for n, v in pad.axes.items())
    return f"{axes_str}  |  {' '.join(pad.pressed())}"


def run_client(connect="tcp://localhost:5666", topic="joy"):
    """Subscribe to gamepad state and print the latest on a single line."""
    print(f"[joyzmq] subscribing to {connect} (topic '{topic}')")
    for pad in recv_states(connect, topic):
        print(f"\r{_format(pad):<80}", end="", flush=True)
