#!/bin/bash
# workers-selfupdate.sh - pull-update for the station WORKERS (radar.py, dropler.py, stream.sh,
# stream_supervisor.py). Sibling of Smart-Healer's healer-selfupdate.sh, which runs this at the
# end of every pat-healer-update timer tick (plan decision 2: one file copy, no new unit, no root).
#
# OUTBOUND HTTPS ONLY. Verifies the bundle's ed25519 signature against the SAME baked-in publisher
# key as the healer (decision 1), runs the bundle's own selftest with the interpreter the units use,
# installs atomically, restarts ONLY the units whose files changed (one sudo call each, the
# per-unit NOPASSWD lines), and on later runs proves -> promotes -> or rolls back. ANY failure
# leaves the running set exactly as it was. Every outcome is an event (codes workers.update.*,
# documented in Smart-Healer's events_schema.py).
#
# Layout (plan section 3a)
#   $W/radar.py dropler.py stream.sh stream_supervisor.py   the RUNNING set (units exec these)
#   $W/WORKERS_VERSION  $W/BUILD  $W/selftest.py            identity of the running set + its selftest
#   $W/.good/                                               fallback: the last set that BOTH came from git AND proved itself here
#   $STATE/workers.installed.sha256                          provenance: identity of the set this updater last installed
#   $STATE/workers.update.pending                            "unproven" marker, created at install
#   $STATE/workers.update.units                              "unit was_active nrestarts" recorded at install, for the health check
#
# Prove / promote / rollback (plan 3c). Until healer v536 clears the marker itself, this script
# judges health on its next run from what admin can read without sudo: every unit that was active
# before the install is active now, and its NRestarts (systemd's AUTOMATIC restart counter) has not
# moved. NRestarts +3 or more = crash loop -> roll back at once. Healthy for WORKERS_PROVE_S -> the
# marker goes and the set is promoted to .good. Not healthy by WORKERS_PEND_MAX_S -> roll back.
# Roll-forward is the only way past a bad release: nodes never downgrade (RV <= LV exits).
#
# Env knobs   WORKERS_RELEASE_BASE (tests point it at file://), WORKERS_PROVE_S (180),
#             WORKERS_PEND_MAX_S (900), DRY_RUN=1 (read-only: says what it would do, changes nothing)
# Usage       workers-selfupdate.sh              one updater run
#             workers-selfupdate.sh --rollback   manual rollback to .good (workers-rollback.sh wraps this)
set -u
BASE="${WORKERS_RELEASE_BASE:-https://raw.githubusercontent.com/TheOpenSoft-Ltd/Dashboard-Hardware/main/dist}"
W="$HOME/.config/pat-smart/workers"
PUB="$W/healer-release.pub"
STATE="$HOME/.local/state/pat-smart"
ENVF="$HOME/.config/pat-smart/.env"
GOOD="$W/.good"
PROV="$STATE/workers.installed.sha256"
PEND="$STATE/workers.update.pending"
UNITS_REC="$STATE/workers.update.units"
FILES="radar.py dropler.py stream.sh stream_supervisor.py"
IDENT="WORKERS_VERSION BUILD"
PROVE_S="${WORKERS_PROVE_S:-180}"
PEND_MAX="${WORKERS_PEND_MAX_S:-900}"
DRY="${DRY_RUN:-0}"

# the interpreter the units use (pipx venv on the RPi5 stations); the selftest must run with it
PY="$HOME/.local/share/pipx/venvs/pat-smart/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3 || echo /usr/bin/python3)"
SYSPY="$(command -v python3 || echo /usr/bin/python3)"      # carries python3-cryptography on the RPi5 image

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
NID="$(grep -m1 '^DEVICE_ID' "$ENVF" 2>/dev/null | cut -d= -f2)"
[ -n "$NID" ] || NID="$(hostname)"
ev(){ local d="${2:-}"; [ -n "$d" ] || d='{}'   # NOT "${2:-{}}": bash closes that at the first '}' and appends a stray brace
      printf '%s [workers-update] %s %s\n' "$(date +%FT%T%z)" "$1" "$d" >&2
      { mkdir -p "$STATE" && printf '{"t":%s,"n":"%s","e":"%s","d":%s}\n' \
        "$(date +%s)" "$NID" "$1" "$d" >> "$STATE/events.jsonl"; } 2>/dev/null || true; }
