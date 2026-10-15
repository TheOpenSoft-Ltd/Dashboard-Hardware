"""workers v7: the LDFM radar flowmeter driver in dropler.py (FLOW_SENSOR=LDFM).

The LDFM (JET / Shanghai Jiant) is a non-contact radar flowmeter: a velocity radar and a level radar in
one unit, RS485 Modbus-RTU like the old Doppler (JGDP-F22D150) but with its own register map and its
floats sent with the bytes reversed. The worker is executed up to its main loop with the network
libraries stubbed (as in test_station_status.py), and its serial client is swapped for a fake meter
whose registers come from the vendor's protocol document (V3), including its own example replies.
"""
import json
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ota"))
import selftest  # noqa: E402

WORKERS = os.path.join(ROOT, "src", "pat_smart", "modules", "workers")


def load_dropler(mode, flow_sensor=None, extra=None):
    """Execute dropler.py's body (minus its loop) with stubs; return (namespace, mqtt stub, env)."""
    tmp = tempfile.mkdtemp(prefix="ldfm-t-")
    env = selftest._fake_env(tmp, secure=False)
    env.update({"MODE": mode, "SENSOR_STATE_DIR": tmp})
    if flow_sensor:
        env["FLOW_SENSOR"] = flow_sensor
    env.update(extra or {})
    record = []
    path = os.path.join(WORKERS, "dropler.py")
    with open(path, encoding="utf-8") as f:
        code = selftest._body_before_main_loop(f.read(), path)
    stubs = selftest._stub_modules(record)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    ns = {"__name__": "dropler_under_test", "__file__": path}
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
    return ns, client, env


def f32(value):
    """A float as the meter sends it (two registers, bytes reversed)."""
    b = struct.pack("<f", value)
    return [(b[0] << 8) | b[1], (b[2] << 8) | b[3]]


class _Resp(object):
    def __init__(self, registers=None):
        self.registers = registers or []

    def isError(self):
        return registers_missing(self)


def registers_missing(resp):
    return not resp.registers


class FakeMeter(object):
    """Registers in, Modbus calls out - the same three calls the worker makes."""
    connected = True

    def __init__(self, regs):
        self.regs = dict(regs)
        self.writes = []

    def connect(self):
        return True

    def close(self):
        pass

    def read_holding_registers(self, address, count=1, device_id=1):
        vals = [self.regs.get(address + i) for i in range(count)]
        return _Resp(None if any(v is None for v in vals) else vals)

    def write_register(self, address, value, device_id=1):
        self.writes.append((address, [value]))
        self.regs[address] = value

    def write_registers(self, address, values, device_id=1):
        self.writes.append((address, list(values)))
        for i, v in enumerate(values):
            self.regs[address + i] = v


def meter(over=None):
    """A meter as installed: 2.00 m above the bed, 1.20 m pipe, water 0.80 m deep, flowing towards it."""
    regs = {
        0x0001: 1200, 0x0003: 800, 0x0004: 45, 0x0005: 200, 0x000A: 52, 0x000C: 1,
        0x000F: 0xE7FB, 0x0010: 0x493F,          # doc 2.1 reply: surface velocity
        0x0011: 0x292A, 0x0012: 0x463F,          # doc 2.3 reply: 0.77408 m3/s
        0x0019: 0x0000, 0x001A: 0x0000,          # silt 0.0 mm
        0x005E: 0, 0x0061: 2, 0x0062: 120, 0x0063: 1, 0x0064: 1, 0x0065: 1, 0x0066: 0,
        0xEA60: 6801, 0xEA61: 1, 0xEA62: 2, 0xEA63: 3,
    }
    for addr, val in zip((0x0013, 0x0015, 0x0017, 0x001D), (1234.5, 0.0, 0.5, 0.65)):
        regs[addr], regs[addr + 1] = f32(val)
    regs.update(over or {})
    return FakeMeter(regs)


FACTORY = {0x0005: 10000, 0x005E: 2, 0x0061: 0, 0x0062: 1, 0x0019: 0x0000, 0x001A: 0xC842}  # 0xC842 -> 100.0 mm


def published(client, topic):
    return [json.loads(a[1]) for n, a, k in client.calls if n == "publish" and a and a[0] == topic]


class Decoding(unittest.TestCase):
    def setUp(self):
        self.ns, _, _ = load_dropler("DROPLER", "LDFM")

    def test_vendor_example_replies(self):
        self.assertAlmostEqual(self.ns["ldfm_float"]([0x292A, 0x463F]), 0.77408, places=5)   # doc 2.3
        self.assertAlmostEqual(self.ns["ldfm_float"]([0xE7FB, 0x493F]), 0.78900, places=4)   # doc 2.1
        self.assertAlmostEqual(self.ns["ldfm_float"]([0x0000, 0xC842]), 100.0)              # silt default "C842"

    def test_round_trip_and_int16(self):
        for v in (0.0, 0.5, 12.25, 1234.5):
            self.assertAlmostEqual(self.ns["ldfm_float"](self.ns["ldfm_float_regs"](v)), v, places=3)
        self.assertEqual(self.ns["ldfm_int16"](0xFFFB), -5)
        self.assertEqual(self.ns["ldfm_int16"](52), 52)


