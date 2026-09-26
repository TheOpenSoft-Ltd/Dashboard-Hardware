"""The camera stream supervisor re-asserts its status after an MQTT reconnect.

Found 2026-09-26: the supervisor's status is edge-triggered and its last will is a retained
"offline". After any reconnect the broker published the will and the supervisor, still streaming,
never said "online" again - 41 cameras sat "offline" in the recorder while the media server had
them live. radar.py and dropler.py already re-assert on connect; this pins the same for the camera.

The worker is a script (no main guard): its body is executed up to the main loop with the network
libraries stubbed, the same way ota/selftest.py does it. Nothing here touches a network.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ota"))
import selftest  # noqa: E402

PATH = os.path.join(ROOT, "src", "pat_smart", "modules", "workers", "stream_supervisor.py")


def load_supervisor():
    env = selftest._fake_env(tempfile.mkdtemp(prefix="stream-t-"), secure=False)
    record = []
    with open(PATH, encoding="utf-8") as f:
        code = selftest._body_before_main_loop(f.read(), PATH)
    stubs = selftest._stub_modules(record)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    ns = {"__name__": "worker_under_test", "__file__": PATH}
    try:
        with selftest._Env(**env):
            exec(code, ns)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    client = [r for r in record if r.kind == "mqtt.Client"][0]
    return ns, client


class ReassertOnReconnect(unittest.TestCase):
    def setUp(self):
        self.ns, self.client = load_supervisor()
        self.topic = self.ns["STATUS_TOPIC"]

    def statuses(self):
        return [json.loads(a[1]) for n, a, k in self.client.calls if n == "publish" and a and a[0] == self.topic]

    def reconnect(self, rc=0):
        self.client.on_connect(self.client, None, {}, rc)

    def test_on_connect_is_wired(self):
        self.assertTrue(callable(self.client.on_connect))

    def test_last_will_is_offline_and_says_why(self):
        will = json.loads(self.client.called("will_set")[0][1][1])
        self.assertEqual(will["status"], "offline")
        self.assertEqual(will["fault_reason"], "mqtt_disconnected")
        self.assertEqual(will["device_id"], self.ns["DEVICE_ID"])

    def test_streaming_is_said_again_after_a_reconnect(self):
        self.ns["enter"]("STREAMING")
        self.reconnect()
        self.assertEqual([s["status"] for s in self.statuses()], ["online", "online"])
        self.assertTrue(self.client.called("publish")[-1][2].get("retain"))

    def test_a_fault_is_said_again_with_its_reason(self):
        self.ns["enter"]("FAULT", "camera_unreachable")
        self.reconnect()
        last = self.statuses()[-1]
        self.assertEqual((last["status"], last["fault_reason"]), ("error", "camera_unreachable"))

    def test_never_forces_online_before_the_first_status(self):
        self.reconnect()  # still STARTING: the will's "offline" stands until the stream is up
        self.assertEqual(self.statuses(), [])

    def test_a_degraded_spell_keeps_the_last_status(self):
        self.ns["enter"]("STREAMING")
        self.ns["enter"]("DEGRADED", "frozen")  # brief gap before a restart: status unchanged
        self.reconnect()
        self.assertEqual([s["status"] for s in self.statuses()], ["online", "online"])

    def test_a_failed_connect_publishes_nothing(self):
        self.ns["enter"]("STREAMING")
        self.reconnect(rc=5)
        self.assertEqual(len(self.statuses()), 1)


class PeriodicReassert(unittest.TestCase):
    """After a healer restart the OLD connection's will lands ~90 s after the new process said online;
    no reconnect happens, so only a periodic re-assert overwrites it."""

    def setUp(self):
        self.ns, self.client = load_supervisor()
        self.topic = self.ns["STATUS_TOPIC"]
        self.every = self.ns["STATUS_REASSERT_S"]

    def statuses(self):
        return [json.loads(a[1])["status"] for n, a, k in self.client.calls if n == "publish" and a and a[0] == self.topic]

    def test_nothing_before_the_first_status(self):
        self.ns["reassert_status"](now=10 ** 9)
        self.assertEqual(self.statuses(), [])

    def test_repeats_the_status_once_per_interval(self):
        self.ns["enter"]("STREAMING")
        t0 = self.ns["_last_assert"]
        self.ns["reassert_status"](now=t0 + self.every / 2)       # too soon
        self.assertEqual(self.statuses(), ["online"])
        self.ns["reassert_status"](now=t0 + self.every + 1)       # a late will may have landed: say it again
        self.assertEqual(self.statuses(), ["online", "online"])
        self.ns["reassert_status"](now=t0 + self.every + 2)       # and not again right away
        self.assertEqual(self.statuses(), ["online", "online"])

    def test_repeats_a_fault_too(self):
        self.ns["enter"]("FAULT", "ams_unreachable")
        self.ns["reassert_status"](now=self.ns["_last_assert"] + self.every + 1)
        self.assertEqual(self.statuses(), ["error", "error"])


if __name__ == "__main__":
    unittest.main()