say(){ printf '%s [workers-update] %s\n' "$(date +%FT%T%z)" "$*" >&2; }
sha(){ sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
num(){ [ -f "$1" ] && tr -dc '0-9' < "$1"; }                  # digits of a small file, empty when absent
# identity of a SET: sha256 over the per-file sha256 lines of the four workers + the two identity files
setsha(){ local d="$1" f; for f in $FILES $IDENT; do printf '%s  %s\n' "$(sha "$d/$f")" "$f"; done | sha256sum | cut -d' ' -f1; }
unit_of(){ case "$1" in radar.py) echo pat-smart-radar;; dropler.py) echo pat-smart-dropler;; stream.sh|stream_supervisor.py) echo pat-smart-stream;; esac; }
units_of(){ local f; for f in "$@"; do unit_of "$f"; done | awk '!s[$0]++'; }
mode_of(){ case "$1" in *.sh|selftest.py) echo 0755;; *) echo 0644;; esac; }
jlist(){ local out="" x; for x in "$@"; do out="$out${out:+,}\"$x\""; done; printf '[%s]' "$out"; }
active(){ systemctl is-active "$1" 2>/dev/null; }
loaded(){ [ "$(systemctl show "$1" -p LoadState --value 2>/dev/null)" = loaded ]; }
nrestarts(){ local n; n="$(systemctl show "$1" -p NRestarts --value 2>/dev/null | tr -dc '0-9')"; echo "${n:-0}"; }
install_one(){ install -m "$(mode_of "$(basename "$2")")" "$1" "$2.new" && mv -f "$2.new" "$2"; }

# ed25519 verify: 0 good, 1 BAD signature, 2 no verifier on this node (fail closed).
# openssl first (fast path, OpenSSL 3 knows -rawin); python-cryptography is the fallback and the
# authority where openssl is too old - try the system python (has the dist-package) then the venv.
verify_sig(){
  if openssl pkeyutl -verify -pubin -inkey "$PUB" -rawin -in "$1" -sigfile "$2" >/dev/null 2>&1; then return 0; fi
  local p
  for p in "$SYSPY" "$PY"; do
    if "$p" -c 'import cryptography' >/dev/null 2>&1; then
      "$p" - "$PUB" "$1" "$2" <<'PYV' >/dev/null 2>&1
import sys
from cryptography.hazmat.primitives.serialization import load_pem_public_key
load_pem_public_key(open(sys.argv[1], 'rb').read()).verify(
    open(sys.argv[3], 'rb').read(), open(sys.argv[2], 'rb').read())
PYV
      return $?
    fi
  done
  return 2
}

# restart the given units, ONE sudo call each, only those that exist and are running; RC_JSON gets
# the per-unit result. A unit that is loaded but stopped is left stopped (the new file takes effect
# whenever it next starts); a unit that does not exist on this node (dropler on a RADAR station) is skipped.
RC_JSON="{}"
restart_units(){
  local u rc any=0 out=""
  for u in "$@"; do
    if ! loaded "$u"; then rc='"absent"'
    elif [ "$(active "$u")" != active ]; then rc='"skipped-inactive"'
    elif [ "$DRY" = 1 ]; then rc=0
    else sudo -n systemctl restart "$u" >/dev/null 2>&1; rc=$?; [ "$rc" -eq 0 ] || any=1; fi
    out="$out${out:+,}\"$u\":$rc"
  done
  RC_JSON="{$out}"; return $any
}

# copy the whole set from $1 to $2 (dir), atomically per file; returns nonzero on the first failure
copy_set(){
  local f
  mkdir -p "$2" || return 1
  for f in $FILES $IDENT selftest.py; do
    [ -f "$1/$f" ] || continue
    install_one "$1/$f" "$2/$f" || return 1
  done
}

# roll the running set back to .good: only the files that differ, restart only their units.
# $1 = why, $2 = extra json fields (no braces). Emits workers.rollback.manual when why starts with "manual".
restore_good(){
  local why="$1" extra="${2:-}" f changed="" code="workers.update.rollback"
  case "$why" in manual*) code="workers.rollback.manual";; esac
  if [ ! -f "$GOOD/WORKERS_VERSION" ]; then
    if [ ! -f "$STATE/workers.rollback-impossible.said" ]; then
      ev "workers.update.rollback-impossible" "{\"why\":\"$why\",\"note\":\"no verified .good yet\"}"
      : > "$STATE/workers.rollback-impossible.said" 2>/dev/null
    fi
    return 1
  fi
  for f in $FILES; do cmp -s "$GOOD/$f" "$W/$f" 2>/dev/null || changed="$changed $f"; done
  for f in $changed $IDENT; do
    install_one "$GOOD/$f" "$W/$f" || { ev "workers.update.rollback-failed" "{\"why\":\"$why\",\"file\":\"$f\"}"; return 1; }
  done
  [ -f "$GOOD/selftest.py" ] && install_one "$GOOD/selftest.py" "$W/selftest.py"
  # shellcheck disable=SC2086
  restart_units $(units_of $changed)
  rm -f "$PEND" "$UNITS_REC"
  setsha "$W" > "$PROV" 2>/dev/null
  # shellcheck disable=SC2086
  ev "$code" "{\"why\":\"$why\",\"restored\":$(num "$GOOD/WORKERS_VERSION" || echo 0),\"changed\":$(jlist $changed),\"rc\":$RC_JSON${extra:+,$extra}}"
  return 0
}

