"""Command-line entry points for the publishers and the subscriber."""

import argparse

from .client import run_client
from .keyboard_server import run_keyboard_server
from .server import run_server


def joystick_main():
    p = argparse.ArgumentParser(
        description="Read a joystick and publish its state over ZMQ."
    )
    p.add_argument("--device", default="/dev/input/js0", help="joystick device")
    p.add_argument("--bind", default="tcp://*:5666", help="ZMQ bind address")
    p.add_argument("--topic", default="joy", help="ZMQ topic prefix")
    p.add_argument(
        "--rate", type=float, default=20.0,
        help="idle heartbeat rate in Hz (republish state when no event; <=0 disables)",
    )
    args = p.parse_args()
    try:
        run_server(args.device, args.bind, args.topic, args.rate)
    except KeyboardInterrupt:
        print()


def keyboard_main():
    p = argparse.ArgumentParser(
        description="Read the keyboard and publish a joystick-like state over ZMQ."
    )
    p.add_argument("--bind", default="tcp://*:5666", help="ZMQ bind address")
    p.add_argument("--topic", default="joy", help="ZMQ topic prefix")
    p.add_argument("--rate", type=float, default=20.0, help="poll rate (Hz)")
    p.add_argument(
        "--hold", type=float, default=0.5,
        help="seconds a key stays active after its last repeat",
    )
    p.add_argument(
        "--ramp", type=float, default=2.0,
        help="stick ramp speed in units/sec (full deflection in 1/ramp s)",
    )
    args = p.parse_args()
    try:
        run_keyboard_server(args.bind, args.topic, args.rate, args.hold, args.ramp)
    except KeyboardInterrupt:
        print()


def web_main():
    p = argparse.ArgumentParser(
        description="Serve a web page that reads a gamepad or the keyboard, and publish its state over ZMQ, "
                    "optionally next to the images of camzmq cameras."
    )
    p.add_argument("--bind", default="tcp://*:5666", help="ZMQ bind address")
    p.add_argument("--topic", default="joy", help="ZMQ topic prefix")
    p.add_argument("--rate", type=float, default=20.0, help="publish rate (Hz)")
    p.add_argument(
        "--deadman", type=float, default=0.3,
        help="seconds without input from the page after which the neutral state is published",
    )
    p.add_argument(
        "--ramp", type=float, default=2.0,
        help="keyboard stick ramp speed in units/sec (full deflection in 1/ramp s)",
    )
    p.add_argument("--http-port", type=int, default=8090, help="port of the page")
    p.add_argument(
        "--http-bind", default="127.0.0.1",
        help="interface the page is served on (0.0.0.0 for other hosts; gamepads need http://localhost, use ssh -L)",
    )
    p.add_argument(
        "--cameras", action="append", default=None,
        help="metadata endpoint of a camzmq publisher, to show its cameras; repeat for more publishers",
    )
    p.add_argument(
        "--camera", action="append", default=None,
        help="camera to show, from whichever publisher has it; repeat for more (default: all)",
    )
    args = p.parse_args()
    from .web import run_web
    try:
        run_web(args.bind, args.topic, args.rate, args.deadman, args.ramp, args.http_bind, args.http_port,
                args.cameras, args.camera)
    except KeyboardInterrupt:
        print()


def client_main():
    p = argparse.ArgumentParser(
        description="Subscribe to joystick state published over ZMQ."
    )
    p.add_argument("--connect", default="tcp://localhost:5666", help="ZMQ address")
    p.add_argument("--topic", default="joy", help="ZMQ topic prefix")
    args = p.parse_args()
    try:
        run_client(args.connect, args.topic)
    except KeyboardInterrupt:
        print()
