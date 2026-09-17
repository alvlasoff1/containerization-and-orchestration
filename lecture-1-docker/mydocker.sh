#!/usr/bin/env bash
#
# mydocker.sh — Part 5: "your own Docker" in one command.
#
# Assembles everything from parts 2-4 into a single launcher for the `api`
# service:
#
#   Part 2  namespaces  -> unshare (pid, mount, net, uts, ipc, user)
#   Part 3  cgroups     -> a cgroup v2 with memory.max / cpu.max / pids.max
#   Part 4  privileges  -> capsh drops CAP_SYS_TIME + a seccomp filter that
#                          traps the time-setting syscalls (SIGSYS)
#
# Result: `api` starts isolated, resource-capped and de-privileged, and
# /health answers from inside its own network namespace.
#
# Run as root (cgroup writes + namespace setup need it, same as parts 3-4):
#   sudo ./mydocker.sh
#
# Cleanup (cgroup dir, child processes) happens automatically on exit.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config — the same limits demonstrated in Part 3, drops from Part 4.
# ---------------------------------------------------------------------------
NAME="mydocker"
PORT="${PORT:-8080}"
MEM_MAX=$((30 * 1024 * 1024))     # 30 MiB ceiling  -> OOM on /eat?mb=...
CPU_MAX="50000 100000"            # 0.5 core        -> throttling on /burn
PIDS_MAX=20                       # max 20 tasks    -> fork bomb can't multiply
DROP_CAPS="cap_sys_time"          # api needs no capabilities at all
CGROOT="/sys/fs/cgroup"
CGDIR="${CGROOT}/${NAME}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Preconditions.
# ---------------------------------------------------------------------------
if [[ "$(id -u)" -ne 0 ]]; then
  echo "mydocker: run me as root (sudo ./mydocker.sh)" >&2
  exit 1
fi

if [[ ! -f "${CGROOT}/cgroup.controllers" ]]; then
  echo "mydocker: cgroup v2 unified hierarchy not found at ${CGROOT}" >&2
  exit 1
fi

for tool in unshare capsh ip curl cc; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "mydocker: required tool '$tool' not found in PATH" >&2
    exit 1
  }
done

# Pick the api binary: installed one from Part 1, else the arch build in api/.
if [[ -n "${API_BIN:-}" ]]; then
  :
elif [[ -x /usr/local/bin/api ]]; then
  API_BIN=/usr/local/bin/api
else
  case "$(uname -m)" in
    x86_64)          API_BIN="${SCRIPT_DIR}/api/api-linux-amd64" ;;
    aarch64|arm64)   API_BIN="${SCRIPT_DIR}/api/api-linux-arm64" ;;
    *) echo "mydocker: no api binary for arch $(uname -m)" >&2; exit 1 ;;
  esac
fi
[[ -x "$API_BIN" ]] || { echo "mydocker: api binary not executable: $API_BIN" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Part 4b — build the seccomp filter helper.
#
# There is no stock CLI that applies an arbitrary seccomp profile to an
# arbitrary command, so we compile a tiny loader. It sets no_new_privs, installs
# a BPF filter that TRAPs (SIGSYS) the clock/time-setting syscalls, and execs
# api. This mirrors seccomp.json next to this script (same blocked syscalls).
# ---------------------------------------------------------------------------
SECCOMP_WRAP="$(mktemp)"
SECCOMP_SRC="$(mktemp --suffix=.c)"

cat >"$SECCOMP_SRC" <<'CSRC'
#include <stddef.h>
#include <stdio.h>
#include <unistd.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <sys/prctl.h>
#include <sys/syscall.h>

/* Trap (SIGSYS) syscall `nr`; fall through to the next check otherwise. */
#define DENY(nr) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_TRAP),

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: seccomp_wrap CMD [ARGS...]\n");
        return 2;
    }
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        DENY(SYS_settimeofday)
        DENY(SYS_clock_settime)
        DENY(SYS_clock_adjtime)
        DENY(SYS_adjtimex)
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog prog = {
        .len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
        .filter = filter,
    };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) { perror("no_new_privs"); return 1; }
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)) { perror("seccomp"); return 1; }
    execvp(argv[1], &argv[1]);
    perror("execvp");
    return 127;
}
CSRC

