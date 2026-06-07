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


## Quick Usage - No install

Start the server up with:

```
uvx --from git+https://github.com/c-rizz/joyzmq joyzmq-joystick --bind tcp://127.0.0.1:5666
```

On the client side, you can check that everything works with the client as:
```
uvx --from git+https://github.com/c-rizz/joyzmq joyzmq-client --connect tcp://127.0.0.1:5666
```

Then, to properly use the client within your python application, you will need to install joyzmq on
the client side and use it as a python package within your code.

## Install

```bash
uv pip install git+https://github.com/c-rizz/joyzmq
```


## Use

Pick one publisher.

**Joystick** (machine with the controller plugged in):

```bash
joyzmq-joystick                    # defaults: /dev/input/js0, tcp://*:5666
joyzmq-joystick --device /dev/input/js1 --bind tcp://*:6000
```

On startup it lists the connected joysticks **by name** and, when more than one
is present, tells you how to pick another with `--device`:

```
[joyzmq] joysticks found:
   * /dev/input/js0  Microsoft X-Box 360 pad   <- using
     /dev/input/js1  Sony Interactive Entertainment Wireless Controller
[joyzmq] more than one joystick: select another with --device, e.g.  joyzmq-joystick --device /dev/input/js1
```

(The name comes from the kernel's `JSIOCGNAME`; it shows `(name unavailable)` if
the node exists but can't be opened — usually the `input`-group permission.)

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
joyzmq-client --connect tcp://127.0.0.1:5666
```

The client prints a single, continuously updated line, e.g.:

```
lx:-0.03 ly:+0.98 rx:+0.00 ry:+0.00  |  a dpad_left
```

## As a library

For a **real-time control loop**, use `GamepadTeleop`. A background thread
receives *every* published state (so no button press is ever missed, even if
they arrive faster than you poll), while you read the latest state without
blocking. This is the pattern the adarl locomotion teleop uses — poll once per
control step, read sticks as continuous values, and use `pressed_edge` for
one-shot button actions:

```python
from joyzmq import GamepadTeleop

gp = GamepadTeleop("tcp://localhost:5666")   # or ipc:///abs/path/joy.ipc
try:
    while running:                       # e.g. your env-step loop
        pad = gp.poll()                  # latest state, never blocks
        forward = pad.ly                 # sticks are floats in [-1.0, 1.0]
        turn    = pad.rx
        if gp.pressed_edge("RS"):        # pressed since last poll (A / cross)
            toggle_stop()
        if gp.pressed_edge("RN"):        # north face button (Y / triangle)
            terminate()
finally:
    gp.close()
```

`poll()` returns the most recent `GamepadState` and remembers which buttons were
pressed since the previous poll; `pressed_edge(name)` reports those rising edges,
so a quick tap between two polls is never lost. Names may be canonical (`"a"`) or
compass aliases (`"RS"` — see [Canonical layout](#canonical-layout)).

Prefer to handle the press set yourself? `GamepadClient.poll()` returns it
directly as `(latest_state, pressed_set)`:

```python
from joyzmq import GamepadClient

gp = GamepadClient("tcp://localhost:5666")
pad, pressed = gp.poll()
if "RS" in pressed:      # canonical "a" is also in the set
    jump()
```

For simple scripts that keep up with the stream, iterate every state with
`recv_states`. Note a SUB socket *queues* messages, so a slow consumer reads a
growing backlog of stale states — prefer the poll-style above for control loops:

```python
from joyzmq import recv_states

for pad in recv_states("tcp://localhost:5666"):
    if pad.RS:                # right-south face button (A / cross)
        fire()
    throttle = pad.ry         # right stick Y, in [-1.0, 1.0]
    print(pad.pressed())      # e.g. ['RS', 'LW', 'rb']
```

Or drive a publisher directly (no ZMQ involved):

```python
from joyzmq import Joystick, Keyboard, list_joysticks

print(list_joysticks())       # [('/dev/input/js0', 'Microsoft X-Box 360 pad'), ...]

with Joystick("/dev/input/js0") as js:
    while True:
        js.read_event()
        print(js.state())     # canonical {"axes": {...}, "buttons": {...}}

with Keyboard() as kb:
    while True:
        kb.poll(0.05)
        print(kb.state())     # same shape, same field names
```

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
- **Across machines / Docker:** the endpoint is just a ZMQ address. Use
  `tcp://<host>:5666` for another machine (open the port; bind the publisher on
  the host, not inside a rootless container), or `ipc:///shared/path/joy.ipc`
  over a shared bind-mount to reach a container without any network setup.
```
