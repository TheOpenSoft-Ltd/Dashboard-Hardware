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
        self.assertEqual(self.ns["LOOP_MIN_MA"], 3.8)
        self.assertEqual(self.ns["LOOP_MAX_MA"], 20.5)

    def test_http_host_can_be_overridden(self):
        ns, _ = load_radar({"VEGAMET_HTTP_HOST": "10.0.0.9"})
        self.assertEqual(ns["CURRENT_HOST"], "10.0.0.9")


if __name__ == "__main__":
    unittest.main(verbosity=2)
