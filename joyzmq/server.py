"""Joystick publisher: reads a joystick and PUBlishes its state over ZMQ."""

import json
import os

import zmq

from .joystick import Joystick, list_joysticks


def _print_joysticks(selected):
    """Print the available joysticks and how to pick a different one."""
    found = list_joysticks()
    if not found:
        print("[joyzmq] no joysticks found under /dev/input/js* "
              "-- is the controller plugged in and the joydev module loaded?")
        return
    print("[joyzmq] joysticks found:")
    for dev, name in found:
        mark = "*" if dev == selected else " "
        label = name if name else "(name unavailable -- check permissions)"
        suffix = "   <- using" if dev == selected else ""
        print(f"   {mark} {dev}  {label}{suffix}")
    others = [dev for dev, _ in found if dev != selected]
    if others:
        print(f"[joyzmq] more than one joystick: select another with "
              f"--device, e.g.  joyzmq-joystick --device {others[0]}")


def run_server(device="/dev/input/js0", bind="tcp://*:5666", topic="joy"):
    """Read `device` and publish the full joystick state on every event.

    The complete state is sent on each event, so a subscriber that joins late
    is fully in sync after the next stick movement or button press. Exits with
    a clear message (no traceback) if the device is missing, unreadable, or
    disconnects.
    """
    _print_joysticks(device)
    if not os.path.exists(device):
        print(f"[joyzmq] '{device}' is not available; not starting the publisher. "
              f"Plug in a controller (and 'sudo modprobe joydev' if needed), or "
              f"pass --device with one of the paths listed above.")
        return

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.PUB)
    sock.bind(bind)
    print(f"[joyzmq] publishing {device} on {bind} (topic '{topic}')")
    try:
        with Joystick(device) as js:
            while True:
                js.read_event()
                payload = json.dumps(js.state())
                sock.send_string(f"{topic} {payload}")
    except PermissionError:
        print(f"[joyzmq] no permission to read '{device}' -- add your user to the "
              f"'input' group (sudo usermod -aG input $USER), then re-login.")
    except EOFError:
        print(f"[joyzmq] joystick '{device}' disconnected; exiting.")
    except OSError as e:
        print(f"[joyzmq] error reading '{device}': {e}")
    finally:
        sock.close(linger=0)
