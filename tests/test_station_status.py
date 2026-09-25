"""workers v4: ONE station status for a dual-sensor (MODE=FULL) station.

radar.py (level) and dropler.py (flow) share one DEVICE_ID and one status topic. Carey's rule
(2026-09-25): "use water level as main, if this is down, it can show as offline, if only flow rate
offline, it can show as error". Each worker leaves its own status in a file in RAM; the level worker
publishes the station status; the flow worker's own status and last will move to its own topic; both
heartbeats carry the same station status. Radar-only and flow-only stations behave as before.

The workers are scripts (no main guard): their bodies are executed up to the main loop with the
network libraries stubbed, the same way ota/selftest.py does it. Nothing here touches a network.
"""
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ota"))
import selftest  # noqa: E402

WORKERS = os.path.join(ROOT, "src", "pat_smart", "modules", "workers")


def load_worker(name, mode, state_dir):
    """Execute <name>'s body (minus its loop) with stubs and MODE; return (namespace, mqtt client stub)."""
    tmp = tempfile.mkdtemp(prefix="worker-t-")
    env = selftest._fake_env(tmp, secure=False)
    env.update({"MODE": mode, "SENSOR_STATE_DIR": state_dir, "CURRENT_POLL_S": "3600"})
    record = []
    path = os.path.join(WORKERS, name)
    with open(path, encoding="utf-8") as f:
        code = selftest._body_before_main_loop(f.read(), path)
    stubs = selftest._stub_modules(record)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    ns = {"__name__": "worker_under_test", "__file__": path}
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


def published(client, topic):
    return [json.loads(a[1]) for n, a, k in client.calls if n == "publish" and a and a[0] == topic]


class TheRule(unittest.TestCase):
    """The station status from (level, flow) - identical in both workers."""

    TABLE = [
        # level      flow       station
        ("online", "online", "online"),
        ("online", None, "online"),        # radar-only station, or flow still unknown at start-up
        ("online", "error", "error"),      # "if only flow rate offline, it can show as error"
        ("online", "offline", "error"),
        ("error", "online", "error"),      # level controller not answering: error, as on radar-only stations
        ("error", "offline", "error"),
        ("offline", "online", "offline"),  # "use water level as main, if this is down ... offline"
        ("offline", "error", "offline"),
        ("offline", None, "offline"),
        (None, "online", None),            # nothing known about the main sensor yet: say nothing
        (None, None, None),
    ]

    def test_both_workers_apply_the_same_rule(self):
        d = tempfile.mkdtemp()
        radar, _ = load_worker("radar.py", "FULL", d)
        dropler, _ = load_worker("dropler.py", "FULL", d)
        for level, flow, want in self.TABLE:
            self.assertEqual(radar["station_status"](level, flow), want, (level, flow))
            self.assertEqual(dropler["station_status"](level, flow), want, (level, flow))


