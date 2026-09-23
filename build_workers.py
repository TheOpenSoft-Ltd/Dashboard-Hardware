#!/usr/bin/env python3
"""build_workers.py - build (and sign) the worker OTA bundle the fleet pulls from dist/.

    python build_workers.py --check
        stage the bundle, run ota/selftest.py with this python, prove the archive is
        byte-deterministic, run tests/test_build_workers.py. Writes NOTHING under dist/.
        This is what CI runs on every PR.

    HEALER_RELEASE_KEY=<ed25519 pem> python build_workers.py --sign [--percent P] [--canary ID,ID,...] [--allow-dirty]
        the release step, on the laptop that holds the key. Writes
            dist/workers.tar.gz          the bundle
            dist/workers.tar.gz.sig      detached raw ed25519 signature over the .tar.gz
            dist/workers.version         the integer version (fetched first, cheap compare)
            dist/workers.rollout.json    {"version", "build", "canary", "percent"}  (decision 3)
        and refuses a dirty worktree, an unbumped version, or a non-ed25519 key.

Sibling of Smart-Healer/build.py. The bundle is:
    radar.py dropler.py stream.sh stream_supervisor.py   from src/pat_smart/modules/workers/, unchanged
    selftest.py                                          from ota/selftest.py
    WORKERS_VERSION                                      from ota/WORKERS_VERSION (the single source)
    BUILD                                                "<version>+g<commit>[-dirty]" stamped here
    REQUIRES_ENV                                         from ota/REQUIRES_ENV when present (optional)
The tar is deterministic (sorted members, fixed owner/mode/mtime, gzip mtime 0), so a
--check build on CI and the --sign build on the laptop of the same commit are identical
bytes, and the node's sha256 provenance means the same thing everywhere.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKERS_SRC = os.path.join(HERE, "src", "pat_smart", "modules", "workers")
OTA = os.path.join(HERE, "ota")
DIST = os.path.join(HERE, "dist")
WORKER_FILES = ("radar.py", "dropler.py", "stream.sh", "stream_supervisor.py")
EXECUTABLE = ("stream.sh", "selftest.py")
BUNDLE = "workers.tar.gz"
FIXED_MTIME = 1_600_000_000  # 2020-09-13: constant so the archive never depends on the clock


def read_version():
    with open(os.path.join(OTA, "WORKERS_VERSION"), encoding="utf-8") as f:
        v = f.read().strip()
    if not v.isdigit() or int(v) < 1:
        raise SystemExit("ota/WORKERS_VERSION must be a positive integer, got %r" % v)
    return int(v)


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 - no git means an unstamped build, reported as such
        return ""


def git_identity():
    """(short commit or 'unknown', dirty flag) - mirrors Smart-Healer's buildinfo stamping."""
    commit = _git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(_git("status", "--porcelain", "--", "src/pat_smart/modules/workers", "ota", "build_workers.py"))
    return commit, dirty


def build_id(version, commit, dirty):
    return "%d+g%s%s" % (version, commit, "-dirty" if dirty else "")


def stage(dst, version, build):
    """Copy the bundle members into dst; return {name: sha256}."""
    os.makedirs(dst, exist_ok=True)
    for name in WORKER_FILES:
        shutil.copyfile(os.path.join(WORKERS_SRC, name), os.path.join(dst, name))
    shutil.copyfile(os.path.join(OTA, "selftest.py"), os.path.join(dst, "selftest.py"))
    with open(os.path.join(dst, "WORKERS_VERSION"), "w", encoding="utf-8", newline="\n") as f:
        f.write("%d\n" % version)
    with open(os.path.join(dst, "BUILD"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build + "\n")
    req = os.path.join(OTA, "REQUIRES_ENV")
    if os.path.exists(req):
        shutil.copyfile(req, os.path.join(dst, "REQUIRES_ENV"))
    return {n: sha256_file(os.path.join(dst, n)) for n in sorted(os.listdir(dst))}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_tar(staging):
    """Deterministic .tar.gz of every file in staging (flat, no directories); returns bytes."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(os.listdir(staging)):
            path = os.path.join(staging, name)
            if not os.path.isfile(path):
                continue
            info = tarfile.TarInfo(name)
            info.size = os.path.getsize(path)
            info.mtime = FIXED_MTIME
            info.mode = 0o755 if name in EXECUTABLE else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(path, "rb") as f:
                tar.addfile(info, f)
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def run_selftest(staging, python=None, require_deps=False):
    env = dict(os.environ)
    if require_deps:
        env["SELFTEST_REQUIRE_DEPS"] = "1"
    r = subprocess.run([python or sys.executable, os.path.join(staging, "selftest.py")],
                       cwd=staging, env=env, capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout + r.stderr).strip()


def sign_bytes(data, key_path):
    """Detached raw ed25519 signature (64 bytes) - the format healer-selfupdate.sh verifies."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    with open(key_path, "rb") as f:
        key = load_pem_private_key(f.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("release key at %s is not an ed25519 key" % key_path)
    sig = key.sign(data)
    key.public_key().verify(sig, data)  # raises InvalidSignature if anything is off
    return sig


def rollout_json(version, build, canary, percent):
    percent = max(0, min(100, int(percent)))
    return json.dumps({"version": version, "build": build, "canary": list(canary), "percent": percent},
                      indent=2, sort_keys=True) + "\n"


def run_unit_tests():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", os.path.join(HERE, "tests"),
                        "-p", "test_build_workers.py", "-v"], cwd=HERE, capture_output=True, text=True, timeout=600)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-4:])
    return r.returncode == 0, tail