cc -O2 -o "$SECCOMP_WRAP" "$SECCOMP_SRC"
rm -f "$SECCOMP_SRC"

# ---------------------------------------------------------------------------
# Part 3 — cgroup v2 limits.
# ---------------------------------------------------------------------------
# Make sure the controllers we need are delegated into child cgroups.
for c in memory cpu pids; do
  echo "+$c" > "${CGROOT}/cgroup.subtree_control" 2>/dev/null || true
done

mkdir -p "$CGDIR"
echo "$MEM_MAX"  > "${CGDIR}/memory.max"
echo "$CPU_MAX"  > "${CGDIR}/cpu.max"
echo "$PIDS_MAX" > "${CGDIR}/pids.max"

# Move THIS shell into the cgroup; every child (unshare -> api) inherits it.
echo $$ > "${CGDIR}/cgroup.procs"

cleanup() {
  set +e
  # Move ourselves back to the root cgroup so the leaf can be removed.
  echo $$ > "${CGROOT}/cgroup.procs" 2>/dev/null
  if [[ -f "${CGDIR}/cgroup.procs" ]]; then
    while read -r pid; do [[ -n "$pid" ]] && kill "$pid" 2>/dev/null; done < "${CGDIR}/cgroup.procs"
  fi
  rmdir "$CGDIR" 2>/dev/null
  rm -f "$SECCOMP_WRAP" 2>/dev/null
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Parts 2 + 4a — namespaces, then drop capabilities and run api under seccomp.
#
# unshare puts us in fresh namespaces and runs the inner shell as PID 1:
#   --pid --fork --mount-proc  : own process list, api-tree only in `ps`
#   --mount                    : own mount table (needed for a private /proc)
#   --net                      : own empty network stack (just loopback)
#   --uts                      : own hostname
#   --ipc                      : own IPC objects
#   --user --map-root-user     : own user namespace; root inside == unprivileged
#                                uid outside
#
# Inside, capsh drops CAP_SYS_TIME from the bounding set, then execs the seccomp
# loader, which execs api.
# ---------------------------------------------------------------------------
echo "mydocker: starting '${NAME}' -> api on :${PORT}"
echo "mydocker:   mem<=$((MEM_MAX/1024/1024))MiB  cpu=${CPU_MAX%% *}/${CPU_MAX##* }us  pids<=${PIDS_MAX}  drop=${DROP_CAPS}  +seccomp"

exec unshare \
  --fork --pid --mount-proc \
  --mount --net --uts --ipc \
  --user --map-root-user \
  -- bash -euo pipefail -c '
    NAME="$1"; PORT="$2"; DROP_CAPS="$3"; API_BIN="$4"; SECCOMP_WRAP="$5"

    hostname "$NAME"          # Part 2: own hostname (uts)
    ip link set lo up         # Part 2: bring up the private loopback

    # Part 4: drop the capability, then Part 4b: seccomp-wrap api and launch it.
    capsh --drop="$DROP_CAPS" -- -c "exec \"\$0\" \"\$1\"" "$SECCOMP_WRAP" "$API_BIN" &
    api_pid=$!
    trap "kill $api_pid 2>/dev/null" TERM INT

    # Health check from inside the net namespace.
    ok=""
    for _ in $(seq 1 50); do
      if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then ok=1; break; fi
      sleep 0.1
    done
    if [[ -n "$ok" ]]; then
      echo "mydocker: /health -> $(curl -fsS "http://127.0.0.1:${PORT}/health")"
      echo "mydocker: api is PID $(pgrep -x api || echo "?") inside, running as uid $(id -u)"
      echo "mydocker: up. Ctrl-C to stop."
    else
      echo "mydocker: /health did not come up" >&2
      kill "$api_pid" 2>/dev/null || true
    fi

    wait "$api_pid"
  ' _ "$NAME" "$PORT" "$DROP_CAPS" "$API_BIN" "$SECCOMP_WRAP"