class ConfiguredMeter(unittest.TestCase):
    def setUp(self):
        self.ns, self.mqtt, _ = load_dropler("DROPLER", "LDFM")
        self.ns["client"] = meter()

    def test_sample(self):
        s = self.ns["read_ldfm"]()
        self.assertAlmostEqual(s["level_m"], 0.8)
        self.assertAlmostEqual(s["flowrate"], 0.77408, places=5)
        self.assertAlmostEqual(s["velocity"], 0.65, places=5)
        self.assertAlmostEqual(s["cumulative"], 1234.5, places=2)
        self.assertEqual(s["direction"], "towards")
        info = self.ns["ldfm_info"]
        self.assertEqual(info["model_code"], 6801)
        self.assertEqual(info["warnings"], [])
        self.assertAlmostEqual(info["pitch_deg"], 5.2)

    def test_payloads_feed_the_backend_as_a_level_and_flow_station(self):
        flow, level = self.ns["ldfm_payloads"](self.ns["read_ldfm"](), "2026-10-15 05:00:00+00:00")
        for key in ("velocity", "flowrate", "cumulative_flow", "liquid_level"):
            self.assertIn(key, flow)
        self.assertIsNone(flow["temperature"])
        self.assertEqual(flow["sensor_model"], "LDFM")
        self.assertEqual(level["level"], 0.8)
        self.assertEqual(level["data_source"], "ldfm")
        self.assertEqual(self.ns["LEVEL_TOPIC"], "sensor/%s/radar" % self.ns["DEVICE_ID"])
        self.assertEqual(self.ns["REPORTED_MODE"], "FULL")
        status = self.ns["_status_payload"]("online")
        self.assertEqual((status["mode"], status["sensor_model"]), ("FULL", "LDFM"))

    def test_publish_sends_both_topics(self):
        self.ns["publish_ldfm_sample"]()
        self.assertEqual(len(published(self.mqtt, self.ns["TOPIC"])), 1)
        self.assertEqual(published(self.mqtt, self.ns["LEVEL_TOPIC"])[0]["level"], 0.8)

    def test_level_above_the_install_height_is_not_water(self):
        self.ns["client"] = meter({0x0003: 2500})
        self.assertIsNone(self.ns["read_ldfm"]()["level_m"])


class FactoryMeter(unittest.TestCase):
    def test_no_level_and_every_trap_named(self):
        ns, mqtt, _ = load_dropler("DROPLER", "LDFM")
        ns["client"] = meter(FACTORY)
        s = ns["read_ldfm"]()
        self.assertIsNone(s["level_m"])
        self.assertIsNone(ns["ldfm_payloads"](s, "t")[1])
        warnings = " | ".join(ns["ldfm_info"]["warnings"])
        for word in ("100 m", "cross-section", "direction filter", "silt"):
            self.assertIn(word, warnings)
        self.assertEqual(ns["client"].writes, [])           # nothing in .env -> nothing written

    def test_env_settings_written_once_then_left_alone(self):
        settings = {"LDFM_INSTALL_HEIGHT_CM": "250", "LDFM_SECTION_SHAPE": "circle", "LDFM_SECTION_P1_CM": "120",
                    "LDFM_DIRECTION_FILTER": "none", "LDFM_SILT_MM": "0"}
        ns, _, env = load_dropler("DROPLER", "LDFM", settings)
        ns["client"] = meter(FACTORY)
        with selftest._Env(**env):
            ns["ldfm_check_config"](0.0)
            first = list(ns["client"].writes)
            ns["ldfm_check_config"](0.0)
        self.assertEqual(sorted(a for a, _ in first), [0x0005, 0x0019, 0x005E, 0x0061, 0x0062])
        self.assertIn((0x0005, [250]), first)
        self.assertIn((0x0061, [2]), first)
        self.assertIn((0x005E, [0]), first)
        self.assertEqual(ns["client"].writes, first)        # second check: already set, no writes
        self.assertEqual(ns["ldfm_info"]["warnings"], [])
        self.assertEqual(ns["ldfm_info"]["install_height_cm"], 250)

    def test_bad_setting_does_not_stop_measuring(self):
        ns, _, env = load_dropler("DROPLER", "LDFM", {"LDFM_SECTION_SHAPE": "hexagon"})
        ns["client"] = meter()
        with selftest._Env(**env):
            s = ns["read_ldfm"]()
        self.assertAlmostEqual(s["flowrate"], 0.77408, places=5)
        self.assertIn("config_error", ns["ldfm_info"])


class OtherStations(unittest.TestCase):
    def test_doppler_default_is_unchanged(self):
        for mode in ("DROPLER", "FULL"):
            ns, _, _ = load_dropler(mode)
            self.assertEqual(ns["FLOW_SENSOR"], "JGDP")
            self.assertEqual(ns["SENSOR_MODEL"], "JGDP-F22D150")
            self.assertEqual(ns["REPORTED_MODE"], mode)
            self.assertFalse(ns["LDFM_LEVEL"])
            self.assertEqual(ns["MEASURES"], ["flow"])

    def test_heartbeat_says_what_the_worker_measures(self):
        # the backend's station sensor record: LDFM alone = level + flow, beside a level worker = flow
        self.assertEqual(load_dropler("DROPLER", "LDFM")[0]["MEASURES"], ["level", "flow"])
        self.assertEqual(load_dropler("FULL", "LDFM")[0]["MEASURES"], ["flow"])
        self.assertEqual(load_dropler("DROPLER", "LDFM", {"LDFM_PUBLISH_LEVEL": "0"})[0]["MEASURES"], ["flow"])

    def test_ldfm_beside_a_level_radar_leaves_the_level_to_it(self):
        ns, _, _ = load_dropler("FULL", "LDFM")
        ns["client"] = meter()
        self.assertFalse(ns["LDFM_LEVEL"])
        self.assertIsNone(ns["ldfm_payloads"](ns["read_ldfm"](), "t")[1])


if __name__ == "__main__":
    unittest.main()
