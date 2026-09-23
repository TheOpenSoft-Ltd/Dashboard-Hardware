#!/usr/bin/env python3
"""selftest.py - prove a worker bundle with the interpreter that will run it.

Ships INSIDE dist/workers.tar.gz. The node updater runs it with the node's venv python
(3.9+ on the IRIV signs, 3.13 on the RPi5 stations) BEFORE the running set is touched;
build_workers.py --check runs it on the laptop / CI with whatever python is there.
stdlib only. The workers' third-party imports (paho, redis, pymodbus, dotenv) are
STUBBED for the wired-import check, so nothing connects to anything and the check works
where those packages are absent; a separate step probes the REAL dependencies in a
subprocess and fails only when SELFTEST_REQUIRE_DEPS=1 (the node updater sets it).

Checks
  1. bundle identity   WORKERS_VERSION is digits, BUILD present, every expected file present
  2. py_compile        every .py compiles (to a scratch dir, never into the bundle)
  3. bash -n           stream.sh parses (skipped with a note when bash is absent)
  4. wired import      radar.py, dropler.py, stream_supervisor.py: the module body is
                       executed up to (not including) its main `while True:` loop against a
                       fake .env with the network libraries stubbed - twice for the MQTT
                       workers (certs+credentials present / absent) so both the TLS and the
                       plaintext branch run; build_ffmpeg() in both audio modes
  5. real deps         paho.mqtt.client, redis, pymodbus.client, dotenv import with THIS
                       python (subprocess, unstubbed) - FAIL only with SELFTEST_REQUIRE_DEPS=1

Exit 0 = PASS. Non-zero = FAIL with the list of failures; the caller must leave the
running set exactly as it was.
"""
import ast
import io
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
WORKERS = ("radar.py", "dropler.py", "stream.sh", "stream_supervisor.py")
IDENTITY = ("WORKERS_VERSION", "BUILD")
REAL_DEPS = ("paho.mqtt.client", "redis", "pymodbus.client", "dotenv")


# ----------------------------------------------------------------------------- stubs
class _Recorder(object):
    """Any method call is recorded and returns True; attributes may be assigned freely."""

    def __init__(self, kind):
        self.kind = kind
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def _call(*a, **k):
            self.calls.append((name, a, k))
            return True

        return _call

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


def _stub_modules(record):
    """Return {module name: stub module} for the four network libraries."""
    paho = types.ModuleType("paho")
    paho_mqtt = types.ModuleType("paho.mqtt")
    paho_client = types.ModuleType("paho.mqtt.client")

    class Client(_Recorder):
        def __init__(self, *a, **k):
            _Recorder.__init__(self, "mqtt.Client")
            self.on_connect = None
            self.on_disconnect = None
            record.append(self)

    paho_client.Client = Client
    paho_client.CallbackAPIVersion = types.SimpleNamespace(VERSION1=1, VERSION2=2)
    paho.mqtt = paho_mqtt
    paho_mqtt.client = paho_client

    redis = types.ModuleType("redis")

    class Redis(_Recorder):
        def __init__(self, *a, **k):
            _Recorder.__init__(self, "redis.Redis")
            record.append(self)

    redis.Redis = Redis

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: True

    pymodbus = types.ModuleType("pymodbus")
    pymodbus_client = types.ModuleType("pymodbus.client")

    class _Modbus(_Recorder):
        DATATYPE = types.SimpleNamespace(FLOAT32="float32")

        def __init__(self, *a, **k):
            _Recorder.__init__(self, "modbus")
            record.append(self)

        @staticmethod
        def convert_from_registers(*a, **k):
            return 0.0

    pymodbus_client.ModbusTcpClient = type("ModbusTcpClient", (_Modbus,), {})
    pymodbus_client.ModbusSerialClient = type("ModbusSerialClient", (_Modbus,), {})
    pymodbus.client = pymodbus_client

    return {
        "paho": paho, "paho.mqtt": paho_mqtt, "paho.mqtt.client": paho_client,
        "redis": redis, "dotenv": dotenv,
        "pymodbus": pymodbus, "pymodbus.client": pymodbus_client,
    }


def _body_before_main_loop(src, filename):
    """Compile the module body up to (not including) its first top-level `while`."""
    tree = ast.parse(src, filename=filename)
    cut = None
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.While):
            cut = i
            break
    if cut is None:
        raise AssertionError("no module-level while loop found - layout changed, update selftest")
    tree.body = tree.body[:cut]
    return compile(tree, filename, "exec")