if [ "${1:-}" = "--rollback" ]; then
  restore_good "${2:-manual-ops}"; exit $?
fi

[ -f "$PUB" ] || { ev "workers.update.reject" '{"why":"no-pubkey"}'; exit 0; }

# ---------------------------------------------------------------------------------------------
# 1. settle the PREVIOUS cycle: health of the unproven set -> rollback now / still proving / proven
# health: 0 healthy, 1 crash loop (roll back now), 2 not (yet) healthy
health(){
  [ -f "$UNITS_REC" ] || return 0
  local u was n0 n crash=0 bad=0
  while read -r u was n0; do
    [ -n "$u" ] || continue
    n="$(nrestarts "$u")"
    [ $((n - ${n0:-0})) -ge 3 ] && crash=1
    [ $((n - ${n0:-0})) -ne 0 ] && bad=1
    [ "$was" = active ] && [ "$(active "$u")" != active ] && bad=1
  done < "$UNITS_REC"
  [ "$crash" -eq 1 ] && return 1
  [ "$bad" -eq 1 ] && return 2
  return 0
}
if [ -f "$PEND" ]; then
  AGE=$(( $(date +%s) - $(stat -c %Y "$PEND" 2>/dev/null || date +%s) ))
  health; H=$?
  if [ "$DRY" = 1 ]; then say "DRY_RUN: unproven set age=${AGE}s health=$H (0 ok,1 crash,2 not yet); no action taken"; exit 0; fi
  if [ "$H" -eq 1 ]; then restore_good "crash-loop" "\"age_s\":$AGE"; exit 0; fi
  if [ "$H" -eq 0 ] && [ "$AGE" -ge "$PROVE_S" ]; then
    rm -f "$PEND" "$UNITS_REC"                     # proven: fall through to the promotion gate
  elif [ "$AGE" -ge "$PEND_MAX" ]; then
    restore_good "no-healthy-tick" "\"age_s\":$AGE"; exit 0
  else
    exit 0                                          # still proving
  fi
fi

# 2. promotion gate: a proven set that this updater installed (identity == provenance) becomes .good.
#    A set that differs from the provenance record is a site hand-fix or tampering: left running, never promoted.
RUN_SHA="$(setsha "$W")"; WANT_SHA="$(cat "$PROV" 2>/dev/null)"
if [ -n "$WANT_SHA" ] && [ "$RUN_SHA" = "$WANT_SHA" ]; then
  if [ ! -f "$GOOD/WORKERS_VERSION" ] || [ "$(setsha "$GOOD")" != "$RUN_SHA" ]; then
    if [ "$DRY" != 1 ]; then
      rm -rf "$GOOD.new"
      if copy_set "$W" "$GOOD.new" && rm -rf "$GOOD" && mv "$GOOD.new" "$GOOD"; then
        rm -f "$STATE/workers.rollback-impossible.said"
        ev "workers.update.promote" "{\"good\":$(num "$W/WORKERS_VERSION" || echo 0)}"
      else
        ev "workers.update.fail" '{"stage":"promote"}'
      fi
    fi
  fi
elif [ -n "$WANT_SHA" ]; then
  # once per foreign identity, not every 30 minutes forever
  if [ ! -f "$STATE/workers.foreign.${RUN_SHA:0:12}" ]; then
    ev "workers.update.foreign" '{"note":"running workers are not the set this updater installed; left running, never promoted"}'
    : > "$STATE/workers.foreign.${RUN_SHA:0:12}" 2>/dev/null
  fi
fi

# 3. is there a newer release? (cheap: one small file)
RV="$(curl -fsSL --max-time 20 "$BASE/workers.version" 2>/dev/null | tr -dc '0-9')"
[ -z "$RV" ] && exit 0
LV="$(num "$W/WORKERS_VERSION")"; [ -z "$LV" ] && LV=0
[ "$RV" -le "$LV" ] 2>/dev/null && exit 0

