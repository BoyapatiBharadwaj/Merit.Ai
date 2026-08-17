"""Hardening of the Docker sandbox used for coding-question execution
(app/services/code_runner_service.py).
"""
import subprocess

from app.core.config import settings
from app.services import code_runner_service


def _capture_docker_cmd(monkeypatch, stdout="", returncode=0):
    """Patch subprocess.run to record the argv it's called with instead of
    actually invoking Docker, and return a list that will hold that argv."""
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(code_runner_service.subprocess, "run", fake_run)
    return captured


def test_run_drops_all_capabilities_and_disables_privilege_escalation(monkeypatch):
    captured = _capture_docker_cmd(monkeypatch, stdout="3\n")
    code_runner_service._run_one("python", "print(3)", "", timeout_seconds=5)

    cmd = captured[0]
    assert "--cap-drop" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in cmd and cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"


def test_run_uses_a_read_only_root_filesystem_with_scratch_tmpfs(monkeypatch):
    captured = _capture_docker_cmd(monkeypatch, stdout="3\n")
    code_runner_service._run_one("python", "print(3)", "", timeout_seconds=5)

    cmd = captured[0]
    assert "--read-only" in cmd
    assert "--tmpfs" in cmd
    tmpfs_spec = cmd[cmd.index("--tmpfs") + 1]
    assert tmpfs_spec.startswith("/tmp:")
    assert "noexec" in tmpfs_spec


def test_run_executes_as_a_non_root_unprivileged_user(monkeypatch):
    captured = _capture_docker_cmd(monkeypatch, stdout="3\n")
    code_runner_service._run_one("python", "print(3)", "", timeout_seconds=5)

    cmd = captured[0]
    assert "--user" in cmd
    user_spec = cmd[cmd.index("--user") + 1]
    assert user_spec not in ("0:0", "root")
    # HOME must point somewhere writable (the tmpfs): the numeric UID above
    # has no /etc/passwd entry for the container runtime to fall back on.
    assert "-e" in cmd and "HOME=/tmp" in cmd


def test_memory_swap_is_pinned_to_the_hard_memory_limit(monkeypatch):
    """Without this, Docker defaults memory-swap to 2x --memory, letting a
    container swap its way past the configured hard limit."""
    captured = _capture_docker_cmd(monkeypatch, stdout="3\n")
    code_runner_service._run_one("python", "print(3)", "", timeout_seconds=5)

    cmd = captured[0]
    memory_value = cmd[cmd.index("--memory") + 1]
    memory_swap_value = cmd[cmd.index("--memory-swap") + 1]
    assert memory_swap_value == memory_value == settings.CODE_EXECUTION_MEMORY_LIMIT


def test_preexisting_isolation_flags_are_untouched(monkeypatch):
    """Regression guard: the hardening pass must not have dropped any of the
    isolation this container already had."""
    captured = _capture_docker_cmd(monkeypatch, stdout="3\n")
    code_runner_service._run_one("python", "print(3)", "", timeout_seconds=5)

    cmd = captured[0]
    assert "--rm" in cmd
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--pids-limit" in cmd and cmd[cmd.index("--pids-limit") + 1] == "64"


def test_javascript_run_gets_the_same_hardening_flags(monkeypatch):
    captured = _capture_docker_cmd(monkeypatch, stdout="3\n")
    code_runner_service._run_one("javascript", "console.log(3)", "", timeout_seconds=5)

    cmd = captured[0]
    assert "--read-only" in cmd
    assert "--cap-drop" in cmd
    assert "--user" in cmd


def test_run_still_reports_correct_stdout_and_no_error_on_success(monkeypatch):
    """The hardening flags must not change the function's return contract
    for a normal successful run."""
    _capture_docker_cmd(monkeypatch, stdout="3\n", returncode=0)
    result = code_runner_service._run_one("python", "print(3)", "", timeout_seconds=5)
    assert result["error"] is None
    assert result["stdout"] == "3\n"