class _Env(object):
    """Temporarily replace os.environ with a fake worker .env."""

    def __init__(self, **values):
        self.values = values
        self.saved = None

    def __enter__(self):
        self.saved = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(("MQTT_", "REDIS_", "STATION_", "DEVICE_", "RTSP_", "RTMP_", "STREAM_", "MODE", "HOST", "LOG_DIR")):
                del os.environ[k]
        os.environ.update(self.values)
        return self

    def __exit__(self, *exc):
        os.environ.clear()
        os.environ.update(self.saved)
        return False


def _run_module(name, env, record):
    """Execute the worker's module body (minus its loop) with stubs; return its namespace."""
    path = os.path.join(HERE, name)
    with open(path, encoding="utf-8") as f:
        code = _body_before_main_loop(f.read(), path)
    stubs = _stub_modules(record)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    ns = {"__name__": "selftest_" + name.replace(".py", ""), "__file__": path}
    # the workers print their own boot lines ("[Redis] Connected", "[MQTT] TLS enabled");
    # keep them out of the node's journal unless the import fails, then show them as context
    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        with _Env(**env):
            exec(code, ns)
    except Exception as e:  # noqa: BLE001 - re-raised with the worker's own output attached
        sys.stdout = real_stdout
        tail = " | ".join(captured.getvalue().strip().splitlines()[-3:])
        raise RuntimeError("%s: %s%s" % (type(e).__name__, e, (" [worker output: %s]" % tail) if tail else "")) from None
    finally:
        sys.stdout = real_stdout
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return ns


# ----------------------------------------------------------------------------- checks
def check_identity(fail):
    for f in WORKERS + IDENTITY:
        if not os.path.exists(os.path.join(HERE, f)):
            fail("identity", "%s missing from the bundle" % f)
    try:
        v = open(os.path.join(HERE, "WORKERS_VERSION")).read().strip()
        if not v.isdigit():
            fail("identity", "WORKERS_VERSION is not an integer: %r" % v)
    except OSError as e:
        fail("identity", "WORKERS_VERSION unreadable: %s" % e)
    try:
        if not open(os.path.join(HERE, "BUILD")).read().strip():
            fail("identity", "BUILD is empty")
    except OSError as e:
        fail("identity", "BUILD unreadable: %s" % e)


def check_compile(fail, scratch):
    for f in WORKERS:
        if f.endswith(".py"):
            try:
                py_compile.compile(os.path.join(HERE, f), cfile=os.path.join(scratch, f + "c"), doraise=True)
            except py_compile.PyCompileError as e:
                fail("compile", "%s: %s" % (f, str(e).strip().splitlines()[-1]))


