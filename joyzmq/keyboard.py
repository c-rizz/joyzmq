"""Dependency-free keyboard reader, mapped to the canonical layout.

Reads the terminal in raw (cbreak) mode via the stdlib, so it needs no extra
packages and no special permissions -- just run it in a terminal.

A terminal only reports key presses/repeats, not releases, so a key counts as
"held" while it keeps auto-repeating and for `hold` seconds after its last
repeat. Stick axes do not snap to +/-1: while a direction key is held the axis
*ramps* toward +/-1 at `ramp` units/second (the longer you hold, the larger the
value), and ramps back toward 0 once the key is released. Buttons stay digital.

Key mapping (emulating a gamepad):
    left stick    W/S = ly +/-   A/D = lx -/+
    right stick   Up/Down = ry   Left/Right = rx
    face (right)  I=y  K=a  J=x  L=b
    d-pad (left)  T=up  G=down  F=left  H=right
    top four      Q=lb  E=rb  R=lt  U=rt
    quit          Ctrl-C
"""

import os
import select
import sys
import termios
import time
import tty

from .layout import neutral_state

# token -> (axis name, target value when held)
_AXIS_KEYS = {
    "w": ("ly", 1.0), "s": ("ly", -1.0),
    "a": ("lx", -1.0), "d": ("lx", 1.0),
    "UP": ("ry", 1.0), "DOWN": ("ry", -1.0),
    "LEFT": ("rx", -1.0), "RIGHT": ("rx", 1.0),
}
# token -> button name
_BUTTON_KEYS = {
    "i": "y", "k": "a", "j": "x", "l": "b",            # face cluster (diamond)
    "t": "dpad_up", "g": "dpad_down", "f": "dpad_left", "h": "dpad_right",
    "q": "lb", "e": "rb", "r": "lt", "u": "rt",         # the four on top
}

_ARROWS = {0x41: "UP", 0x42: "DOWN", 0x43: "RIGHT", 0x44: "LEFT"}


class Keyboard:
    """Reads the terminal and keeps a canonical gamepad state.

    `hold` is how long a key stays active after its last auto-repeat; `ramp` is
    how fast stick axes move toward their target (units/second). Use it as a
    context manager so the terminal is always restored:

        with Keyboard() as kb:
            while True:
                kb.poll(0.05)
                print(kb.state())
    """

    def __init__(self, hold=0.5, ramp=2.0):
        self.hold = hold
        self.ramp = ramp
        self._fd = sys.stdin.fileno()
        self._old = None
        self._last = {}  # token -> monotonic time last seen
        self._t_prev = time.monotonic()
        state = neutral_state()
        self.axes = state["axes"]
        self.buttons = state["buttons"]

    def open(self):
        self._old = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)  # cbreak keeps Ctrl-C working
        self._t_prev = time.monotonic()
        return self

    def close(self):
        if self._old is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            self._old = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def _tokens(data):
        """Split a raw byte buffer into key tokens, decoding arrow escapes."""
        tokens = []
        i, n = 0, len(data)
        while i < n:
            if data[i] == 0x1B and i + 2 < n and data[i + 1] == ord("["):
                arrow = _ARROWS.get(data[i + 2])
                if arrow:
                    tokens.append(arrow)
                    i += 3
                    continue
            tokens.append(chr(data[i]))
            i += 1
        return tokens

    def poll(self, timeout=0.05):
        """Read for up to `timeout` seconds, refresh state, return new tokens."""
        ready, _, _ = select.select([self._fd], [], [], timeout)
        now = time.monotonic()
        dt = now - self._t_prev
        self._t_prev = now
        tokens = []
        if ready:
            tokens = self._tokens(os.read(self._fd, 1024))
            for token in tokens:
                key = token.lower() if len(token) == 1 else token
                self._last[key] = now
        # forget keys that haven't repeated within the hold window
        for key, seen in list(self._last.items()):
            if now - seen > self.hold:
                del self._last[key]
        held = set(self._last)
        # axis targets from currently-held direction keys (opposing keys cancel)
        targets = {name: 0.0 for name in self.axes}
        for key in held:
            if key in _AXIS_KEYS:
                name, value = _AXIS_KEYS[key]
                targets[name] += value
        # ramp each axis toward its target at `ramp` units/second
        step = self.ramp * dt
        for name, current in self.axes.items():
            target = max(-1.0, min(1.0, targets[name]))
            if current < target:
                self.axes[name] = min(target, current + step)
            elif current > target:
                self.axes[name] = max(target, current - step)
        # buttons are digital: pressed while held
        self.buttons = {name: False for name in self.buttons}
        for key in held:
            if key in _BUTTON_KEYS:
                self.buttons[_BUTTON_KEYS[key]] = True
        return tokens

    def state(self):
        """Return a plain-dict snapshot matching the joystick state shape."""
        return {"axes": dict(self.axes), "buttons": dict(self.buttons)}
