"""
Sandboxed code execution for coding questions.

Each test case runs in its own ephemeral, network-disabled Docker container
(`docker run --rm --network none ...`) with CPU/memory/process-count limits
and a hard wall-clock timeout, using the `docker` CLI already on the host --
no separate microservice or Docker-in-Docker setup needed.

The container is further locked down beyond networking and resource limits:
the root filesystem is mounted read-only (with a small size-capped tmpfs at
/tmp for anything that wants scratch space), every Linux capability is
dropped, privilege escalation is disabled, and the process runs as an
unprivileged, non-root UID. None of this is defense against a
sufficiently-motivated container escape -- Docker's own kernel-level
isolation is what actually carries that weight -- but it means student code
has no legitimate avenue to persist anything, escalate, or hold onto a
capability it has no reason to need in the first place.

The student's source code is never written to disk or interpolated into a
shell string. It's base64-encoded and handed to a small trusted bootstrap
script (run via `python3 -c` / `node -e`, passed as a real argv entry, never
through a shell) that decodes and executes it, while the container's real
stdin/stdout are left free for the test case's actual program input/output.

Fails closed: if the `docker` CLI isn't on PATH, the daemon isn't reachable,
or CODE_EXECUTION_ENABLED is off, `is_available()` returns False and callers
should treat coding questions as ungraded/unavailable rather than crash --
the same graceful-degradation pattern used for the optional AI worker
(see app/ai/ai_worker_client.py).
"""
import base64
import logging
import shutil
import subprocess
import threading
import time
import uuid

from app.core.config import settings

logger = logging.getLogger("app")

SUPPORTED_LANGUAGES = ("python", "javascript")

_IMAGES = {
    "python": "python:3.11-slim",
    "javascript": "node:20-slim",
}

_MAX_ERROR_CHARS = 2000

# Hard ceiling on what a single run may emit. Enforced INSIDE the container,
# at the top of the bootstrap, because the host reads the container's output
# with subprocess.run(capture_output=True), which buffers the whole stream in
# the API process's memory with no limit of its own. `while True: print("x")`
# is a one-line student mistake (or a deliberate attack) that would otherwise
# stream several hundred MB into the backend before the wall-clock timeout
# fired -- and every concurrent run would do it at once. The container's own
# --memory cap does not help here: the bytes are in the *host's* buffer, not
# the container's.
#
# Capping at the source also keeps the value honest end-to-end: what's stored
# and shown to the examiner is what the program actually produced, truncated,
# rather than a stream the host silently gave up on midway.
_MAX_OUTPUT_BYTES = 64 * 1024

# Concurrency ceiling. Each run is a full `docker run`; an exam hall of
# students hammering "Run" would otherwise spawn unbounded containers and
# take the host down. Sync FastAPI endpoints execute in a worker threadpool,
# so a threading semaphore is the right primitive.
_run_slots = threading.BoundedSemaphore(settings.CODE_EXECUTION_MAX_CONCURRENT)
_SLOT_WAIT_SECONDS = 10


