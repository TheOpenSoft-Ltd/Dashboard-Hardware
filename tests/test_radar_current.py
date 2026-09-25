"""radar.py loop-current reading (workers v2): parsing, page discovery, staleness, payload shape.

radar.py is a script (no main guard), so its module body is executed up to the main loop the
same way ota/selftest.py does it, with the network libraries stubbed and the HTTP layer
replaced by a fake VEGAMET. Nothing here touches a network.
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ota"))
import selftest  # noqa: E402  (the bundle's own helpers: stubs + body-before-loop)

RADAR = os.path.join(ROOT, "src", "pat_smart", "modules", "workers", "radar.py")

# a plausible VEGAMET input.htm: German UI, comma decimal, the value and the unit in separate cells
PAGE = """<html><head><link rel="stylesheet" href="/000/format.css"></head><body>
<table><tr><td><font size="2">Stromeingang</font></td>
<td align="right"><font size="2">{value}</font></td><td align="center"><font size="2">mA</font></td></tr>
<tr><td>HART Sensoren</td><td>Sensor</td></tr></table></body></html>"""


def load_radar(env_extra=None):
    """Execute radar.py's body (minus its loop) into a namespace with stubs; return (ns, record)."""
    tmp = tempfile.mkdtemp(prefix="radar-t-")
    env = selftest._fake_env(tmp, secure=False)
    env["CURRENT_POLL_S"] = "3600"          # the background thread must not poll during a test
    env.update(env_extra or {})
    record = []
    with open(RADAR, encoding="utf-8") as f:
        code = selftest._body_before_main_loop(f.read(), RADAR)
    stubs = selftest._stub_modules(record)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    ns = {"__name__": "radar_under_test", "__file__": RADAR}
    try:
        with selftest._Env(**env):
            exec(code, ns)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return ns, record


class FakeHttp(object):
    """Stands in for radar._http_get: root redirects to /049/index.htm, input.htm carries the value."""

    def __init__(self, value="5,296", prefix="/049/", fail=False):
        self.value, self.prefix, self.fail, self.calls = value, prefix, fail, []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if self.fail:
            raise OSError("connection refused")
        host = url.split("//", 1)[1].split("/", 1)[0]
        if url.endswith("/") and url.count("/") == 3:
            return "http://%s%sindex.htm" % (host, self.prefix), "<html>index</html>"
        return url, PAGE.format(value=self.value)


class RadarLoopCurrent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns, cls.record = load_radar()

    def test_parse_comma_and_dot_decimals(self):
        p = self.ns["parse_current_ma"]
        self.assertAlmostEqual(p(PAGE.format(value="5,296")), 5.296)
        self.assertAlmostEqual(p(PAGE.format(value="4.000")), 4.0)
        self.assertAlmostEqual(p(PAGE.format(value="21,7")), 21.7)
        self.assertIsNone(p("<html>no current here</html>"))
        self.assertIsNone(p(PAGE.format(value="")))

    def test_prefix_comes_from_the_root_redirect(self):
        fake = FakeHttp(prefix="/049/")
        self.ns["_http_get"] = fake
        self.assertEqual(self.ns["discover_current_prefix"](), "/049/")
        self.ns["_http_get"] = FakeHttp(prefix="/007/")
        self.assertEqual(self.ns["discover_current_prefix"](), "/007/")

    def test_read_current_once_hits_input_htm_under_the_prefix(self):
        fake = FakeHttp(value="4,001")
        self.ns["_http_get"] = fake
        self.assertAlmostEqual(self.ns["read_current_once"]("/049/"), 4.001)
        self.assertTrue(fake.calls[-1].endswith("/049/input.htm"), fake.calls)
        self.assertIn(self.ns["CURRENT_HOST"], fake.calls[-1])

    def test_snapshot_is_none_when_never_read_or_stale(self):
        cur, snap = self.ns["_current"], self.ns["current_snapshot"]
        with self.ns["_current_lock"]:
            cur["ma"], cur["ts"] = None, 0.0
        self.assertIsNone(snap())
        with self.ns["_current_lock"]:
            cur["ma"], cur["ts"] = 5.3, time.monotonic()
        self.assertEqual(snap(), 5.3)
        with self.ns["_current_lock"]:
            cur["ts"] = time.monotonic() - self.ns["CURRENT_STALE_S"] - 1
        self.assertIsNone(snap(), "a reading older than CURRENT_STALE_S must not be published")

    def test_payload_carries_level_current_and_source(self):
        cur = self.ns["_current"]
        with self.ns["_current_lock"]:
            cur["ma"], cur["ts"] = 5.29612, time.monotonic()
        data = self.ns["build_payload"](0.0732, "modbus_pv2")
        self.assertEqual(data["level"], 0.07)
        self.assertEqual(data["current_ma"], 5.296)
        self.assertEqual(data["data_source"], "modbus_pv2")
        for k in ("station_id", "device_id", "station_name", "date_time"):
            self.assertIn(k, data)
        with self.ns["_current_lock"]:
            cur["ma"] = None
        self.assertIsNone(self.ns["build_payload"](0.0, "dry_pipe")["current_ma"])

    def test_level_path_does_not_depend_on_the_page(self):
        # the page layer is a separate thread reading a cache; read_level() never calls it
        import inspect
        src = inspect.getsource(self.ns["read_level"])
        self.assertNotIn("_http_get", src)
        self.assertNotIn("current", src.lower().replace("data_source", ""))

    def test_config_defaults(self):
        self.assertEqual(self.ns["CURRENT_HOST"], self.ns["MODBUS_HOST"])
        # NAMUR NE43 failure limits (v3); 3.8/20.5 called PIR010's dry 3.799 mA a fault
        self.assertEqual(self.ns["LOOP_MIN_MA"], 3.6)
        self.assertEqual(self.ns["LOOP_MAX_MA"], 21.0)
        self.assertEqual(self.ns["SENSOR_FAULT_AFTER_S"], 120)
        self.assertEqual(self.ns["SENSOR_OK_AFTER_S"], 30)

    def test_http_host_can_be_overridden(self):
        ns, _ = load_radar({"VEGAMET_HTTP_HOST": "10.0.0.9"})
        self.assertEqual(ns["CURRENT_HOST"], "10.0.0.9")