class DualStation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sensor-state-")
        self.radar, self.rc = load_worker("radar.py", "FULL", self.dir)
        self.dropler, self.dc = load_worker("dropler.py", "FULL", self.dir)
        self.dev = self.radar["DEVICE_ID"]
        self.station_topic = "sensor/%s/status" % self.dev
        self.flow_topic = "sensor/%s/status/flow" % self.dev

    def set_state(self, ns, state):
        ns["state"] = state

    def test_a_flow_fault_is_a_station_error_and_both_heartbeats_agree(self):
        self.set_state(self.dropler, "FAULT")          # RS485 unplugged
        self.dropler["enter"]("FAULT")
        self.radar["enter"]("ONLINE")
        self.assertEqual(published(self.rc, self.station_topic)[-1]["status"], "error")
        self.assertEqual(published(self.rc, self.station_topic)[-1]["sensors"], {"level": "online", "flow": "error"})
        self.assertEqual(self.radar["station_view"]()[0], "error")
        self.assertEqual(self.dropler["station_view"]()[0], "error", "the flow worker's heartbeat says the same")

    def test_a_dead_level_sensor_is_offline_whatever_the_flow_meter_says(self):
        self.dropler["enter"]("ONLINE")
        self.radar["enter"]("SENSOR_FAULT")            # v3: controller says the level sensor is not measuring
        self.assertEqual(published(self.rc, self.station_topic)[-1]["status"], "offline")
        self.assertEqual(self.dropler["station_view"]()[0], "offline",
                         "the flow worker's heartbeat must also say offline, or the ingest drops the held offline")

    def test_recovery_goes_back_online_on_both(self):
        self.dropler["enter"]("FAULT")
        self.radar["enter"]("ONLINE")
        self.dropler["enter"]("ONLINE")
        self.radar["enter"]("ONLINE")                  # the level worker re-evaluates every loop
        self.assertEqual([p["status"] for p in published(self.rc, self.station_topic)], ["error", "online"])
        self.assertEqual(self.dropler["station_view"]()[0], "online")

    def test_the_flow_worker_never_speaks_for_the_station(self):
        self.dropler["enter"]("ONLINE")
        self.dropler["enter"]("FAULT")
        self.assertEqual(published(self.dc, self.station_topic), [], "nothing on the station topic from the flow worker")
        self.assertEqual([p["status"] for p in published(self.dc, self.flow_topic)], ["online", "error"])
        will = [k for n, a, k in self.dc.calls if n == "will_set"][0]
        self.assertEqual(will["topic"], self.flow_topic, "its last will is its own, not the station's")
        rwill = [k for n, a, k in self.rc.calls if n == "will_set"][0]
        self.assertEqual(rwill["topic"], self.station_topic, "the level worker's last will stays the station's")

    def test_a_dead_flow_worker_turns_the_station_to_error_not_offline(self):
        self.dropler["enter"]("ONLINE")
        self.radar["enter"]("ONLINE")
        self.radar["_started"] = time.monotonic() - 1000   # past the start-up grace
        future = time.time() + self.radar["SENSOR_STALE_S"] + 5
        self.assertEqual(self.radar["read_sensor_state"]("flow", future), "offline", "a stale file = the worker is gone")
        self.assertEqual(self.radar["station_view"](future)[0], "error")

    def test_a_dead_level_worker_makes_the_flow_heartbeat_say_offline(self):
        self.radar["enter"]("ONLINE")
        self.dropler["enter"]("ONLINE")
        self.dropler["_started"] = time.monotonic() - 1000
        future = time.time() + self.dropler["SENSOR_STALE_S"] + 5
        self.assertEqual(self.dropler["station_view"](future)[0], "offline")

    def test_start_up_grace_says_nothing_about_a_missing_partner(self):
        self.radar["enter"]("ONLINE")                  # no flow file yet, radar just started
        self.assertIsNone(self.radar["read_sensor_state"]("flow"))
        self.assertEqual(self.radar["station_view"]()[0], "online")

    def test_a_degraded_spell_keeps_the_last_status(self):
        self.dropler["enter"]("ONLINE")
        self.radar["enter"]("ONLINE")
        self.radar["enter"]("DEGRADED")                # 1-4 failed reads: not published, as before
        self.assertEqual([p["status"] for p in published(self.rc, self.station_topic)], ["online"])

    def test_the_state_file_is_in_the_shared_directory_and_complete(self):
        self.radar["enter"]("ONLINE")
        p = os.path.join(self.dir, "pat-smart-%s-level.json" % self.dev)
        with open(p) as f:
            d = json.load(f)
        self.assertEqual(d["status"], "online")
        self.assertEqual(d["state"], "ONLINE")
        self.assertFalse(os.path.exists(p + ".tmp"), "written atomically")


class SingleSensorStationsUnchanged(unittest.TestCase):
    def test_radar_only_station(self):
        d = tempfile.mkdtemp()
        radar, rc = load_worker("radar.py", "RADAR", d)
        self.assertIsNone(radar["OTHER_SENSOR"])
        radar["enter"]("ONLINE")
        radar["enter"]("FAULT")
        topic = "sensor/%s/status" % radar["DEVICE_ID"]
        self.assertEqual([p["status"] for p in published(rc, topic)], ["online", "error"])
        self.assertNotIn("sensors", published(rc, topic)[-1])
        self.assertEqual(os.listdir(d), [], "no state file on a radar-only station")

    def test_flow_only_station(self):
        d = tempfile.mkdtemp()
        dropler, dc = load_worker("dropler.py", "DROPLER", d)
        topic = "sensor/%s/status" % dropler["DEVICE_ID"]
        self.assertEqual(dropler["OWN_STATUS_TOPIC"], topic, "the flow meter IS the station here")
        dropler["enter"]("ONLINE")
        self.assertEqual([p["status"] for p in published(dc, topic)], ["online"])
        will = [k for n, a, k in dc.calls if n == "will_set"][0]
        self.assertEqual(will["topic"], topic)


if __name__ == "__main__":
    unittest.main(verbosity=2)