# ----------------------------------------------------------------------------- commands
def cmd_check(args):
    version = read_version()
    commit, dirty = git_identity()
    build = build_id(version, commit, dirty)
    print("bundle v%d build %s" % (version, build))
    ok = True
    a = tempfile.mkdtemp(prefix="workers-a-")
    b = tempfile.mkdtemp(prefix="workers-b-")
    try:
        manifest = stage(a, version, build)
        for n, h in manifest.items():
            print("  %-22s %s" % (n, h[:16]))
        rc, out = run_selftest(a)
        print(out)
        print("SELFTEST: %s" % ("PASS" if rc == 0 else "FAIL rc=%d" % rc))
        ok &= rc == 0
        ta = make_tar(a)
        stage(b, version, build)
        tb = make_tar(b)
        det = hashlib.sha256(ta).hexdigest() == hashlib.sha256(tb).hexdigest()
        print("DETERMINISTIC: %s (%d bytes, sha256 %s)" % ("PASS" if det else "FAIL", len(ta), hashlib.sha256(ta).hexdigest()[:16]))
        ok &= det
        if not args.no_tests:
            tests_ok, tail = run_unit_tests()
            print(tail)
            print("TESTS: %s" % ("PASS" if tests_ok else "FAIL"))
            ok &= tests_ok
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)
    print("CHECK: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def cmd_sign(args):
    key_path = os.environ.get("HEALER_RELEASE_KEY", "")
    if not key_path or not os.path.exists(key_path):
        raise SystemExit("--sign needs HEALER_RELEASE_KEY=<path to the ed25519 private key> (decision 1: healer-release.key)")
    version = read_version()
    commit, dirty = git_identity()
    if dirty and not args.allow_dirty:
        raise SystemExit("worktree has uncommitted changes in the bundle inputs; commit them or pass --allow-dirty (the BUILD will say -dirty)")
    published = os.path.join(DIST, "workers.version")
    if os.path.exists(published):
        with open(published, encoding="utf-8") as f:
            cur = f.read().strip()
        if cur.isdigit() and int(cur) >= version:
            raise SystemExit("dist/workers.version is %s; bump ota/WORKERS_VERSION above it - nodes never downgrade and ignore an equal version" % cur)
    build = build_id(version, commit, dirty)
    staging = tempfile.mkdtemp(prefix="workers-sign-")
    try:
        stage(staging, version, build)
        rc, out = run_selftest(staging)
        if rc != 0:
            print(out)
            raise SystemExit("selftest failed with this python; not signing")
        data = make_tar(staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    sig = sign_bytes(data, key_path)
    os.makedirs(DIST, exist_ok=True)
    with open(os.path.join(DIST, BUNDLE), "wb") as f:
        f.write(data)
    with open(os.path.join(DIST, BUNDLE + ".sig"), "wb") as f:
        f.write(sig)
    with open(os.path.join(DIST, "workers.version"), "w", encoding="utf-8", newline="\n") as f:
        f.write("%d\n" % version)
    canary = [c.strip() for c in (args.canary or "").split(",") if c.strip()]
    with open(os.path.join(DIST, "workers.rollout.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(rollout_json(version, build, canary, args.percent))
    print("signed v%d build %s" % (version, build))
    print("  dist/%s      %d bytes sha256 %s" % (BUNDLE, len(data), hashlib.sha256(data).hexdigest()))
    print("  dist/%s.sig  %d bytes" % (BUNDLE, len(sig)))
    print("  dist/workers.rollout.json  canary=%s percent=%d" % (canary or "[]", max(0, min(100, args.percent))))
    print("commit dist/ in the same PR as the source change (plan §2)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd")
    chk = sub.add_parser("check", help="stage + selftest + determinism + unit tests; writes nothing under dist/")
    chk.add_argument("--no-tests", action="store_true", help="skip tests/test_build_workers.py (the tests call this themselves)")
    sg = sub.add_parser("sign", help="build and sign into dist/ (HEALER_RELEASE_KEY required)")
    sg.add_argument("--percent", type=int, default=0, help="rollout percent for dist/workers.rollout.json (default 0 = canary only)")
    sg.add_argument("--canary", default="", help="comma-separated DEVICE_IDs that apply the release regardless of percent")
    sg.add_argument("--allow-dirty", action="store_true")
    # accept the --check / --sign spelling from the plan as well
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [a.lstrip("-") if a in ("--check", "--sign") else a for a in argv]
    args = p.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "sign":
        return cmd_sign(args)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
