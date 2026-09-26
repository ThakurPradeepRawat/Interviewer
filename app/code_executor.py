"""
Executes candidate-submitted Python code in an isolated subprocess with a hard
timeout and a restricted builtins list.

IMPORTANT — scope and limits:
This is a demo-grade sandbox suitable for a portfolio project and for trusted,
low-stakes evaluation. It reduces risk (subprocess isolation, resource limits,
timeout, blocked imports) but is NOT a substitute for a real sandboxing layer
(gVisor, Firecracker microVMs, Docker with seccomp, or a hosted code-execution
API) before ever exposing this to arbitrary untrusted internet users.
"""

import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.config import settings
from app.schemas import CodeExecResult

BLOCKED_IMPORTS = {"os", "sys", "subprocess", "socket", "shutil", "pathlib", "requests", "urllib"}

_RUNNER_TEMPLATE = """
import builtins

_blocked = {blocked!r}
_real_import = builtins.__import__

def _guarded_import(name, *args, **kwargs):
    top = name.split(".")[0]
    if top in _blocked:
        raise ImportError(f"import of '{{top}}' is disabled in this sandbox")
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _guarded_import

# --- candidate code below ---
{code}
"""


def _limit_resources():
    """Applied inside the child process (POSIX only) before exec."""
    cpu_seconds = settings.code_exec_timeout_seconds
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    mem_bytes = 256 * 1024 * 1024  # 256 MB
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))


def run_python_snippet(code: str) -> CodeExecResult:
    wrapped = _RUNNER_TEMPLATE.format(blocked=BLOCKED_IMPORTS, code=code)

    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "candidate_submission.py"
        script_path.write_text(wrapped)

        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=settings.code_exec_timeout_seconds,
                preexec_fn=_limit_resources if sys.platform != "win32" else None,
                cwd=tmp,
            )
            stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout or ""
            stderr = (e.stderr or "") + "\n[terminated: execution exceeded time limit]"
            exit_code = -1
        duration_ms = int((time.monotonic() - start) * 1000)

    return CodeExecResult(
        stdout=stdout[-4000:],
        stderr=stderr[-4000:],
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
    )
