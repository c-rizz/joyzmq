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
    args = p.parse_args()
    try:
        run_server(args.device, args.bind, args.topic)
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