# 4. one change per node at a time: the healer's own update is still proving -> wait a tick
[ -f "$STATE/update.pending" ] && { ev "workers.update.deferred" "{\"why\":\"healer-update-pending\",\"rv\":$RV}"; exit 0; }

# 5. rollout gate (decision 3): every release carries rollout.json with the build identity; a node
#    applies the release only when listed as canary or when sha1(DEVICE_ID) mod 100 < percent.
curl -fsSL --max-time 20 "$BASE/workers.rollout.json" -o "$TMP/rollout.json" 2>/dev/null \
  || { ev "workers.update.reject" "{\"why\":\"rollout-missing\",\"rv\":$RV}"; exit 0; }
GATE="$("$SYSPY" - "$TMP/rollout.json" "$NID" "$RV" <<'PYG' 2>/dev/null
import hashlib, json, sys
try:
    d = json.load(open(sys.argv[1]))
    nid, rv = sys.argv[2], int(sys.argv[3])
    if int(d.get("version", -1)) != rv:
        print("version-mismatch"); sys.exit(0)
    build = str(d.get("build", "")).strip()
    if not build:
        print("no-build"); sys.exit(0)
    pct = int(d.get("percent", 0)); canary = d.get("canary") or []
    mine = nid in canary or (int(hashlib.sha1(nid.encode()).hexdigest()[:8], 16) % 100) < pct
    print(("go " if mine else "wait ") + build)
except Exception:
    print("unreadable")
PYG
)"
case "$GATE" in
  go\ *)   BUILD_WANT="${GATE#go }" ;;
  wait\ *) exit 0 ;;                                   # not my turn yet: silent
  *)       ev "workers.update.reject" "{\"why\":\"rollout-${GATE:-unreadable}\",\"rv\":$RV}"; exit 0 ;;
esac

# 6. fetch bundle + detached signature; verify BEFORE anything is opened
curl -fsSL --max-time 90 "$BASE/workers.tar.gz"     -o "$TMP/workers.tar.gz"     || { ev "workers.update.fail" "{\"stage\":\"download\",\"rv\":$RV}"; exit 0; }
curl -fsSL --max-time 20 "$BASE/workers.tar.gz.sig" -o "$TMP/workers.tar.gz.sig" || { ev "workers.update.fail" "{\"stage\":\"sig\",\"rv\":$RV}"; exit 0; }
verify_sig "$TMP/workers.tar.gz" "$TMP/workers.tar.gz.sig"; VRC=$?
[ "$VRC" = 2 ] && { ev "workers.update.reject" "{\"why\":\"no-verifier\",\"rv\":$RV}"; exit 0; }
[ "$VRC" != 0 ] && { ev "workers.update.reject" "{\"why\":\"bad-signature\",\"rv\":$RV}"; exit 0; }

# 7. open in staging; the bundle must be the build the gate named; its env needs must be met
mkdir -p "$TMP/b" && tar -xzf "$TMP/workers.tar.gz" -C "$TMP/b" 2>/dev/null \
  || { ev "workers.update.reject" "{\"why\":\"bad-archive\",\"rv\":$RV}"; exit 0; }
for f in $FILES $IDENT selftest.py; do
  [ -f "$TMP/b/$f" ] || { ev "workers.update.reject" "{\"why\":\"incomplete-bundle\",\"missing\":\"$f\",\"rv\":$RV}"; exit 0; }
done
BUILD_GOT="$(tr -d '\r\n' < "$TMP/b/BUILD")"
[ "$BUILD_GOT" = "$BUILD_WANT" ] || { ev "workers.update.reject" "{\"why\":\"build-mismatch\",\"rv\":$RV,\"gate\":\"$BUILD_WANT\",\"bundle\":\"$BUILD_GOT\"}"; exit 0; }
[ "$(num "$TMP/b/WORKERS_VERSION")" = "$RV" ] || { ev "workers.update.reject" "{\"why\":\"version-mismatch\",\"rv\":$RV}"; exit 0; }
if [ -f "$TMP/b/REQUIRES_ENV" ]; then
  while read -r key; do
    key="${key%%[#=]*}"; key="${key//[[:space:]]/}"; [ -n "$key" ] || continue
    grep -qE "^${key}=." "$ENVF" 2>/dev/null || { ev "workers.update.reject" "{\"why\":\"env-missing\",\"key\":\"$key\",\"rv\":$RV}"; exit 0; }
  done < "$TMP/b/REQUIRES_ENV"
fi

