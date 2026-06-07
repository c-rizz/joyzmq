"""Joystick publisher: reads a joystick and PUBlishes its state over ZMQ."""

import json

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
    is fully in sync after the next stick movement or button press.
    """
    _print_joysticks(device)
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
    finally:
        sock.close(linger=0)
