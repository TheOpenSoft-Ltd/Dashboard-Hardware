"""Behavioural tests for ota/workers-selfupdate.sh (plan section 10, U1-U15 and the arming path).

The script runs against a FAKE node: a throwaway HOME with a workers dir, .env, the test's own
publisher key as healer-release.pub, a venv python wrapper (the interpreter the units use) whose
PYTHONPATH carries stub packages for the worker dependencies, and PATH shims for `systemctl` and
`sudo` that answer from / record into a state dir. Releases are served over file:// (curl handles
it, no network). Signatures are REAL ed25519 - the wrapper delegates everything to a real python,
so a forged bundle is refused by the real check, not by a stub that says yes.

On the laptop (OpenSSL 1.1.1, no -rawin) the script takes its python-cryptography fallback; on a
station (OpenSSL 3) it takes the openssl fast path. The suite is meant to run on both:
    python -m unittest tests.test_workers_selfupdate -v          (laptop / CI)
    scp the repo's ota/ + tests/ to a node and run the same          (station, real tools)
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build_workers as bw  # noqa: E402

SCRIPT = os.path.join(ROOT, "ota", "workers-selfupdate.sh")
ROLLBACK = os.path.join(ROOT, "ota", "workers-rollback.sh")

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import cryptography as _c
    CRYPTO_PATH = os.path.dirname(os.path.dirname(os.path.abspath(_c.__file__)))
    HAVE_CRYPTO = True
except Exception:  # noqa: BLE001
    HAVE_CRYPTO = False

BASH = shutil.which("bash")


def _crypto_needs_path():
    """True when `import cryptography` fails under a rewritten HOME (a user-site install)."""
    if not HAVE_CRYPTO:
        return False
    h = tempfile.mkdtemp(prefix="probe-home-")
    env = dict(os.environ, HOME=h)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "-c", "import cryptography"], capture_output=True, env=env)
    shutil.rmtree(h, ignore_errors=True)
    return r.returncode != 0


CRYPTO_NEEDS_PATH = _crypto_needs_path()
UNITS_DEFAULT = "pat-smart-radar active 0 loaded\npat-smart-stream active 0 loaded\n"


def posix(p):
    """bash-side spelling of a path (Git bash on Windows needs /c/...)."""
    if sys.platform == "win32" and shutil.which("cygpath"):
        return subprocess.run(["cygpath", "-u", p], capture_output=True, text=True).stdout.strip()
    return p


def write(path, content, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    if mode is not None:
        os.chmod(path, mode)


class Node(object):
    """A fake station: HOME + PATH shims + a shim state dir the tests read and write."""

    def __init__(self, tmp, pub_pem, device_id="PAT-T-001", installed_version=None, healer_pending=False,
                 units=UNITS_DEFAULT, no_crypto=False):
        self.home = os.path.join(tmp, "home")
        self.w = os.path.join(self.home, ".config", "pat-smart", "workers")
        self.state = os.path.join(self.home, ".local", "state", "pat-smart")
        self.shim = os.path.join(tmp, "shim")
        self.shim_state = os.path.join(tmp, "shimstate")
        os.makedirs(self.w)
        os.makedirs(self.state)
        os.makedirs(self.shim)
        os.makedirs(self.shim_state)
        write(os.path.join(self.home, ".config", "pat-smart", ".env"), "DEVICE_ID=%s\nMQTT_HOST=127.0.0.1\n" % device_id)
        for n in bw.WORKER_FILES:
            shutil.copyfile(os.path.join(bw.WORKERS_SRC, n), os.path.join(self.w, n))
        with open(os.path.join(self.w, "healer-release.pub"), "wb") as f:
            f.write(pub_pem)
        if installed_version is not None:
            write(os.path.join(self.w, "WORKERS_VERSION"), "%d\n" % installed_version)
            write(os.path.join(self.w, "BUILD"), "%d+gtest000\n" % installed_version)
        if healer_pending:
            write(os.path.join(self.state, "update.pending"), "")
        write(os.path.join(self.shim_state, "units"), units)
        # stub packages so the bundle's strict dependency probe passes with the "venv" python
        stubs = os.path.join(tmp, "stubdeps")
        for pkg in ("paho/__init__.py", "paho/mqtt/__init__.py", "paho/mqtt/client.py", "redis/__init__.py",
                    "pymodbus/__init__.py", "pymodbus/client/__init__.py", "dotenv/__init__.py"):
            write(os.path.join(stubs, *pkg.split("/")), "")
        # add cryptography's site dir ONLY when a rewritten HOME would hide it (user-site installs);
        # putting a system site-packages on PYTHONPATH shadows stdlib modules (a stale pathlib backport
        # in anaconda's site-packages broke `from collections import Sequence` here)
        pypath = os.pathsep.join([stubs] + ([CRYPTO_PATH] if CRYPTO_NEEDS_PATH else []))
        py = sys.executable.replace("\\", "/")
        probe = '' if not no_crypto else 'case "$*" in *"import cryptography"*) exit 1 ;; esac\n'
        wrapper = '#!/bin/bash\n%sexport PYTHONPATH="%s"\nexec "%s" "$@"\n' % (probe, pypath, py)
        venv_py = os.path.join(self.home, ".local", "share", "pipx", "venvs", "pat-smart", "bin", "python3")
        write(venv_py, wrapper, 0o755)
        write(os.path.join(self.shim, "python3"), wrapper, 0o755)
        write(os.path.join(self.shim, "systemctl"), r'''#!/bin/bash
# fake systemd: answers from $SHIM_STATE/units ("unit active|inactive nrestarts loaded")
S="$SHIM_STATE/units"
case "$1" in
  is-active) st="$(awk -v u="$2" '$1==u{print $2}' "$S")"; echo "${st:-inactive}"; [ "$st" = active ] ;;
  show)      case "$4" in
               NRestarts) awk -v u="$2" '$1==u{print $3}' "$S" ;;
               LoadState) awk -v u="$2" '$1==u{print $4}' "$S" ;;
             esac ;;
  *) exit 0 ;;
esac
''', 0o755)
        write(os.path.join(self.shim, "sudo"), r'''#!/bin/bash
# fake sudo: records every call; `systemctl restart U` fails when $SHIM_STATE/fail.U exists, else marks U active
echo "$*" >> "$SHIM_STATE/sudo.log"
[ "$1" = -n ] && shift
if [ "$1" = systemctl ] && [ "$2" = restart ]; then
  u="$3"; [ -f "$SHIM_STATE/fail.$u" ] && exit 1
  awk -v u="$u" '$1==u{$2="active"} {print}' "$SHIM_STATE/units" > "$SHIM_STATE/units.tmp" && mv "$SHIM_STATE/units.tmp" "$SHIM_STATE/units"
  exit 0
fi
exit 0
''', 0o755)

    # --- helpers the tests use -------------------------------------------------------
    def run(self, base, extra_env=None, args=(), script=SCRIPT):
        env = dict(os.environ)
        env.update({"HOME": posix(self.home), "WORKERS_RELEASE_BASE": base, "SHIM_STATE": posix(self.shim_state),
                    "PATH": posix(self.shim) + os.pathsep + env.get("PATH", ""), "WORKERS_PROVE_S": "0",
                    "WORKERS_PEND_MAX_S": "900"})
        env.pop("PYTHONPATH", None)
        env.pop("DRY_RUN", None)
        env.update(extra_env or {})
        return subprocess.run([BASH, posix(script), *args], capture_output=True, text=True, env=env, timeout=300)

    def events(self):
        p = os.path.join(self.state, "events.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def codes(self):
        return [e["e"] for e in self.events()]

    def sudo_calls(self):
        p = os.path.join(self.shim_state, "sudo.log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return f.read().splitlines()

    def running(self, name):
        with open(os.path.join(self.w, name), "rb") as f:
            return f.read()

    def good(self, name):
        p = os.path.join(self.w, ".good", name)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def set_units(self, text):
        write(os.path.join(self.shim_state, "units"), text)

    def pending(self):
        return os.path.exists(os.path.join(self.state, "workers.update.pending"))

    def age_pending(self, seconds):
        p = os.path.join(self.state, "workers.update.pending")
        t = os.path.getmtime(p) - seconds
        os.utime(p, (t, t))


def make_release(tmp, version, key, build=None, mutate=None, corrupt_sig=False, rollout=None, no_rollout=False,
                 requires_env=None, version_file=None):
    """A dist/ the fake node can pull over file://."""
    d = tempfile.mkdtemp(prefix="rel-", dir=tmp)
    build = build or "%d+gtest%03d" % (version, version)
    staging = os.path.join(d, "staging")
    bw.stage(staging, version, build)
    if requires_env:
        write(os.path.join(staging, "REQUIRES_ENV"), requires_env)
    if mutate:
        mutate(staging)
    data = bw.make_tar(staging)
    shutil.rmtree(staging)
    with open(os.path.join(d, "workers.tar.gz"), "wb") as f:
        f.write(data)
    sig = key.sign(data)
    if corrupt_sig:
        sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    with open(os.path.join(d, "workers.tar.gz.sig"), "wb") as f:
        f.write(sig)
    write(os.path.join(d, "workers.version"), "%d\n" % (version if version_file is None else version_file))
    if not no_rollout:
        r = {"version": version, "build": build, "canary": [], "percent": 100}
        r.update(rollout or {})
        write(os.path.join(d, "workers.rollout.json"), json.dumps(r) + "\n")
    return pathlib.Path(d).as_uri()