# the page as PIT043's controller showed it on 2026-09-25 (sensor F013 -> failure current -> E 015),
# with the label repeated as a heading above the table like the real page
PAGE_HEADED = """<html><body><h3>Stromeingang</h3><table><tr><td>Eingang</td><td>Wert</td><td>Einheit</td></tr>
<tr><td>Stromeingang</td><td align="right">{value}</td><td>mA</td></tr></table></body></html>"""
PAGE_EN = "<html><table><tr><td>current input</td><td>{value}</td><td>mA</td></tr></table></html>"


class RadarSensorFault(unittest.TestCase):
    """workers v3: a sensor the controller calls dead is sensor_fault (not dry_pipe) and, once that
    has held for SENSOR_FAULT_AFTER_S, the station is offline."""

    @classmethod
    def setUpClass(cls):
        cls.ns, cls.record = load_radar()

    def setUp(self):
        with self.ns["_current_lock"]:
            self.ns["_current"].update(ma=None, err=None, ts=0.0)
            self.ns["_sensor"].update(fault_since=None, ok_since=None, down=False, evidence_ts=0.0)

    def fresh(self, ma, err=None):
        with self.ns["_current_lock"]:
            self.ns["_current"].update(ma=ma, err=err, ts=time.monotonic())

    def test_parse_input_reads_the_labelled_cell(self):
        p = self.ns["parse_input"]
        self.assertEqual(p(PAGE.format(value="5,296")), (5.296, None))
        self.assertEqual(p(PAGE.format(value="E 015")), (None, "E015"))
        self.assertEqual(p(PAGE.format(value="E015")), (None, "E015"))
        self.assertEqual(p(PAGE.format(value="E 13")), (None, "E013"))
        self.assertEqual(p(PAGE_HEADED.format(value="7,044")), (7.044, None), "the heading above the table is not the cell")
        self.assertEqual(p(PAGE_HEADED.format(value="E 015")), (None, "E015"))
        self.assertEqual(p(PAGE_EN.format(value="16,106")), (16.106, None), "PIT002's English page")
        self.assertEqual(p(PAGE.format(value="")), (None, None), "blank cell: no evidence either way")
        self.assertEqual(p("<html>Messwert 5,1 mA</html>"), (5.1, None), "no label: first number, as v2")
        self.assertEqual(p("<html>E 015 but no labelled row</html>"), (None, None), "a code only counts from the row")
        self.assertEqual(self.ns["parse_current_ma"](PAGE.format(value="E 015")), None)

    def test_the_verdict_follows_the_controller_and_namur(self):
        code = self.ns["sensor_fault_code"]
        self.assertEqual(code(None, "E015"), "E015")
        self.assertEqual(code(None, "E021"), "E021")
        self.assertEqual(code(3.6, None), "LOOP_LOW")
        self.assertEqual(code(3.55, None), "LOOP_LOW")
        self.assertEqual(code(21.0, None), "LOOP_HIGH")
        for measuring in (3.799, 3.61, 4.0, 12.0, 20.5, 20.99):
            self.assertIsNone(code(measuring, None), "%s mA is a reading (dry / full scale)" % measuring)
        self.assertIsNone(code(None, None))

    def test_a_dead_sensor_is_sensor_fault_on_every_sample_at_once(self):
        self.fresh(None, "E015")
        data, state = self.ns["classify_sample"](0.0, "dry_pipe")
        self.assertEqual(data["data_source"], "sensor_fault")
        self.assertEqual(data["fault_code"], "E015")
        self.assertEqual(data["level"], 0.0, "the raw PV still goes out; the ingest keeps it as rawLevel")
        self.assertEqual(state, "ONLINE", "the station state waits for the verdict to hold")

    def test_a_dry_pipe_stays_a_dry_pipe(self):
        self.fresh(4.0)
        data, state = self.ns["classify_sample"](0.0, "dry_pipe")
        self.assertEqual((data["data_source"], data["fault_code"], state), ("dry_pipe", None, "ONLINE"))
        self.fresh(3.799)
        self.assertEqual(self.ns["classify_sample"](0.0, "dry_pipe")[0]["data_source"], "dry_pipe")

    def test_no_fresh_reading_no_claim(self):
        with self.ns["_current_lock"]:
            self.ns["_current"].update(ma=None, err="E015", ts=time.monotonic() - self.ns["CURRENT_STALE_S"] - 1)
        data, _ = self.ns["classify_sample"](0.0, "dry_pipe")
        self.assertEqual((data["data_source"], data["fault_code"]), ("dry_pipe", None))
        with self.ns["_current_lock"]:
            self.ns["_current"].update(ma=None, err=None, ts=0.0)
        self.assertIsNone(self.ns["fault_snapshot"]())

    def test_offline_only_after_the_fault_holds_and_online_after_good_readings(self):
        note, down = self.ns["note_reading"], self.ns["sensor_down"]
        t0 = 1000.0
        for t in range(0, 120, 10):                      # 0..110 s of E 015: an echo lost in rain, not yet dead
            note(None, "E015", t0 + t)
        self.assertFalse(down())
        note(None, "E015", t0 + 120)
        self.assertTrue(down(), "held for SENSOR_FAULT_AFTER_S -> offline")
        self.fresh(None, "E015")
        self.assertEqual(self.ns["classify_sample"](0.0, "dry_pipe")[1], "SENSOR_FAULT")
        note(7.2, None, t0 + 130)                        # one good reading is not a recovery
        note(None, "E015", t0 + 140)
        self.assertTrue(down())
        for t in range(150, 180, 10):
            note(7.2, None, t0 + t)
        self.assertTrue(down(), "20 s of good readings: still offline")
        note(7.2, None, t0 + 180)
        self.assertFalse(down(), "SENSOR_OK_AFTER_S of good readings -> online")

    def test_a_short_fault_never_goes_offline(self):
        note, down = self.ns["note_reading"], self.ns["sensor_down"]
        t0 = 5000.0
        for t in range(0, 100, 10):
            note(None, "E015", t0 + t)                   # 90 s of faults
        note(6.1, None, t0 + 100)                        # echo back before the hold ran out
        for t in range(110, 220, 10):
            note(None, "E015", t0 + t)                   # another 100 s: the clock restarted at 110
        self.assertFalse(down(), "the fault clock restarts after a good reading")

    def test_blank_cells_and_an_unreadable_page_drop_a_stale_verdict(self):
        note, down = self.ns["note_reading"], self.ns["sensor_down"]
        t0 = 9000.0
        for t in range(0, 130, 10):
            note(None, "E015", t0 + t)
        self.assertTrue(down())
        note(None, None, t0 + 150)                       # blank: no evidence, verdict kept for now
        self.assertTrue(down())
        note(None, None, t0 + 120 + self.ns["CURRENT_STALE_S"] + 1)
        self.assertFalse(down(), "no evidence for CURRENT_STALE_S -> not kept on nothing")
        for t in range(0, 130, 10):
            note(None, "E015", t0 + 1000 + t)
        self.assertTrue(down())
        self.ns["note_unreadable"](t0 + 1120 + self.ns["CURRENT_STALE_S"] + 1)
        self.assertFalse(down(), "page unreachable past CURRENT_STALE_S -> verdict dropped")

    def test_the_station_reports_offline_on_both_channels(self):
        self.assertEqual(self.ns["STATE_STATUS"]["SENSOR_FAULT"], "offline")
        self.assertEqual(self.ns["HEARTBEAT_STATUS"]["SENSOR_FAULT"], "offline",
                         "the ingest releases a held offline only while the heartbeat also says offline")
        self.assertEqual(self.ns["HEARTBEAT_STATUS"].get("DEGRADED", "degraded"), "degraded")
        self.assertEqual(self.ns["HEARTBEAT_STATUS"]["ONLINE"], "online")


if __name__ == "__main__":
    unittest.main(verbosity=2)