def _python_bootstrap(code_b64: str) -> str:
    # code_b64 is base64 (alphabet A-Za-z0-9+/=), so it cannot contain a quote
    # or newline and cannot break out of the string literal it's embedded in.
    return (
        "import sys, base64\n"
        f"_MAX = {_MAX_OUTPUT_BYTES}\n"
        "class _Capped:\n"
        "    def __init__(self, s):\n"
        "        self._s = s\n"
        "        self._n = 0\n"
        "    def write(self, d):\n"
        "        if self._n < _MAX:\n"
        "            chunk = d[:_MAX - self._n]\n"
        "            self._n += len(chunk)\n"
        "            self._s.write(chunk)\n"
        "        return len(d)\n"
        "    def flush(self):\n"
        "        self._s.flush()\n"
        "    def __getattr__(self, name):\n"
        "        return getattr(self._s, name)\n"
        "sys.stdout = _Capped(sys.stdout)\n"
        "sys.stderr = _Capped(sys.stderr)\n"
        f'code = base64.b64decode("{code_b64}").decode("utf-8")\n'
        "try:\n"
        "    exec(compile(code, 'solution.py', 'exec'), {'__name__': '__main__'})\n"
        "except SystemExit:\n"
        "    raise\n"
        "except Exception as e:\n"
        "    print(repr(e), file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )


def _js_bootstrap(code_b64: str) -> str:
    return (
        f"const _MAX = {_MAX_OUTPUT_BYTES};\n"
        "function _cap(stream) {\n"
        "  let n = 0;\n"
        "  const orig = stream.write.bind(stream);\n"
        "  stream.write = (chunk, ...rest) => {\n"
        "    if (n >= _MAX) return true;\n"
        "    const s = typeof chunk === 'string' ? chunk : String(chunk);\n"
        "    const slice = s.slice(0, _MAX - n);\n"
        "    n += slice.length;\n"
        "    return orig(slice, ...rest);\n"
        "  };\n"
        "}\n"
        "_cap(process.stdout);\n"
        "_cap(process.stderr);\n"
        f'const code = Buffer.from("{code_b64}", "base64").toString("utf-8");\n'
        "try {\n"
        "  eval(code);\n"
        "} catch (e) {\n"
        "  process.stderr.write(String((e && e.stack) || e));\n"
        "  process.exit(1);\n"
        "}\n"
    )


# Cached availability, but only briefly. This used to be @lru_cache, i.e.
# cached for the lifetime of the process: if Docker happened to be down at
# the first call, coding questions stayed "unavailable" until someone
# restarted the API, and if Docker died later the app kept confidently
# shelling out to a daemon that wasn't there. A short TTL keeps the check
# cheap (it's a subprocess spawn per miss) while letting the answer actually
# track reality.
_AVAILABILITY_TTL_SECONDS = 30
_availability_cache: dict = {"value": None, "checked_at": 0.0}
_availability_lock = threading.Lock()


def is_available() -> bool:
    now = time.monotonic()
    with _availability_lock:
        cached = _availability_cache["value"]
        if cached is not None and (now - _availability_cache["checked_at"]) < _AVAILABILITY_TTL_SECONDS:
            return cached
    result = _probe_docker()
    with _availability_lock:
        _availability_cache["value"] = result
        _availability_cache["checked_at"] = time.monotonic()
    return result


def _probe_docker() -> bool:
    if not settings.CODE_EXECUTION_ENABLED:
        return False
    if shutil.which("docker") is None:
        return False
    # The CLI being on PATH doesn't mean the daemon is up -- on Windows/Mac,
    # Docker Desktop installs `docker.exe` on PATH even when the app itself
    # isn't running. Without this check, `is_available()` reports True, so
    # `run_against_test_cases` proceeds straight to `docker run`, which then
    # fails with a raw daemon-connection error (e.g. "failed to connect to
    # the docker API at npipe:////./pipe/dockerDesktopLinuxEngine...") that
    # gets surfaced verbatim as a test case's "error" field -- an internal
    # infra detail leaking into the student-facing exam UI instead of the
    # clean "unavailable" message this function exists to produce. `docker
    # info` is a cheap, side-effect-free daemon ping; a short timeout keeps a
    # hung daemon from blocking the request this is called from.
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        ).returncode == 0
    except Exception:
        return False


def run_against_test_cases(language: str, source_code: str, test_cases: list[dict], time_limit_seconds: int | None = None) -> dict:
    """
    test_cases: list of {"input": str, "expected_output": str, "is_sample": bool}
    Returns {"available": bool, "results": [...], "all_passed": bool, "message": str | None}.
    Output comparison is whitespace-trimmed (leading/trailing), matching the
    typical stdin/stdout contract used by competitive-programming-style judges.
    """
    if language not in SUPPORTED_LANGUAGES:
        return {"available": False, "results": [], "all_passed": False, "message": f"Unsupported language: {language}"}
    if not is_available():
        return {"available": False, "results": [], "all_passed": False, "message": "Code execution is unavailable (Docker isn't installed or running on the server)."}
    if not test_cases:
        return {"available": True, "results": [], "all_passed": False, "message": "This question has no test cases configured."}

    timeout = time_limit_seconds or settings.CODE_EXECUTION_DEFAULT_TIMEOUT_SECONDS
    results = []
    for case in test_cases:
        run_result = _run_one(language, source_code, case.get("input", "") or "", timeout)
        expected = (case.get("expected_output") or "").strip()
        actual = run_result["stdout"].strip()
        passed = run_result["error"] is None and actual == expected
        results.append({
            "passed": passed,
            "is_sample": bool(case.get("is_sample")),
            "input": case.get("input", ""),
            "expected_output": expected,
            "actual_output": actual,
            "error": run_result["error"],
            "time_ms": run_result["time_ms"],
        })
    return {"available": True, "results": results, "all_passed": all(r["passed"] for r in results), "message": None}


