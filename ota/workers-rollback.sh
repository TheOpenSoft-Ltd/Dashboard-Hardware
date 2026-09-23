#!/bin/bash
# workers-rollback.sh - manual rollback of the station workers to the last PROVEN set (.good).
# For ops and the field: `~/.config/pat-smart/workers/workers-rollback.sh` as admin. Restores only
# the files that differ from .good, restarts only their units (one sudo call each), clears the
# prove marker and emits workers.rollback.manual. Refuses honestly (rollback-impossible) when no
# proven set exists yet on this node. The logic lives in workers-selfupdate.sh so the two can never drift.
exec "$(dirname "$(readlink -f "$0")")/workers-selfupdate.sh" --rollback "manual-${1:-ops}"
