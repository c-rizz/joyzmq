"""Dependency-free Linux joystick reader, mapped to the canonical layout.

Reads raw events from /dev/input/jsX (the kernel joystick API). Each event is
an 8-byte struct:
    __u32 time;   /* event timestamp in milliseconds */
    __s16 value;  /* axis value or button state       */
    __u8  type;   /* event type (axis/button/init)    */
    __u8  number; /* axis/button index                */

Raw hardware indices are translated to the named axes/buttons in `layout`,
using the standard Linux xpad mapping for Xbox-style controllers. Triggers
(analog axes) and the d-pad (a hat axis) are thresholded into named buttons so
the joystick exposes exactly the same set as the keyboard publisher.
"""

import fcntl
import glob
import os
import struct

from .layout import neutral_state

_EVENT_FORMAT = "IhBB"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)

_EVENT_BUTTON = 0x01
_EVENT_AXIS = 0x02
_EVENT_INIT = 0x80  # set on the synthetic events sent right after opening

# Standard xpad layout: raw index -> canonical name.
_STICK_AXES = {0: "lx", 1: "ly", 3: "rx", 4: "ry"}
_TRIGGER_AXES = {2: "lt", 5: "rt"}              # analog -> button via threshold
_HAT_AXES = {                                   # hat axis -> (neg button, pos button)
    6: ("dpad_left", "dpad_right"),
    7: ("dpad_up", "dpad_down"),
}
_BUTTONS = {0: "a", 1: "b", 2: "x", 3: "y", 4: "lb", 5: "rb"}

# The kernel reports stick Y axes positive-down; flip them so up = +1, matching
# the canonical layout and the keyboard publisher.
_INVERTED_AXES = {"ly", "ry"}

_TRIGGER_ON = 0.5  # normalised pull past which a trigger counts as pressed
_HAT_ON = 0.5      # normalised hat deflection past which a d-pad dir is pressed

_JSIOCGNAME_LEN = 128


def _jsiocgname(length):
    """Build the JSIOCGNAME(length) ioctl request number.

    Equivalent to the C macro _IOC(_IOC_READ, 'j', 0x13, length).
    """
    return (2 << 30) | (length << 16) | (ord("j") << 8) | 0x13


def joystick_name(device):
    """Return the device's reported name (brand/model), or None if unavailable.

    Uses the JSIOCGNAME ioctl. Returns None if the device can't be opened
    (e.g. missing permissions) or reports no name.
    """
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        buf = bytearray(_JSIOCGNAME_LEN)
        fcntl.ioctl(fd, _jsiocgname(_JSIOCGNAME_LEN), buf)
        return buf.split(b"\x00", 1)[0].decode("utf-8", "replace") or None
    except OSError:
        return None
    finally:
        os.close(fd)


def _js_index(path):
    suffix = path.rsplit("js", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


def list_joysticks():
    """List connected joysticks as (device_path, name_or_None) tuples.

    Sorted by device number (js0, js1, ...). `name` comes from JSIOCGNAME and
    is None when the device exists but its name can't be read.
    """
    devices = sorted(glob.glob("/dev/input/js*"), key=_js_index)
    return [(dev, joystick_name(dev)) for dev in devices]


class Joystick:
    """Reads a Linux joystick and keeps the canonical axis/button state.

    Axis values are normalised to [-1.0, 1.0]; buttons are booleans. Hardware
    inputs not part of the canonical layout (start, back, guide, stick clicks,
    extra axes) are ignored. Use it as a context manager:

        with Joystick("/dev/input/js0") as js:
            while True:
                js.read_event()
                print(js.state())
    """

    def __init__(self, device="/dev/input/js0"):
        self.device = device
        self._fd = None
        state = neutral_state()
        self.axes = state["axes"]
        self.buttons = state["buttons"]

    def open(self):
        self._fd = open(self.device, "rb")
        return self

    def close(self):
        if self._fd is not None:
            self._fd.close()
            self._fd = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def read_event(self):
        """Block until one event arrives and fold it into the current state.

        Raises EOFError if the device disconnects.
        """
        data = self._fd.read(_EVENT_SIZE)
        if not data:
            raise EOFError("joystick disconnected")
        _time, value, ev_type, number = struct.unpack(_EVENT_FORMAT, data)
        ev_type &= ~_EVENT_INIT  # treat init events like normal ones
        if ev_type == _EVENT_AXIS:
            norm = max(-1.0, min(1.0, value / 32767.0))
            if number in _STICK_AXES:
                name = _STICK_AXES[number]
                self.axes[name] = -norm if name in _INVERTED_AXES else norm
            elif number in _TRIGGER_AXES:
                self.buttons[_TRIGGER_AXES[number]] = norm > _TRIGGER_ON
            elif number in _HAT_AXES:
                neg, pos = _HAT_AXES[number]
                self.buttons[neg] = norm < -_HAT_ON
                self.buttons[pos] = norm > _HAT_ON
        elif ev_type == _EVENT_BUTTON and number in _BUTTONS:
            self.buttons[_BUTTONS[number]] = bool(value)

    def state(self):
        """Return a plain-dict snapshot of the current joystick state."""
        return {"axes": dict(self.axes), "buttons": dict(self.buttons)}
