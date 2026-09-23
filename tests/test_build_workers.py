"""Behavioural tests for build_workers.py and the bundled ota/selftest.py.

Run:  python -m unittest tests.test_build_workers -v     (or via `python build_workers.py --check`)
stdlib unittest; the signing tests need `cryptography` and skip without it.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build_workers as bw  # noqa: E402

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
    HAVE_CRYPTO = True
except Exception:  # noqa: BLE001
    HAVE_CRYPTO = False

EXPECTED_MEMBERS = {"radar.py", "dropler.py", "stream.sh", "stream_supervisor.py", "selftest.py", "WORKERS_VERSION", "BUILD"}


class StageAndTar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t-bw-")
        self.version = bw.read_version()
        self.build = bw.build_id(self.version, "abc1234", False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stage_has_exactly_the_bundle_members(self):
        manifest = bw.stage(os.path.join(self.tmp, "s"), self.version, self.build)
        members = set(manifest) - {"REQUIRES_ENV"}
        self.assertEqual(members, EXPECTED_MEMBERS)
        with open(os.path.join(self.tmp, "s", "WORKERS_VERSION")) as f:
            self.assertEqual(f.read().strip(), str(self.version))
        with open(os.path.join(self.tmp, "s", "BUILD")) as f:
            self.assertEqual(f.read().strip(), self.build)

    def test_worker_files_are_byte_identical_to_the_source_tree(self):
        bw.stage(os.path.join(self.tmp, "s"), self.version, self.build)
        for n in bw.WORKER_FILES:
            self.assertEqual(bw.sha256_file(os.path.join(self.tmp, "s", n)),
                             bw.sha256_file(os.path.join(bw.WORKERS_SRC, n)), n)

    def test_build_id_format(self):
        self.assertEqual(bw.build_id(3, "14db61d", False), "3+g14db61d")
        self.assertEqual(bw.build_id(3, "14db61d", True), "3+g14db61d-dirty")

    def test_tar_is_deterministic_and_flat(self):
        a = os.path.join(self.tmp, "a")
        b = os.path.join(self.tmp, "b")
        bw.stage(a, self.version, self.build)
        bw.stage(b, self.version, self.build)
        ta, tb = bw.make_tar(a), bw.make_tar(b)
        self.assertEqual(hashlib.sha256(ta).hexdigest(), hashlib.sha256(tb).hexdigest())
        with tarfile.open(fileobj=io.BytesIO(ta), mode="r:gz") as tar:
            names = tar.getnames()
            self.assertEqual(set(names), EXPECTED_MEMBERS | ({"REQUIRES_ENV"} & set(names)))
            for m in tar.getmembers():
                self.assertNotIn("/", m.name)
                self.assertEqual(m.uid, 0)
                self.assertEqual(m.mtime, bw.FIXED_MTIME)
                self.assertEqual(m.mode, 0o755 if m.name in bw.EXECUTABLE else 0o644, m.name)

    def test_a_different_build_changes_the_archive(self):
        a = os.path.join(self.tmp, "a")
        b = os.path.join(self.tmp, "b")
        bw.stage(a, self.version, self.build)
        bw.stage(b, self.version, bw.build_id(self.version, "fffffff", True))
        self.assertNotEqual(bw.make_tar(a), bw.make_tar(b))


class Selftest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t-st-")
        self.staging = os.path.join(self.tmp, "s")
        bw.stage(self.staging, bw.read_version(), bw.build_id(bw.read_version(), "abc1234", False))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_passes_on_a_clean_bundle(self):
        rc, out = bw.run_selftest(self.staging)
        self.assertEqual(rc, 0, out)
        self.assertIn("SELFTEST PASS", out)
        for label in ("radar.py wired import", "dropler.py wired import", "stream_supervisor.py"):
            self.assertRegex(out, r"ok\s+" + label.split()[0], out)

    def test_fails_on_a_syntax_error(self):
        with open(os.path.join(self.staging, "radar.py"), "a") as f:
            f.write("\ndef broken(:\n")
        rc, out = bw.run_selftest(self.staging)
        self.assertNotEqual(rc, 0)
        self.assertIn("compile", out)

    def test_fails_when_the_tls_branch_is_removed(self):
        p = os.path.join(self.staging, "radar.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        guard = "if all(_mqtt_certs) and all(os.path.exists(p) for p in _mqtt_certs):"
        self.assertIn(guard, src)
        src = src.replace(guard, "if False:", 1)  # a regression that silently drops TLS must be caught
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        rc, out = bw.run_selftest(self.staging)
        self.assertNotEqual(rc, 0)
        self.assertIn("tls_set", out)

    def test_fails_on_bad_identity(self):
        with open(os.path.join(self.staging, "WORKERS_VERSION"), "w") as f:
            f.write("one\n")
        rc, out = bw.run_selftest(self.staging)
        self.assertNotEqual(rc, 0)
        self.assertIn("WORKERS_VERSION", out)

    def test_fails_when_a_member_is_missing(self):
        os.remove(os.path.join(self.staging, "stream.sh"))
        rc, out = bw.run_selftest(self.staging)
        self.assertNotEqual(rc, 0)
        self.assertIn("stream.sh missing", out)

    def test_require_deps_fails_where_the_real_libraries_are_absent(self):
        try:
            import paho.mqtt.client  # noqa: F401
            import pymodbus.client  # noqa: F401
            import redis  # noqa: F401
            import dotenv  # noqa: F401
        except Exception:  # noqa: BLE001 - laptop/CI without the worker deps: the strict mode must FAIL
            rc, out = bw.run_selftest(self.staging, require_deps=True)
            self.assertNotEqual(rc, 0)
            self.assertIn("real dependencies", out)
            return
        rc, out = bw.run_selftest(self.staging, require_deps=True)  # a node-like environment: must pass
        self.assertEqual(rc, 0, out)


class Rollout(unittest.TestCase):
    def test_rollout_carries_the_build_identity(self):
        d = json.loads(bw.rollout_json(2, "2+gabc1234", ["PAT-A", "PAT-B"], 10))
        self.assertEqual(d, {"version": 2, "build": "2+gabc1234", "canary": ["PAT-A", "PAT-B"], "percent": 10})

    def test_percent_is_clamped(self):
        self.assertEqual(json.loads(bw.rollout_json(1, "b", [], 250))["percent"], 100)
        self.assertEqual(json.loads(bw.rollout_json(1, "b", [], -5))["percent"], 0)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
class Signing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t-sig-")
        self.key_path = os.path.join(self.tmp, "k.pem")
        self.key = Ed25519PrivateKey.generate()
        with open(self.key_path, "wb") as f:
            f.write(self.key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detached_raw_ed25519_signature(self):
        data = b"bundle bytes"
        sig = bw.sign_bytes(data, self.key_path)
        self.assertEqual(len(sig), 64)
        self.key.public_key().verify(sig, data)
        from cryptography.exceptions import InvalidSignature
        with self.assertRaises(InvalidSignature):
            self.key.public_key().verify(sig, data + b"x")

    def test_non_ed25519_key_is_refused(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        rsa_path = os.path.join(self.tmp, "rsa.pem")
        with open(rsa_path, "wb") as f:
            f.write(rsa.generate_private_key(65537, 2048).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        with self.assertRaises(SystemExit):
            bw.sign_bytes(b"x", rsa_path)


class Commands(unittest.TestCase):
    def test_sign_refuses_without_a_key_and_writes_nothing(self):
        env = dict(os.environ)
        env.pop("HEALER_RELEASE_KEY", None)
        before = set(os.listdir(bw.DIST)) if os.path.isdir(bw.DIST) else set()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "build_workers.py"), "--sign"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("HEALER_RELEASE_KEY", r.stdout + r.stderr)
        after = set(os.listdir(bw.DIST)) if os.path.isdir(bw.DIST) else set()
        self.assertEqual(before, after)

    def test_check_without_tests_passes_and_writes_nothing(self):
        before = set(os.listdir(bw.DIST)) if os.path.isdir(bw.DIST) else set()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "build_workers.py"), "--check", "--no-tests"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DETERMINISTIC: PASS", r.stdout)
        self.assertIn("SELFTEST: PASS", r.stdout)
        after = set(os.listdir(bw.DIST)) if os.path.isdir(bw.DIST) else set()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
