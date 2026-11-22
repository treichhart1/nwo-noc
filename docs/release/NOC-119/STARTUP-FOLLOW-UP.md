# NOC-119A — independent Ubuntu startup investigation

Continue PR123 on `feature/noc-119-pilot-security`, starting from
`2d4d5c1ebca934d2c8157ac0ddbf035cc1081486`. No new milestone or PR.
No applicable AGENTS.md was found; the initial working tree was clean.

## Baseline evidence and controlled experiment

Run670 booted a real Ubuntu24.04 guest under QEMU TCG, provisioned packages,
and ran the actual installer. PostgreSQL and Redis were healthy. Backend stayed
running/health-starting with connection-refused health probes. One probe exited137,
but container State.OOMKilled was false. Workers/frontend correctly waited for
backend readiness. No application exception was observed. These facts establish
failed readiness, not its root cause or a successful installation/recovery.

Diagnostic commit0103b06 adds numeric CPython import timings, process CPU/memory/
thread counts, probe durations and configured memory limit to the existing
credential-safe failure collector. Only identifiers/numbers/static flags are
exported; raw logs and environment values remain private. Its temporary profiling
environment setting correctly failed the existing empty-environment assertion;
the assertion is retained unchanged. An intermediate candidate used uvicorn via
`python -X importtime -m`; that also changed the test-only startup fault injector's
argv matching. All launch instrumentation is removed from the final candidate;
production and controlled-test command/environment match the starting commit.
The original instrumented baseline retains the failure profile separately.

The image deliberately installed dependencies with `--no-compile`, excluded local
pyc files and disabled runtime bytecode writes. Thus a read-only cold start must
compile dependency and application Python sources on every process start.
Native local diagnostic imports (three per case, synthetic configuration, no DB
connections) took2.424/2.070/1.871s with existing bytecode and3.977/5.184/5.329s
with an empty bytecode prefix and writes disabled. This is a candidate cause,
not proof of TCG root cause or independent VM acceptance.

The candidate compiles application and dependency bytecode on the build host,
using CPython checked-hash invalidation. Source changes invalidate the cache;
unchecked bytecode is not trusted. Caches and code are root-owned/non-writable
by the runtime UID. Native image evidence must verify real source hashes,
cache headers and non-writability for app.main/FastAPI/SQLAlchemy/Uvicorn.
Python3.12, dependencies/locks, healthchecks,180s startup contract, resource
limits, private bindings, read-only filesystem and database readiness are unchanged.

Two related probe costs were reproduced locally: importing worker_health also
imported the complete ORM/monitoring runtime merely to get ID_FILE (916ms native
import); the planning --health path loaded ORM/GIS before checking a file's age.
The unchanged worker identity is now a shared dependency-free constant; Redis
lookup, worker-specific key and0<=age<60s remain enforced. The planning CLI now
dispatches its same150s heartbeat check before heavy worker imports and uses the
same extracted private storage-path function. Missing/stale/symlink heartbeats
fail. Settings validation is retained. Worker probe import fell to256ms native;
this is diagnostic evidence, not a claim that the VM probe deadline passed.
Nine regression cases verify fresh/stale/future/missing worker heartbeat behavior,
planning freshness/symlink rejection, and absence of ORM/GIS initialization in
fresh probe processes. Exact-source static reviews were updated for the unchanged
moved UUID marker; the original private-listener fingerprint is restored with
the original launch command. No new rule/CVE ignore or severity exception was added.

Two existing PostgreSQL API tests also expired on the actual CI wall clock:
their fixed2026-11-13 samples fell outside the real seven-day history window on
2026-11-22. Those API tests now freeze the history service clock to their existing
fixture timestamp. Real queries, retention limits, auth/role checks, bounds and
all sample assertions remain unchanged; application retention is not extended.

## Native initialization and virtual CPU investigation

Instrumented run672/commit0103b06 reproduced failed startup. Its last completed
native import record is uvloop.includes; no application-startup-complete flag was
observed. Uvicorn import itself was4.711s; app.api.routes completed27.253s in the
retained profile. These records do not establish that bytecode work alone caused
the180s failure. Existing OOMKilled=false is not a substitute for restart/exit
history. The failure collector now retains numeric restart/die codes, OOM event
counts, whitelisted process resources, CPU features and kernel failure flags.

The locked NumPy2.5.5 wheel reports a required X86_V2 baseline. The original
harness passed no -cpu option. [QEMU's CPU model documentation](https://www.qemu.org/docs/master/system/qemu-cpu-models.html)
states the default is qemu64 and lists it below x86-64-v2; [NumPy's build contract](https://numpy.org/doc/stable/reference/simd/build-options.html)
requires baseline CPU features and identifies X86_V2 as its x86-64 minimum.
That virtual CPU is therefore not an adequate dependency compatibility target.
The harness now explicitly selects the Nehalem x86-64-v2 model with the same
2vCPU/5120MiB limits. It queries actual QEMU model expansion for both qemu64 and
Nehalem, requires all selected-model v2 flags, verifies the source guest's actual
flags, and imports NumPy/uvloop/rasterio/pyproj in the exact read-only candidate
image before installation. A failed native import fails the job; no timeout,
healthcheck, seccomp or capability bypass is introduced. Runtime acceptance and
the exact causal disposition remain pending actual corrected-guest execution.

## Acceptance requirements

The instrumented baseline and corrected-image VM runs must establish the actual
startup cause and whether the candidate resolves it. A native import comparison
alone cannot mark that finding fixed. Final exact commit, timings, raw security
artifacts, all job conclusions and terminal status are recorded in PR123's ledger.
Until those tests complete, installation/reboot/second-host recovery are not PASS.

Require actual source install, verification, guarded upgrade, encrypted backup,
kernel reboot and automatic startup. Then power off the source, independently
verify the signed synthetic transport/fingerprint, restore to the second fresh
guest, verify auth/history/documents/keys/Redis and reboot that guest. Corrupted,
missing and wrong-key material must fail safely. Scan the exact tested image IDs.
Do not substitute another image's scan or claim production artifact authorization.

Existing vendor security, legal, off-site failure-domain, real VPN/MFA, operational
scanner-definition, firmware/provider and consistent15s performance blockers remain.
This investigation grants no live-pilot or production authorization.


For causal verification, the source VM now starts with the original qemu64 CPU,
provisions the same pinned image and runs bounded per-module NumPy/uvloop/GIS
imports. It then powers off, reboots the same disk with Nehalem, verifies actual
CPU features and repeats the probes against the identical immutable image ID.
Only successful corrected-CPU imports may proceed to the actual installer.
Reports retain numeric exit/signal/timeout/duration and static error flags, not raw
errors. This distinguishes a CPU ABI rejection, native import stall and general
startup cost. A/B results remain pending until actually executed; a documented
CPU mismatch alone is not claimed to prove the complete failure cause.

The source and receiver recovery guests still use separate roots/kernels/daemons;
the compatibility reboot occurs before any NOC installation. Probe image IDs are
included in exact-image scanning. Production startup and health limits are unchanged.

The local workspace reached zero writable space during a new disposable DB
attempt. The failed container/network and this follow-up's completed synthetic
pytest fixtures were removed; existing images, volumes, repository work and
historical artifacts were not pruned. All earlier code was already pushed.
Follow-up forensic edits use an atomic, expected-parent GitHub tree/commit/ref
update on the same branch; the local checkout remains at715a749 until writable
space is restored. In-memory Python parsing passed; execution evidence must come
from the final-commit CI. No local PostgreSQL initialization or VM success is claimed.
