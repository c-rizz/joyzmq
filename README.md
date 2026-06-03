# joyzmq

A dead-simple joystick/keyboard-over-ZMQ publisher/subscriber for Linux.

- **Joystick publisher** reads a joystick (`/dev/input/jsX`) and publishes its state.
- **Keyboard publisher** reads the keyboard and publishes the same kind of state.
- **Client** subscribes and prints the latest state.

Both publishers map their input onto one **canonical gamepad layout** (modelled
on an Xbox/PlayStation controller), so the single client works with either and
the field names mean the same thing regardless of source. The joystick is read
straight from the Linux kernel joystick API and the keyboard via the stdlib in
raw terminal mode, so the only dependency is `pyzmq`. State is sent on every
change, so a client that connects late is in sync after the next input.

## Canonical layout

Every message has this exact shape:

```json
{
  "axes":    {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0},
  "buttons": {"a": false, "b": false, "x": false, "y": false,
              "dpad_up": false, "dpad_down": false,
              "dpad_left": false, "dpad_right": false,
              "lb": false, "rb": false, "lt": false, "rt": false}
}
```

- **`lx,ly,rx,ry`** — the two analog sticks, each normalised to `[-1.0, 1.0]`
  (x: left `-1` / right `+1`, y: up `+1` / down `-1`).
- **`a,b,x,y`** — right thumb cluster (face buttons; cross/circle/square/triangle on PS).
- **`dpad_*`** — left thumb cluster (d-pad).
- **`lb,rb,lt,rt`** — the four on top (bumpers + triggers).

On the client the two front (thumb) clusters can also be read by **compass
direction** via `GamepadState`:

| alias | canonical | | alias | canonical |
|-------|-----------|-|-------|-----------|
| `LN` | dpad_up | | `RN` | y |
| `LE` | dpad_right | | `RE` | b |
| `LS` | dpad_down | | `RS` | a |
| `LW` | dpad_left | | `RW` | x |

## Install

```bash
pip install -e .
```

## Use

Pick one publisher.

**Joystick** (machine with the controller plugged in):

```bash
joyzmq-joystick                    # defaults: /dev/input/js0, tcp://*:5666
joyzmq-joystick --device /dev/input/js1 --bind tcp://*:6000
```

Uses the standard Linux xpad mapping (sticks on axes 0/1/3/4, triggers on 2/5,
d-pad on the 6/7 hat, face/bumper buttons 0–5). The stick Y axes are inverted so
up is `+1`. Triggers and the d-pad are thresholded into the `lt/rt` and `dpad_*`
buttons.

**Keyboard** (no special hardware or permissions — just a terminal):

```bash
joyzmq-keyboard                    # binds tcp://*:5666
```

| group | keys |
|-------|------|
| left stick | `W`/`S` = ly, `A`/`D` = lx |
| right stick | `Up`/`Down` = ry, `Left`/`Right` = rx |
| face (right) | `I`=y `K`=a `J`=x `L`=b |
| d-pad (left) | `T`=up `G`=down `F`=left `H`=right |
| top four | `Q`=lb `E`=rb `R`=lt `U`=rt |
| quit | `Ctrl-C` |

Stick keys **ramp**: while a direction key is held the axis moves toward `±1` at
`--ramp` units/second (default `2.0`, i.e. full deflection in 0.5 s), and ramps
back to `0` when released — so a quick tap nudges, a long hold goes full. Tune
with `--ramp`.

**Client** (same or another machine):

```bash
joyzmq-client                      # connects to tcp://localhost:5666
joyzmq-client --connect tcp://192.168.1.42:5666
```

The client prints a single, continuously updated line, e.g.:

```
lx:-0.03 ly:+0.98 rx:+0.00 ry:+0.00  |  a dpad_left
```

## As a library

Consume the stream on the client with `recv_states`, which yields
`GamepadState` objects. Front buttons are reachable by compass name, sticks and
the four on top by their canonical names:

```python
from joyzmq import recv_states

for pad in recv_states("tcp://localhost:5666"):
    if pad.RS:            # right-south face button (A / cross)
        fire()
    if pad.LW:            # d-pad left
        step_left()
    throttle = pad.ry     # right stick Y, in [-1.0, 1.0]
    print(pad.pressed())  # e.g. ['RS', 'LW', 'rb']
```

Or drive a publisher directly:

```python
from joyzmq import Joystick, Keyboard

with Joystick("/dev/input/js0") as js:
    while True:
        js.read_event()
        print(js.state())     # canonical {"axes": {...}, "buttons": {...}}

with Keyboard() as kb:
    while True:
        kb.poll(0.05)
        print(kb.state())     # same shape, same field names
```

## Notes

- **Joystick permissions:** if you get `PermissionError` on the device, add
  your user to the `input` group (`sudo usermod -aG input $USER`, then
  re-login). Find the device with `ls /dev/input/js*`.
- **Triggers as buttons:** on a real controller `lt/rt` are analog; they're
  reported pressed once pulled past `_TRIGGER_ON` (50%). Some pads report a
  trigger as `0` until first pulled — pull each trigger once after connecting.
- **Keyboard hold/ramp:** a terminal reports key presses/repeats but not
  releases, so a key stays active for `--hold` seconds (default 0.5) after its
  last auto-repeat, and the stick ramp-down only starts after that. Lower
  `--hold` for snappier release (but too low can flicker on the auto-repeat
  gap); raise `--ramp` for faster sticks.
- The keyboard server must run in a real terminal (it puts stdin in raw mode).
```
