"""Keyboard publisher: reads the keyboard and PUBlishes a canonical gamepad state.

Emits the same JSON state shape as the joystick server, so the standard
`joyzmq-client` works against it unchanged.
"""

import json

import zmq

from .keyboard import Keyboard

_HELP = (
    "  sticks: WASD (left) / arrows (right) -- ramp while held\n"
    "  face:   I=y K=a J=x L=b      d-pad: T=up G=down F=left H=right\n"
    "  top:    Q=lb E=rb R=lt U=rt      quit: Ctrl-C"
)


def run_keyboard_server(bind="tcp://*:5666", topic="joy", rate=20.0, hold=0.5, ramp=2.0):
    """Read the keyboard and publish the full state whenever it changes."""
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.PUB)
    sock.bind(bind)
    print(f"[joyzmq] publishing keyboard on {bind} (topic '{topic}')")
    print(_HELP)
    period = 1.0 / rate
    last_payload = None
    try:
        with Keyboard(hold=hold, ramp=ramp) as kb:
            while True:
                kb.poll(period)
                payload = json.dumps(kb.state())
                if payload != last_payload:
                    sock.send_string(f"{topic} {payload}")
                    last_payload = payload
    finally:
        sock.close(linger=0)