def check_bash(fail, notes):
    bash = shutil.which("bash")
    if not bash:
        notes.append("bash absent here - stream.sh syntax not checked (the node has bash)")
        return
    r = subprocess.run([bash, "-n", "stream.sh"], cwd=HERE, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        fail("bash -n", "stream.sh: %s" % (r.stderr.strip() or "rc=%d" % r.returncode))


def _fake_env(tmp, secure):
    env = {
        "DEVICE_ID": "PAT-SELFTEST00", "STATION_ID": "selftest", "STATION_NAME": "selftest",
        "MQTT_HOST": "127.0.0.1", "MQTT_PORT": "1883", "REDIS_HOST": "127.0.0.1",
        "LOG_DIR": os.path.join(tmp, "logs"), "HOST": "127.0.0.1", "MODE": "RADAR",
        "RTSP_URL": "rtsp://127.0.0.1:554/selftest", "RTMP_URL": "rtmp://127.0.0.1/CCTVApp/STREAM-SELFTEST",
    }
    if secure:
        for k, name in (("MQTT_CERT", "c.crt"), ("MQTT_PRIVATE_KEY", "c.key"), ("MQTT_CA", "ca.crt")):
            p = os.path.join(tmp, name)
            with open(p, "w") as f:
                f.write("selftest placeholder\n")
            env[k] = p
        env["MQTT_USERNAME"] = "selftest"
        env["MQTT_PASSWORD"] = "selftest"
    return env


def check_mqtt_worker(fail, name, tmp):
    for secure in (True, False):
        label = "%s[%s]" % (name, "tls+creds" if secure else "plain")
        record = []
        try:
            ns = _run_module(name, _fake_env(tmp, secure), record)
        except Exception as e:  # noqa: BLE001 - any exception here is the finding
            fail(label, "%s: %s" % (type(e).__name__, e))
            continue
        mq = [r for r in record if r.kind == "mqtt.Client"]
        rd = [r for r in record if r.kind == "redis.Redis"]
        mb = [r for r in record if r.kind == "modbus"]
        if len(mq) != 1:
            fail(label, "expected one mqtt.Client, got %d" % len(mq))
            continue
        c = mq[0]
        if not c.called("will_set"):
            fail(label, "last-will not set")
        if not c.called("connect"):
            fail(label, "mqtt connect() never called")
        elif c.called("connect")[0][1][:2] != ("127.0.0.1", 1883):
            fail(label, "mqtt connect() with %r, expected (MQTT_HOST, MQTT_PORT)" % (c.called("connect")[0][1],))
        if not c.called("loop_start"):
            fail(label, "mqtt loop_start() never called")
        if secure and not c.called("tls_set"):
            fail(label, "certs present but tls_set() not called")
        if secure and not c.called("username_pw_set"):
            fail(label, "MQTT_USERNAME present but username_pw_set() not called")
        if not secure and (c.called("tls_set") or c.called("username_pw_set")):
            fail(label, "no certs/credentials but TLS or credentials were set")
        if not rd or not rd[0].called("ping"):
            fail(label, "redis client not created / not pinged")
        if not mb:
            fail(label, "modbus client not created")
        if "mqtt_client" not in ns or "file_service" not in ns:
            fail(label, "expected module globals missing (mqtt_client, file_service)")


def check_stream_supervisor(fail, tmp):
    name = "stream_supervisor.py"
    record = []
    try:
        ns = _run_module(name, _fake_env(tmp, True), record)
    except Exception as e:  # noqa: BLE001
        fail(name, "%s: %s" % (type(e).__name__, e))
        return
    mq = [r for r in record if r.kind == "mqtt.Client"]
    if len(mq) != 1 or not mq[0].called("connect") or not mq[0].called("will_set"):
        fail(name, "MQTT status client not wired (Client/will_set/connect)")
    if "build_ffmpeg" not in ns:
        fail(name, "build_ffmpeg() missing")
        return
    ns["AUDIO"] = "aac"
    cmd = ns["build_ffmpeg"]()
    if "-c:a" not in cmd or "aac" not in cmd:
        fail(name, "AUDIO=aac did not produce an audio encoder: %s" % " ".join(cmd))
    ns["AUDIO"] = "none"
    cmd = ns["build_ffmpeg"]()
    if "-an" not in cmd or "-c:a" in cmd:
        fail(name, "AUDIO=none did not produce -an: %s" % " ".join(cmd))
    ns["ENC"] = "copy"
    cmd = ns["build_ffmpeg"]()
    if cmd[cmd.index("-c:v") + 1] != "copy":
        fail(name, "ENC=copy ignored")
    if "-progress" not in cmd or "pipe:1" not in cmd:
        fail(name, "ffmpeg -progress pipe:1 missing (the stall watchdog needs it)")
    if ns.get("STREAM_ID") != "STREAM-SELFTEST":
        fail(name, "STREAM_ID not derived from RTMP_URL: %r" % ns.get("STREAM_ID"))


def check_real_deps(fail, notes):
    probe = "import importlib,sys\nmissing=[m for m in %r if not __import__('importlib').util.find_spec(m.split('.')[0])]\nprint(','.join(missing))" % (REAL_DEPS,)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    missing = [m for m in r.stdout.strip().split(",") if m]
    if r.returncode != 0:
        missing = list(REAL_DEPS)
    if missing:
        msg = "real dependencies not importable with %s: %s" % (sys.executable, ", ".join(missing))
        if os.environ.get("SELFTEST_REQUIRE_DEPS") == "1":
            fail("deps", msg)
        else:
            notes.append(msg + " (informational; SELFTEST_REQUIRE_DEPS=1 makes this a failure)")


# ----------------------------------------------------------------------------- main
def main():
    failures = []
    notes = []
    checks = []

    def fail(check, why):
        failures.append("%s: %s" % (check, why))

    def run(label, fn, *a):
        before = len(failures)
        fn(fail, *a)
        checks.append(label)
        print("%s %s" % ("FAIL" if len(failures) > before else "ok  ", label), flush=True)

    scratch = tempfile.mkdtemp(prefix="workers-selftest-")
    try:
        run("bundle identity", check_identity)
        run("py_compile", check_compile, scratch)
        run("bash -n stream.sh", check_bash, notes)
        run("radar.py wired import", lambda f, t: check_mqtt_worker(f, "radar.py", t), scratch)
        run("dropler.py wired import", lambda f, t: check_mqtt_worker(f, "dropler.py", t), scratch)
        run("stream_supervisor.py wired import + build_ffmpeg", check_stream_supervisor, scratch)
        run("real dependencies", check_real_deps, notes)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    for n in notes:
        print("note %s" % n)
    for f in failures:
        print("FAIL %s" % f)
    if failures:
        print("SELFTEST FAIL (%d failure%s in %d checks)" % (len(failures), "" if len(failures) == 1 else "s", len(checks)))
        return 1
    print("SELFTEST PASS (%d checks, python %s)" % (len(checks), sys.version.split()[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
