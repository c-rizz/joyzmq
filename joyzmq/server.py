"""Joystick publisher: reads a joystick and PUBlishes its state over ZMQ."""

import json

import zmq

from .joystick import Joystick


def run_server(device="/dev/input/js0", bind="tcp://*:5666", topic="joy"):
    """Read `device` and publish the full joystick state on every event.

    The complete state is sent on each event, so a subscriber that joins late
    is fully in sync after the next stick movement or button press.
    """
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