# 8. the bundle proves itself with the interpreter the units use, before the running set is touched
if ! ( cd "$TMP/b" && SELFTEST_REQUIRE_DEPS=1 "$PY" selftest.py >"$TMP/selftest.out" 2>&1 ); then
  say "selftest: $(grep -E '^FAIL' "$TMP/selftest.out" | head -3 | tr '\n' ' ')"
  ev "workers.update.reject" "{\"why\":\"selftest-failed\",\"rv\":$RV}"; exit 0
fi

# 9. what actually changes? -> which units restart
CHANGED=""
for f in $FILES; do cmp -s "$TMP/b/$f" "$W/$f" 2>/dev/null || CHANGED="$CHANGED $f"; done
# shellcheck disable=SC2086
UNITS="$(units_of $CHANGED)"
if [ "$DRY" = 1 ]; then
  # shellcheck disable=SC2086
  say "DRY_RUN: would install v$RV ($BUILD_WANT) from v$LV; changed=$(jlist $CHANGED) restart=$(jlist $UNITS); nothing changed"
  exit 0
fi

if [ -z "$CHANGED" ]; then
  # 10a. identity-only release (the arming release v1 is exactly this): nothing to restart, nothing
  #      to prove - the running set IS the proven set - so record provenance and promote at once.
  for f in $IDENT selftest.py; do
    install_one "$TMP/b/$f" "$W/$f" || { ev "workers.update.fail" "{\"stage\":\"install\",\"file\":\"$f\",\"rv\":$RV}"; exit 0; }
  done
  setsha "$W" > "$PROV" 2>/dev/null
  rm -rf "$GOOD.new"
  if copy_set "$W" "$GOOD.new" && rm -rf "$GOOD" && mv "$GOOD.new" "$GOOD"; then
    rm -f "$STATE/workers.rollback-impossible.said"
    ev "workers.update.ok" "{\"from\":$LV,\"to\":$RV,\"changed\":[],\"restarted\":{},\"promoted\":true}"
  else
    ev "workers.update.ok" "{\"from\":$LV,\"to\":$RV,\"changed\":[],\"restarted\":{},\"promoted\":false}"
    ev "workers.update.fail" '{"stage":"promote"}'
  fi
  exit 0
fi

# 10b. real change: remember what was running (for the health check and for a restart failure),
#      install atomically, restart only the changed units, mark unproven.
mkdir -p "$TMP/pre" && for f in $FILES $IDENT; do [ -f "$W/$f" ] && cp -p "$W/$f" "$TMP/pre/$f"; done
PRE_ACTIVE=""
for u in $UNITS; do PRE_ACTIVE="$PRE_ACTIVE $u=$(active "$u")"; done
for f in $CHANGED $IDENT selftest.py; do
  if ! install_one "$TMP/b/$f" "$W/$f"; then
    for g in $FILES $IDENT; do [ -f "$TMP/pre/$g" ] && install_one "$TMP/pre/$g" "$W/$g"; done
    ev "workers.update.fail" "{\"stage\":\"install\",\"file\":\"$f\",\"rv\":$RV}"; exit 0
  fi
done
# shellcheck disable=SC2086
if ! restart_units $UNITS; then
  # a unit refused to restart: the new file is on disk but the old process is running -> that set can
  # never be proven. Put the previous files back (from .good when we have it, else the pre-install copy).
  RC_FAIL="$RC_JSON"
  if [ -f "$GOOD/WORKERS_VERSION" ]; then
    restore_good "restart-failed" "\"rv\":$RV,\"restart_rc\":$RC_FAIL"
  else
    for g in $FILES $IDENT; do [ -f "$TMP/pre/$g" ] && install_one "$TMP/pre/$g" "$W/$g"; done
    # shellcheck disable=SC2086
    restart_units $UNITS
    ev "workers.update.fail" "{\"stage\":\"restart\",\"rv\":$RV,\"rc\":$RC_FAIL,\"restored\":\"pre-install\"}"
  fi
  exit 0
fi
: > "$UNITS_REC" 2>/dev/null
for u in $UNITS; do
  was="${PRE_ACTIVE##* $u=}"; was="${was%% *}"
  printf '%s %s %s\n' "$u" "${was:-unknown}" "$(nrestarts "$u")" >> "$UNITS_REC" 2>/dev/null
done
setsha "$W" > "$PROV" 2>/dev/null
: > "$PEND" 2>/dev/null || true
# shellcheck disable=SC2086
ev "workers.update.ok" "{\"from\":$LV,\"to\":$RV,\"changed\":$(jlist $CHANGED),\"restarted\":$RC_JSON,\"promoted\":false}"