def change_radar(staging):
    with open(os.path.join(staging, "radar.py"), "a", encoding="utf-8") as f:
        f.write("\n# release change: a comment the running set does not have\n")


def change_stream(staging):
    with open(os.path.join(staging, "stream_supervisor.py"), "a", encoding="utf-8") as f:
        f.write("\n# release change in the stream supervisor\n")


def break_radar(staging):
    with open(os.path.join(staging, "radar.py"), "a", encoding="utf-8") as f:
        f.write("\ndef broken(:\n")


@unittest.skipUnless(BASH and HAVE_CRYPTO, "needs bash and cryptography")
class WorkersSelfupdate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = Ed25519PrivateKey.generate()
        cls.pub = cls.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t-wsu-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def node(self, **kw):
        return Node(self.tmp, self.pub, **kw)

    def armed_node(self):
        """A node after the arming release: running set == v1, provenance recorded, .good == v1."""
        n = self.node()
        r = n.run(make_release(self.tmp, 1, self.key))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("workers.update.ok", n.codes())
        self.assertEqual(n.good("WORKERS_VERSION"), b"1\n")
        with open(os.path.join(n.shim_state, "sudo.log"), "w"):
            pass
        return n

    # --- arming ----------------------------------------------------------------------
    def test_A_arming_release_installs_identity_only_and_promotes_at_once(self):
        n = self.node()
        r = n.run(make_release(self.tmp, 1, self.key))
        self.assertEqual(r.returncode, 0, r.stderr)
        ok = [e for e in n.events() if e["e"] == "workers.update.ok"]
        self.assertEqual(len(ok), 1, n.events())
        self.assertEqual(ok[0]["d"]["changed"], [])
        self.assertTrue(ok[0]["d"]["promoted"])
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")
        self.assertEqual(n.good("WORKERS_VERSION"), b"1\n")
        self.assertEqual(n.good("radar.py"), n.running("radar.py"))
        self.assertEqual(n.sudo_calls(), [], "an identity-only release must restart nothing")
        self.assertFalse(n.pending())
        self.assertTrue(os.path.exists(os.path.join(n.state, "workers.installed.sha256")))

    def test_A2_second_run_after_arming_is_silent(self):
        n = self.armed_node()
        before = len(n.events())
        r = n.run(make_release(self.tmp, 1, self.key))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(n.events()), before, "same version -> no event noise")
        self.assertEqual(r.stderr.strip(), "")

    # --- U1 real change ---------------------------------------------------------------
    def test_U1_real_change_installs_restarts_only_changed_unit_and_marks_unproven(self):
        n = self.armed_node()
        r = n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertEqual(r.returncode, 0, r.stderr)
        ok = [e for e in n.events() if e["e"] == "workers.update.ok" and e["d"]["to"] == 2]
        self.assertEqual(len(ok), 1, n.events())
        self.assertEqual(ok[0]["d"]["changed"], ["radar.py"])
        self.assertEqual(ok[0]["d"]["restarted"], {"pat-smart-radar": 0})
        self.assertFalse(ok[0]["d"]["promoted"])
        self.assertEqual(n.sudo_calls(), ["-n systemctl restart pat-smart-radar"])
        self.assertIn(b"release change", n.running("radar.py"))
        self.assertEqual(n.running("WORKERS_VERSION"), b"2\n")
        self.assertTrue(n.pending())
        self.assertEqual(n.good("WORKERS_VERSION"), b"1\n", "not promoted on the same run")

    def test_U1b_proven_set_is_promoted_on_the_next_run(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        n.age_pending(600)
        r = n.run(make_release(self.tmp, 2, self.key, mutate=change_radar), extra_env={"WORKERS_PROVE_S": "180"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(n.pending())
        self.assertIn("workers.update.promote", n.codes())
        self.assertEqual(n.good("WORKERS_VERSION"), b"2\n")
        self.assertEqual(n.good("radar.py"), n.running("radar.py"))

    def test_U1c_still_proving_does_not_fetch_or_promote(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        before = len(n.events())
        r = n.run(make_release(self.tmp, 3, self.key, mutate=change_stream), extra_env={"WORKERS_PROVE_S": "3600"})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(n.pending())
        self.assertEqual(len(n.events()), before)
        self.assertEqual(n.running("WORKERS_VERSION"), b"2\n", "no new install while proving")

    def test_U1d_two_files_one_unit_restart_each_unit_once(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=lambda s: (change_radar(s), change_stream(s))))
        self.assertEqual(sorted(n.sudo_calls()), ["-n systemctl restart pat-smart-radar", "-n systemctl restart pat-smart-stream"])

    def test_U1e_unit_absent_on_this_node_is_skipped_not_failed(self):
        n = self.armed_node()

        def change_dropler(s):
            with open(os.path.join(s, "dropler.py"), "a") as f:
                f.write("\n# dropler change\n")
        n.run(make_release(self.tmp, 2, self.key, mutate=change_dropler))
        ok = [e for e in n.events() if e["e"] == "workers.update.ok" and e["d"]["to"] == 2]
        self.assertEqual(len(ok), 1, n.events())
        self.assertEqual(ok[0]["d"]["restarted"], {"pat-smart-dropler": "absent"})
        self.assertEqual(n.sudo_calls(), [])
        self.assertIn(b"dropler change", n.running("dropler.py"))

    # --- U2/U3 signature ---------------------------------------------------------------
    def test_U2_bad_signature_is_rejected_and_nothing_moves(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, corrupt_sig=True))
        rej = [e for e in n.events() if e["e"] == "workers.update.reject"]
        self.assertEqual([e["d"]["why"] for e in rej], ["bad-signature"])
        self.assertNotIn(b"release change", n.running("radar.py"))
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")
        self.assertEqual(n.sudo_calls(), [])

    def test_U3_wrong_signer_is_rejected(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, Ed25519PrivateKey.generate(), mutate=change_radar))
        self.assertIn("bad-signature", [e["d"].get("why") for e in n.events()])
        self.assertNotIn(b"release change", n.running("radar.py"))

    def test_U3b_no_verifier_is_a_distinct_reason(self):
        # openssl without -rawin AND no cryptography anywhere -> the node cannot check, says so, installs nothing
        if subprocess.run(["openssl", "pkeyutl", "-help"], capture_output=True, text=True).stdout.find("-rawin") >= 0 or \
           "-rawin" in subprocess.run(["openssl", "pkeyutl", "-help"], capture_output=True, text=True).stderr:
            self.skipTest("this openssl verifies ed25519 itself; the no-verifier path needs an old openssl")
        n = self.node(installed_version=1, no_crypto=True)
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertIn("no-verifier", [e["d"].get("why") for e in n.events()])
        self.assertNotIn(b"release change", n.running("radar.py"))

    # --- U4/U5 versions ------------------------------------------------------------------
    def test_U4_same_version_is_silent(self):
        n = self.node(installed_version=2)
        r = n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertEqual(n.events(), [])
        self.assertEqual(r.stderr.strip(), "")
        self.assertNotIn(b"release change", n.running("radar.py"))

    def test_U5_older_release_never_downgrades(self):
        n = self.node(installed_version=5)
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertEqual(n.events(), [])
        self.assertEqual(n.running("WORKERS_VERSION"), b"5\n")

    # --- U6..U8 rollout gate --------------------------------------------------------------
    def test_U6_not_in_rollout_waits_silently(self):
        n = self.armed_node()
        before = len(n.events())
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, rollout={"percent": 0}))
        self.assertEqual(len(n.events()), before)
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")

    def test_U6b_canary_applies_regardless_of_percent(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, rollout={"percent": 0, "canary": ["PAT-T-001"]}))
        self.assertEqual(n.running("WORKERS_VERSION"), b"2\n")

    def test_U6c_percent_bucket_is_stable_per_device(self):
        # sha1("PAT-T-001")[:8] mod 100 decides; whichever side it lands, 0 waits and 100 goes
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, rollout={"percent": 100}))
        self.assertEqual(n.running("WORKERS_VERSION"), b"2\n")

    def test_U7_rollout_build_mismatch_is_rejected(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, rollout={"build": "2+gsomethingelse"}))
        self.assertIn("build-mismatch", [e["d"].get("why") for e in n.events()])
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")

    def test_U8_rollout_missing_fails_closed(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, no_rollout=True))
        self.assertIn("rollout-missing", [e["d"].get("why") for e in n.events()])
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")

    def test_U8b_stale_cdn_version_vs_rollout_fails_closed(self):
        n = self.armed_node()
        # workers.version already says 3 while rollout.json still describes 2 (mid-push): refuse, retry next tick
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, version_file=3))
        self.assertIn("rollout-version-mismatch", [e["d"].get("why") for e in n.events()])
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")

    # --- U9/U10 bundle checks -----------------------------------------------------------------
    def test_U9_selftest_failure_is_rejected_before_install(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=break_radar))
        self.assertIn("selftest-failed", [e["d"].get("why") for e in n.events()])
        self.assertNotIn(b"broken(", n.running("radar.py"))
        self.assertEqual(n.sudo_calls(), [])

    def test_U10_required_env_key_missing_is_rejected(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, requires_env="MQTT_HOST\nVEGAMET_URL\n"))
        rej = [e for e in n.events() if e["e"] == "workers.update.reject"]
        self.assertEqual(rej[-1]["d"]["why"], "env-missing")
        self.assertEqual(rej[-1]["d"]["key"], "VEGAMET_URL")
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")

    def test_U10b_required_env_present_installs(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar, requires_env="MQTT_HOST\n"))
        self.assertEqual(n.running("WORKERS_VERSION"), b"2\n")

    # --- U12..U15 restart / prove / rollback ----------------------------------------------------
    def test_U12_restart_failure_restores_good_and_reports(self):
        n = self.armed_node()
        write(os.path.join(n.shim_state, "fail.pat-smart-radar"), "")
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        codes = n.codes()
        self.assertIn("workers.update.rollback", codes)
        rb = [e for e in n.events() if e["e"] == "workers.update.rollback"][-1]
        self.assertEqual(rb["d"]["why"], "restart-failed")
        self.assertEqual(rb["d"]["restart_rc"], {"pat-smart-radar": 1})
        self.assertEqual(n.running("radar.py"), n.good("radar.py"))
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")
        self.assertFalse(n.pending())

    def test_U13_crash_loop_rolls_back_immediately(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertTrue(n.pending())
        n.set_units("pat-smart-radar active 3 loaded\npat-smart-stream active 0 loaded\n")   # NRestarts jumped by 3
        with open(os.path.join(n.shim_state, "sudo.log"), "w"):
            pass
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar), extra_env={"WORKERS_PROVE_S": "3600"})
        rb = [e for e in n.events() if e["e"] == "workers.update.rollback"]
        self.assertEqual(len(rb), 1, n.events())
        self.assertEqual(rb[0]["d"]["why"], "crash-loop")
        self.assertEqual(rb[0]["d"]["restored"], 1)
        self.assertEqual(rb[0]["d"]["changed"], ["radar.py"])
        self.assertEqual(n.running("radar.py"), n.good("radar.py"))
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")
        self.assertEqual(n.sudo_calls(), ["-n systemctl restart pat-smart-radar"])
        self.assertFalse(n.pending())

    def test_U13b_one_extra_restart_is_not_healthy_but_not_a_crash(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        n.set_units("pat-smart-radar active 1 loaded\npat-smart-stream active 0 loaded\n")
        n.age_pending(600)
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar), extra_env={"WORKERS_PROVE_S": "180"})
        self.assertTrue(n.pending(), "not healthy -> keeps proving")
        self.assertNotIn("workers.update.promote", n.codes())
        self.assertNotIn("workers.update.rollback", n.codes())

    def test_U14_never_healthy_by_deadline_rolls_back(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        n.set_units("pat-smart-radar inactive 0 loaded\npat-smart-stream active 0 loaded\n")   # unit died, no restarts
        n.age_pending(1000)
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar), extra_env={"WORKERS_PROVE_S": "180", "WORKERS_PEND_MAX_S": "900"})
        rb = [e for e in n.events() if e["e"] == "workers.update.rollback"]
        self.assertEqual(len(rb), 1, n.events())
        self.assertEqual(rb[0]["d"]["why"], "no-healthy-tick")
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")

    def test_U15_rollback_impossible_without_good_is_said_once(self):
        # a node that never armed: v2 installs directly, then never proves; there is no .good to fall back to
        n = self.node(installed_version=1)
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertTrue(n.pending())
        n.set_units("pat-smart-radar inactive 0 loaded\npat-smart-stream active 0 loaded\n")
        n.age_pending(1000)
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar), extra_env={"WORKERS_PROVE_S": "180"})
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar), extra_env={"WORKERS_PROVE_S": "180"})
        self.assertEqual(n.codes().count("workers.update.rollback-impossible"), 1)
        self.assertIn(b"release change", n.running("radar.py"), "left running: nothing better exists")

    # --- U16 foreign, U17 defer, U18 dry run, U19 manual rollback -----------------------------------
    def test_U16_hand_edited_running_set_is_reported_once_and_never_promoted(self):
        n = self.armed_node()
        with open(os.path.join(n.w, "radar.py"), "a") as f:
            f.write("\n# site hand-fix\n")
        n.run(make_release(self.tmp, 1, self.key))
        n.run(make_release(self.tmp, 1, self.key))
        self.assertEqual(n.codes().count("workers.update.foreign"), 1)
        self.assertNotIn(b"site hand-fix", n.good("radar.py"))

    def test_U17_defers_while_the_healer_update_is_proving(self):
        n = self.armed_node()
        write(os.path.join(n.state, "update.pending"), "")
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertIn("workers.update.deferred", n.codes())
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")

    def test_U18_dry_run_changes_nothing_and_says_so(self):
        n = self.armed_node()
        before = len(n.events())
        r = n.run(make_release(self.tmp, 2, self.key, mutate=change_radar), extra_env={"DRY_RUN": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY_RUN", r.stderr)
        self.assertIn("radar.py", r.stderr)
        self.assertEqual(len(n.events()), before)
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")
        self.assertEqual(n.sudo_calls(), [])
        self.assertFalse(n.pending())

    def test_U19_manual_rollback_restores_good_and_restarts_changed_units(self):
        n = self.armed_node()
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        with open(os.path.join(n.shim_state, "sudo.log"), "w"):
            pass
        r = n.run(make_release(self.tmp, 2, self.key), script=ROLLBACK, args=("field",))
        self.assertEqual(r.returncode, 0, r.stderr)
        rb = [e for e in n.events() if e["e"] == "workers.rollback.manual"]
        self.assertEqual(len(rb), 1, n.events())
        self.assertEqual(rb[0]["d"]["why"], "manual-field")
        self.assertEqual(rb[0]["d"]["changed"], ["radar.py"])
        self.assertEqual(n.running("radar.py"), n.good("radar.py"))
        self.assertEqual(n.running("WORKERS_VERSION"), b"1\n")
        self.assertEqual(n.sudo_calls(), ["-n systemctl restart pat-smart-radar"])
        self.assertFalse(n.pending())

    def test_U19b_manual_rollback_without_good_refuses(self):
        n = self.node(installed_version=1)
        r = n.run(make_release(self.tmp, 1, self.key), script=ROLLBACK)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("workers.update.rollback-impossible", n.codes())

    def test_U20_no_release_published_is_silent(self):
        n = self.armed_node()
        before = len(n.events())
        r = n.run(pathlib.Path(os.path.join(self.tmp, "nothing-here")).as_uri())
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(n.events()), before)
        self.assertEqual(r.stderr.strip(), "")

    def test_U21_missing_pubkey_refuses_everything(self):
        n = self.node(installed_version=1)
        os.remove(os.path.join(n.w, "healer-release.pub"))
        n.run(make_release(self.tmp, 2, self.key, mutate=change_radar))
        self.assertEqual([e["d"].get("why") for e in n.events()], ["no-pubkey"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
