"""Canonical gamepad layout shared by every joyzmq publisher.

Modelled on a standard Xbox / PlayStation controller so the client sees the
same named axes and buttons no matter which publisher produced them.

    axes (each normalised to [-1.0, 1.0]):
        lx, ly      left analog stick   (x: left -1 / right +1, y: up +1 / down -1)
        rx, ry      right analog stick

    buttons (bool):
        a, b, x, y                              right thumb cluster (face buttons)
        dpad_up, dpad_down, dpad_left, dpad_right   left thumb cluster (d-pad)
        lb, rb, lt, rt                          the four on top (bumpers + triggers)
"""

# Analog sticks, in canonical order.
AXES = ("lx", "ly", "rx", "ry")

# Digital buttons, grouped: face cluster, d-pad, then the four on top.
BUTTONS = (
    "a", "b", "x", "y",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
    "lb", "rb", "lt", "rt",
)


def neutral_state():
    """A fresh, fully-populated state with everything at rest."""
    return {
        "axes": {name: 0.0 for name in AXES},
        "buttons": {name: False for name in BUTTONS},
    }