def _run_one(language: str, source_code: str, stdin_input: str, timeout_seconds: int) -> dict:
    image = _IMAGES[language]
    code_b64 = base64.b64encode(source_code.encode("utf-8")).decode("ascii")
    container_name = f"exam-run-{uuid.uuid4().hex[:12]}"

    if language == "python":
        run_cmd = ["python3", "-c", _python_bootstrap(code_b64)]
    else:
        run_cmd = ["node", "-e", _js_bootstrap(code_b64)]

    docker_cmd = [
        "docker", "run", "--rm", "-i",
        "--name", container_name,
        "--network", "none",
        "--memory", settings.CODE_EXECUTION_MEMORY_LIMIT,
        # Docker defaults memory-swap to 2x --memory when unset, which would
        # let a container swap its way past the "hard" limit above.
        "--memory-swap", settings.CODE_EXECUTION_MEMORY_LIMIT,
        "--cpus", settings.CODE_EXECUTION_CPUS,
        "--pids-limit", "64",
        # Isolation beyond networking/resources -- see the module docstring.
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "1000:1000",
        "-e", "HOME=/tmp",
        image, *run_cmd,
    ]

    # Bound how many containers can exist at once (see _run_slots). Waiting a
    # short while is better than either failing instantly under a burst or
    # queueing without limit; if the wait is exceeded the caller gets an
    # honest "busy" rather than a timeout that reads like their code hung.
    if not _run_slots.acquire(timeout=_SLOT_WAIT_SECONDS):
        logger.warning("Code execution rejected: all %s slots busy", settings.CODE_EXECUTION_MAX_CONCURRENT)
        return {"stdout": "", "error": "The grading sandbox is busy. Please try again in a moment.", "time_ms": 0}

    started = time.monotonic()
    try:
        proc = subprocess.run(
            docker_cmd, input=stdin_input, capture_output=True, text=True,
            timeout=timeout_seconds, encoding="utf-8", errors="replace",
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # Second line of defence behind the in-container cap: a program that
        # writes to fd 1 directly bypasses the wrapped sys.stdout, so never
        # hand an unbounded string to the response/DB either.
        stdout = (proc.stdout or "")[:_MAX_OUTPUT_BYTES]
        if proc.returncode != 0:
            error = (proc.stderr or f"Exited with code {proc.returncode}").strip()[:_MAX_ERROR_CHARS]
            return {"stdout": stdout, "error": error, "time_ms": elapsed_ms}
        return {"stdout": stdout, "error": None, "time_ms": elapsed_ms}
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5)
        return {"stdout": "", "error": "Time limit exceeded.", "time_ms": timeout_seconds * 1000}
    except FileNotFoundError:
        return {"stdout": "", "error": "Docker is not installed or not on PATH.", "time_ms": 0}
    except Exception as error:
        logger.exception("Sandboxed execution failed unexpectedly")
        # Deliberately generic: `error` can carry daemon paths and other host
        # infrastructure detail, and this string is rendered in the student's
        # exam UI. The real cause is in the log above, tied to the request id.
        return {"stdout": "", "error": "Execution failed due to a server error.", "time_ms": 0}
    finally:
        # Must release on every path, including the timeout/kill branch, or
        # the pool leaks a slot per timed-out submission and eventually
        # deadlocks every future run.
        _run_slots.release()
