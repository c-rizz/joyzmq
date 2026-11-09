"""joyzmq-web: the publisher behind the page (input, deadman, one controller) and its HTTP endpoints."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest
import zmq

from joyzmq.layout import neutral_state
from joyzmq.web import MultiView, WebPublisher, sanitize_state, serve


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _state(lx=0.0, **buttons):
    state = neutral_state()
    state["axes"]["lx"] = lx
    state["buttons"].update(buttons)
    return state


@pytest.fixture
def publisher():
    clock = FakeClock()
    pub = WebPublisher(bind="tcp://127.0.0.1:*", rate=50.0, deadman_s=0.3, clock=clock)
    yield pub, clock
    pub.close()


def test_sanitize_state():
    s = sanitize_state({"axes" : {"lx" : 3.0, "ly" : -0.5, "rx" : "x", "ry" : float("nan"), "zz" : 1.0},
                        "buttons" : {"a" : True, "b" : 1, "nope" : True}})
    assert s["axes"] == {"lx" : 1.0, "ly" : -0.5, "rx" : 0.0, "ry" : 0.0}
    assert s["buttons"]["a"] is True and s["buttons"]["b"] is False and "nope" not in s["buttons"]
    assert sanitize_state({}) == neutral_state()
    with pytest.raises(ValueError):
        sanitize_state([1, 2])


def test_one_controller_and_deadman(publisher):
    pub, clock = publisher
    assert pub.published_state() == neutral_state()
    assert pub.submit("A", _state(lx=0.5, a=True))["controlling"]
    reply = pub.submit("B", _state(lx=-1.0))
    assert not reply["controlling"] and reply["state"]["axes"]["lx"] == 0.5
    clock.t = 0.29
    assert pub.published_state()["axes"]["lx"] == 0.5
    clock.t = 0.31
    assert pub.published_state() == neutral_state() # A went silent
    assert pub.submit("B", _state(lx=-1.0))["controlling"] # anyone can take over a silent controller
    assert not pub.submit("A", _state(lx=0.5))["controlling"]
    reply = pub.submit("A", _state(lx=0.25), take=True)
    assert reply["controlling"] and reply["state"]["axes"]["lx"] == 0.25


def test_state_is_published_over_zmq(publisher):
    pub, clock = publisher
    sock = zmq.Context.instance().socket(zmq.SUB)
    sock.setsockopt_string(zmq.SUBSCRIBE, "joy")
    sock.setsockopt(zmq.RCVTIMEO, 2000)
    sock.connect(pub.endpoint)
    try:
        pub.submit("A", _state(lx=0.75, rt=True))
        deadline = time.monotonic() + 3.0
        while True: # the first heartbeats can be lost while the subscription propagates
            topic, _, payload = sock.recv_string().partition(" ")
            state = json.loads(payload)
            if state["axes"]["lx"] == 0.75 or time.monotonic() > deadline:
                break
        assert topic == "joy" and state == _state(lx=0.75, rt=True)
    finally:
        sock.close(linger=0)


class FakeView:
    def __init__(self, name="cam", seq=5):
        self.name = name
        self.seq = seq

    def cameras(self):
        return {"cameras" : [{"name" : self.name, "height" : 4, "width" : 6}], "colorbar" : "bar"}

    def frame(self, camera, after=None):
        if camera != self.name:
            raise KeyError(camera)
        return {"seq" : self.seq}

    def stats(self, camera):
        if camera != self.name:
            raise KeyError(camera)
        return {"frames" : 9}


def test_multi_view_routes_each_camera_to_its_publisher():
    view = MultiView([FakeView("left", seq=1), FakeView("right", seq=2)])
    described = view.cameras()
    assert [c["name"] for c in described["cameras"]] == ["left", "right"] and described["colorbar"] == "bar"
    assert view.frame("left")["seq"] == 1 and view.frame("right")["seq"] == 2
    assert view.stats("right")["frames"] == 9
    with pytest.raises(KeyError):
        view.frame("nope")
    with pytest.raises(ValueError, match="more than one"):
        MultiView([FakeView("cam"), FakeView("cam")])


def _post(url, body):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST")))


@pytest.mark.parametrize("view", [None, FakeView()])
def test_http_endpoints(publisher, view):
    pub, clock = publisher
    server = serve(pub, "127.0.0.1", 0, view=view, ramp=3.0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert b"joyzmq" in urllib.request.urlopen(base + "/").read()
        config = json.load(urllib.request.urlopen(base + "/api/config"))
        assert (config["rate"], config["deadman_s"], config["ramp"]) == (50.0, 0.3, 3.0)
        reply = _post(base + "/api/input", {"client" : "A", "state" : _state(lx=0.5)})
        assert reply["controlling"] and reply["state"]["axes"]["lx"] == 0.5
        assert not _post(base + "/api/input", {"client" : "B", "state" : _state()})["controlling"]
        assert _post(base + "/api/input", {"client" : "B", "state" : _state(), "take" : True})["controlling"]
        bad = [("/api/input", b"not json"), ("/api/input", {"state" : {}}), ("/api/input", {"client" : "A", "state" : 3}),
               ("/api/other", {"client" : "A"})]
        for path, body in bad:
            with pytest.raises(urllib.error.HTTPError) as err:
                _post(base + path, body)
            assert err.value.code == (404 if path == "/api/other" else 400)
        if view is None:
            assert config["cameras"] is None
            missing = ["/api/frame/cam", "/api/stats/cam"]
        else:
            assert [c["name"] for c in config["cameras"]] == ["cam"]
            assert json.load(urllib.request.urlopen(base + "/api/frame/cam?after=4"))["seq"] == 5
            assert json.load(urllib.request.urlopen(base + "/api/stats/cam"))["frames"] == 9
            missing = ["/api/frame/nope", "/api/stats/nope"]
        for path in missing:
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(base + path)
            assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
