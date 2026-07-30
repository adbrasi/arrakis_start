#!/usr/bin/env python3
"""
Process Manager - ComfyUI lifecycle management
Start, stop, restart ComfyUI with configurable flags
"""

import os
import signal
import subprocess
import logging
import time
import requests
from pathlib import Path
from typing import List, Optional, Dict, Tuple
import psutil

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read a positive int env var, falling back to default when unusable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (ValueError, TypeError):
        logger.warning(f"Invalid integer for env var {name}={raw!r}, using default {default}")
        return default
    if value <= 0:
        logger.warning(f"Non-positive value for env var {name}={raw!r}, using default {default}")
        return default
    return value


# Paths
COMFY_BASE = Path(os.environ.get('COMFY_BASE', '/workspace/comfy'))
COMFY_DIR = COMFY_BASE / 'ComfyUI'
VENV_DIR = COMFY_BASE / '.venv'
COMFY_CLI = os.environ.get('COMFY_CLI', str(VENV_DIR / 'bin' / 'comfy'))
COMFY_STARTUP_TIMEOUT = _env_int('COMFY_STARTUP_TIMEOUT', 120)
# Single source of truth for the ComfyUI port in this module. start.py reads the
# same variable for its own launch path, so both agree: health checks probe the
# port ComfyUI was actually started on and the UI cannot spawn a second instance
# on a different port (double VRAM -> OOM).
COMFY_PORT = _env_int('COMFY_PORT', 8818)
# Per-probe budget while waiting for startup. Short on purpose: a bound but
# unresponsive port (or dropped packets) must not stretch the
# COMFY_STARTUP_TIMEOUT wall-clock budget.
STARTUP_PROBE_TIMEOUT = 2


class ProcessManager:
    """Manages ComfyUI process lifecycle"""

    def __init__(self, state_manager):
        self.state_manager = state_manager
        self.process = None
        # Identity of the process this instance launched: (pid, create_time).
        # A PID alone is not an identity — it can be recycled — so the start
        # time is kept alongside it and compared before any kill.
        self._tracked_identity: Optional[Tuple[int, float]] = None

    @staticmethod
    def _resolve_port(port: Optional[int] = None) -> int:
        """Resolve the ComfyUI port: explicit argument wins, else COMFY_PORT."""
        if port:
            return int(port)
        return COMFY_PORT

    @staticmethod
    def _process_create_time(pid: Optional[int]) -> Optional[float]:
        """Start time of a PID, used as the second half of its identity."""
        if not pid:
            return None
        try:
            return psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def _record_tracked_identity(self, pid: Optional[int]) -> None:
        """Remember (pid, create_time) for the ComfyUI process we own."""
        create_time = self._process_create_time(pid)
        if pid and create_time is not None:
            self._tracked_identity = (int(pid), create_time)
        else:
            self._tracked_identity = None

    def _pid_is_alive(self, pid: Optional[int]) -> bool:
        """Check if PID exists and is not a zombie."""
        if not pid:
            return False
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _find_port_owner_pid(self, port: int) -> Optional[int]:
        """Return PID that owns a listening socket on the target port."""
        try:
            for conn in psutil.net_connections(kind='inet'):
                if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                    return conn.pid
        except Exception as e:
            logger.warning(f"Could not inspect port owner for {port}: {e}")
        return None

    @staticmethod
    def _cmdline_is_comfy_server(cmdline: List[str]) -> bool:
        """
        True only for a ComfyUI *server* command line.

        Deliberately strict: bootstrap runs git/pip/apt inside COMFY_DIR, so a
        plain substring match on the workspace path is not an identity — it
        would authorise killing unrelated processes whose PID happens to match
        stale state.
        """
        if not cmdline:
            return False
        tokens = [str(t).lower() for t in cmdline]
        joined = ' '.join(tokens)
        basenames = {os.path.basename(t) for t in tokens}
        # comfy-cli wrapper: "<python> .../bin/comfy --workspace <dir> launch -- ..."
        if 'launch' in tokens and any(b.startswith('comfy') for b in basenames):
            return True
        # ComfyUI itself: "<python> .../ComfyUI/main.py --port <n> ..."
        if 'main.py' in basenames and ('comfyui' in joined or '--port' in tokens):
            return True
        return False

    def _is_comfy_process(self, pid: Optional[int]) -> bool:
        """Identity check: does this PID belong to a ComfyUI server we may stop?"""
        if not pid or int(pid) == os.getpid():
            return False
        try:
            return self._cmdline_is_comfy_server(psutil.Process(pid).cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _tracked_pid_is_stoppable(self, pid: Optional[int]) -> bool:
        """
        Decide whether the PID persisted in state may be killed.

        state.json lives on the persistent volume, so the recorded PID survives
        container restarts: on a fresh boot it is stale and PIDs climb past it
        while bootstrap runs apt/pip/git. Existence is therefore not identity —
        the PID must not be us, must still look like a ComfyUI server, and must
        keep the start time we recorded when we launched it.
        """
        if not pid:
            return False
        if int(pid) == os.getpid():
            logger.warning(
                f"Tracked PID {pid} is this orchestrator itself (stale state); not killing it"
            )
            return False
        if not self._pid_is_alive(pid):
            return False
        tracked = self._tracked_identity
        if tracked and tracked[0] == int(pid) and self._process_create_time(pid) != tracked[1]:
            logger.warning(
                f"Tracked PID {pid} was recycled by another process "
                f"(start time changed); not killing it"
            )
            return False
        if not self._is_comfy_process(pid):
            logger.warning(
                f"Tracked PID {pid} is not a ComfyUI process (stale state or recycled PID); "
                "refusing to kill automatically"
            )
            return False
        return True

    def _kill_process_group(self, pid: int, sig: int) -> bool:
        """
        Signal the whole process group led by `pid`.

        Only a group this PID leads is touched, and never our own group, so a
        stray PID can never take the orchestrator down with it.
        """
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            return False
        if pgid != pid or pgid == os.getpgid(0):
            return False
        try:
            os.killpg(pgid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError) as e:
            logger.debug(f"killpg({pgid}, {sig}) failed: {e}")
            return False

    def _reap_child(self, timeout: Optional[float] = None) -> None:
        """
        Reap the comfy-cli child we launched so it cannot linger as a zombie.

        Non-blocking by default: only an already-exited child is collected.
        """
        proc = self.process
        if proc is None:
            return
        try:
            if timeout is None:
                if proc.poll() is None:
                    return
                proc.wait(timeout=1)
            else:
                proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return
        except Exception as e:
            logger.debug(f"Reaping comfy-cli child failed: {e}")
        self.process = None

    def _terminate_tree(self, pid: int, timeout: int = 10) -> bool:
        """
        Stop `pid` and every descendant.

        ComfyUI runs as a grandchild of the comfy-cli wrapper and may hold VRAM
        without owning the listening socket, so stopping a single PID is not
        enough: the whole tree (and the process group, for anything that
        reparented) has to go.
        """
        try:
            root = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            logger.info(f"PID {pid} is no longer running")
            return True

        try:
            cmdline = ' '.join(root.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            cmdline = ''
        logger.info(f"Stopping process tree of PID {pid}: {cmdline}")

        try:
            targets = [root] + root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            targets = [root]

        self._kill_process_group(pid, signal.SIGTERM)
        for proc in targets:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        _, alive = psutil.wait_procs(targets, timeout=timeout)
        if alive:
            logger.warning(
                f"{len(alive)} process(es) in tree of PID {pid} did not stop in {timeout}s, "
                "forcing kill..."
            )
            self._kill_process_group(pid, signal.SIGKILL)
            for proc in alive:
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            _, alive = psutil.wait_procs(alive, timeout=5)

        if self.process is not None and self.process.pid == pid:
            self._reap_child(timeout=5)
        if self._tracked_identity and self._tracked_identity[0] == pid:
            self._tracked_identity = None

        if alive:
            logger.error(
                f"Failed to stop PID(s) {[p.pid for p in alive]} in tree of {pid}"
            )
            return False
        logger.info(f"✓ Process tree of PID {pid} stopped")
        return True

    def _try_comfy_stop(self, timeout: int = 12) -> bool:
        """Try stopping ComfyUI through comfy-cli before PID-level fallback."""
        commands = [
            [COMFY_CLI, '--workspace', str(COMFY_DIR), 'stop'],
            [COMFY_CLI, 'stop'],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(COMFY_DIR),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                if result.returncode == 0:
                    logger.info(f"comfy stop succeeded: {' '.join(cmd)}")
                    return True
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()
                detail = stderr or stdout
                if detail:
                    logger.info(
                        f"comfy stop returned {result.returncode}: {detail.splitlines()[-1]}"
                    )
            except FileNotFoundError:
                logger.warning(f"comfy binary not found: {COMFY_CLI}")
                return False
            except subprocess.TimeoutExpired:
                logger.warning(f"comfy stop timed out: {' '.join(cmd)}")
            except Exception as e:
                logger.warning(f"comfy stop failed ({' '.join(cmd)}): {e}")
        return False
    
    def is_running(self, port: Optional[int] = None) -> bool:
        """Check if ComfyUI is running"""
        port = self._resolve_port(port)
        self._reap_child()
        status = self.state_manager.get_comfyui_status()
        pid = status.get('pid')

        # A live PID only counts when it is really ComfyUI: state.json survives
        # container restarts, so the recorded PID may now belong to anything.
        if self._pid_is_alive(pid) and self._is_comfy_process(pid):
            return True

        # Fallback for stale/missing PID: if health endpoint responds, ComfyUI is
        # alive. This also promotes a "starting"/"error" record to "running" once
        # a slow launch finally answers, so no state can wedge the UI.
        if self.health_check(port=port, timeout=STARTUP_PROBE_TIMEOUT):
            owner_pid = self._find_port_owner_pid(port)
            if owner_pid != pid or status.get('status') != 'running':
                logger.info(
                    f"ComfyUI responds on port {port}; syncing state "
                    f"(status={status.get('status')}, tracked PID={pid}, owner PID={owner_pid})"
                )
            self._record_tracked_identity(owner_pid)
            self.state_manager.set_comfyui_status(
                status="running",
                pid=owner_pid,
                port=port
            )
            return True

        return False

    def health_check(self, port: Optional[int] = None, timeout: float = 5) -> bool:
        """Check if ComfyUI is responding"""
        port = self._resolve_port(port)
        try:
            response = requests.get(
                f"http://localhost:{port}/system_stats",
                timeout=timeout
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False
    
    def start(self, flags: Optional[List[str]] = None, port: Optional[int] = None) -> bool:
        """Start ComfyUI with optional flags + preset-specific flags"""
        port = self._resolve_port(port)
        if self.is_running(port=port):
            status = self.state_manager.get_comfyui_status()
            running_pid = status.get('pid')
            running_port = status.get('port') or port
            running_flags = status.get('flags') or []
            running_cmdline = ''
            try:
                if running_pid:
                    running_cmdline = ' '.join(psutil.Process(running_pid).cmdline())
            except Exception:
                pass
            logger.warning(
                f"ComfyUI is already running (pid={running_pid}, port={running_port}); "
                f"refusing to launch a second instance. Use restart() to apply new flags."
            )
            if running_flags:
                logger.warning(f"  → current flags: {running_flags}")
            if running_cmdline:
                logger.warning(f"  → cmdline: {running_cmdline}")
            return False

        if self._is_port_in_use(port):
            owner_pid = self._find_port_owner_pid(port)
            logger.error(
                f"Cannot start ComfyUI: port {port} is already in use "
                f"(owner PID: {owner_pid})"
            )
            self.state_manager.set_comfyui_status(status="error", port=port)
            return False

        # Guard against a CUDA 13 torch wheel on a CUDA 12.8-only driver. This
        # covers the "Start" button path where the venv may have been left with
        # an incompatible torch by a previous run (no install step in between).
        # Deferred import keeps process_manager free of a circular dependency on
        # start.py (which imports this module at load time).
        try:
            from start import _ensure_torch_driver_compatible
            _ensure_torch_driver_compatible()
        except Exception as exc:
            logger.warning(f"torch driver-compat guard skipped: {exc}")

        # Default flags
        default_flags = [
            '--listen', '0.0.0.0',
            '--port', str(port),
            '--preview-method', 'latent2rgb'
        ]
        
        # Get preset-specific flags from state
        preset_flags = self.state_manager.get_comfyui_flags()
        if preset_flags:
            logger.info(f"Adding preset-specific flags: {preset_flags}")
        
        # Merge: defaults + preset flags + explicit flags (last wins)
        all_flags = default_flags.copy()
        all_flags.extend(preset_flags)
        if flags:
            all_flags.extend(flags)
        
        # Deduplicate while preserving order (later values override).
        # Flags starting with '--' followed by a non-'--' token form a pair.
        seen: Dict[str, int] = {}
        deduped: List[str] = []
        i = 0
        while i < len(all_flags):
            token = all_flags[i]
            if token.startswith('--'):
                # Check if next token is a value (not another flag)
                has_value = (i + 1 < len(all_flags) and not all_flags[i + 1].startswith('--'))
                if token in seen:
                    # Remove previous occurrence (and its value if any)
                    prev = seen[token]
                    prev_has_value = (prev + 1 < len(deduped) and not deduped[prev + 1].startswith('--'))
                    if prev_has_value:
                        del deduped[prev:prev + 2]
                    else:
                        del deduped[prev:prev + 1]
                    # Recompute indices for remaining entries
                    seen.clear()
                    for j, t in enumerate(deduped):
                        if t.startswith('--'):
                            seen[t] = j
                seen[token] = len(deduped)
                deduped.append(token)
                if has_value:
                    deduped.append(all_flags[i + 1])
                    i += 2
                else:
                    i += 1
            else:
                deduped.append(token)
                i += 1
        flags = deduped
        
        logger.info(f"Starting ComfyUI on port {port} with flags: {flags}")
        
        try:
            # Build command
            cmd = [
                COMFY_CLI,
                '--workspace', str(COMFY_DIR),
                'launch',
                '--'
            ] + flags

            # Ensure runtime env for Blackwell + SageAttention stability.
            env = os.environ.copy()

            # Force comfy-cli launch to use the workspace venv Python.
            # Without this, cloud templates (VastAI/Runpod) may have /venv/main
            # on PATH, causing comfy-cli to spawn ComfyUI with the wrong Python.
            # This ensures pip-installed deps (cv2, gguf, pywt, etc.) are visible
            # at runtime and the correct PyTorch build (stable cu128) is used.
            venv_bin = str(VENV_DIR / 'bin')
            env['VIRTUAL_ENV'] = str(VENV_DIR)
            env['PATH'] = f"{venv_bin}:{env.get('PATH', '')}"

            env.setdefault('NVCC_APPEND_FLAGS', '--threads 8')
            # PyTorch 2.9+ renamed PYTORCH_CUDA_ALLOC_CONF to PYTORCH_ALLOC_CONF.
            # Set both so old and new torch builds work without warnings.
            alloc_conf = env.get('PYTORCH_ALLOC_CONF') or env.get('PYTORCH_CUDA_ALLOC_CONF') or 'expandable_segments:True'
            env['PYTORCH_ALLOC_CONF'] = alloc_conf
            env['PYTORCH_CUDA_ALLOC_CONF'] = alloc_conf
            env.setdefault('MAX_JOBS', '32')
            logger.info(
                "ComfyUI env: "
                f"NVCC_APPEND_FLAGS={env.get('NVCC_APPEND_FLAGS')} "
                f"PYTORCH_ALLOC_CONF={env.get('PYTORCH_ALLOC_CONF')} "
                f"MAX_JOBS={env.get('MAX_JOBS')}"
            )
            
            # Start process WITHOUT capturing output - logs go directly to terminal
            # This allows real-time log viewing and prevents Python buffering issues.
            # start_new_session puts comfy-cli and its ComfyUI grandchild in a
            # dedicated process group, so stop() can take the whole tree down.
            self.process = subprocess.Popen(
                cmd,
                cwd=str(COMFY_DIR),
                env=env,
                start_new_session=True
                # No stdout/stderr capture - ComfyUI logs appear in real-time
            )
            child_pid = self.process.pid
            self._record_tracked_identity(child_pid)

            # Update state
            self.state_manager.set_comfyui_status(
                status="starting",
                pid=child_pid,
                flags=flags,
                port=port
            )

            # Wait for startup (check health and early process crash) against a
            # single wall-clock deadline: COMFY_STARTUP_TIMEOUT is a seconds
            # budget, not an iteration count.
            logger.info(f"Waiting for ComfyUI to start (timeout: {COMFY_STARTUP_TIMEOUT}s)...")
            deadline = time.monotonic() + COMFY_STARTUP_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                exit_code = self.process.poll()
                if exit_code is not None:
                    logger.error(f"ComfyUI process exited before startup (exit code: {exit_code})")
                    self._reap_child(timeout=5)
                    self._tracked_identity = None
                    self.state_manager.set_comfyui_status(
                        status="error",
                        port=port,
                        clear_pid=True
                    )
                    return False
                if self.health_check(port=port, timeout=min(STARTUP_PROBE_TIMEOUT, remaining)):
                    self.state_manager.set_comfyui_status(
                        status="running",
                        pid=child_pid,
                        flags=flags,
                        port=port
                    )
                    # Colorful success banner
                    print("\n" + "="*60)
                    print("\033[1;32m🚀 COMFYUI LIGADO! PRONTO PARA USO! 🚀\033[0m")
                    print("\033[1;36m   Entre no portal do VastAI e selecione ComfyUI!\033[0m")
                    print("="*60 + "\n")
                    logger.info(f"✓ ComfyUI started successfully on port {port}")
                    return True
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0)))

            # Timeout: the launch did not answer inside the budget, but it is
            # still alive (poll() above would have caught an exit). Record what
            # is actually true — "starting", with the real PID — instead of
            # "error" plus an orphaned process: is_running() promotes it to
            # "running" as soon as it answers, and any later stop/restart/install
            # takes the whole tree down. Writing "error" while keeping the PID is
            # what used to wedge the Start button permanently.
            logger.error(
                f"ComfyUI did not answer on port {port} within {COMFY_STARTUP_TIMEOUT}s "
                f"(PID {child_pid} still starting). Raise COMFY_STARTUP_TIMEOUT if the "
                "first launch is legitimately slower, or use Restart to stop it."
            )
            self.state_manager.set_comfyui_status(
                status="starting",
                pid=child_pid,
                flags=flags,
                port=port
            )
            return False

        except Exception as e:
            logger.error(f"Failed to start ComfyUI: {e}")
            self.state_manager.set_comfyui_status(status="error", port=port)
            return False


    def ensure_stopped(self, port: Optional[int] = None, timeout: int = 10) -> bool:
        """
        Ensure ComfyUI is stopped by tracked PID and by port ownership.
        This handles stale state where PID no longer matches the process on port.
        """
        port = self._resolve_port(port)
        self._reap_child()
        status = self.state_manager.get_comfyui_status()
        tracked_pid = status.get('pid')
        stopped_any = False
        ok = True

        logger.info("Trying comfy-cli stop before PID fallback...")
        if self._try_comfy_stop():
            stopped_any = True
            time.sleep(1)

        # The tracked PID is only killed when its identity still checks out —
        # same rule the port-owner path below applies.
        if self._tracked_pid_is_stoppable(tracked_pid):
            logger.info(f"Stopping tracked ComfyUI PID: {tracked_pid}")
            ok = self._terminate_tree(tracked_pid, timeout=timeout) and ok
            stopped_any = True

        owner_pid = self._find_port_owner_pid(port)
        if owner_pid and owner_pid != tracked_pid:
            if self._is_comfy_process(owner_pid):
                logger.warning(
                    f"Found ComfyUI-like process on port {port} with PID {owner_pid} "
                    "not tracked in state; stopping it."
                )
                ok = self._terminate_tree(owner_pid, timeout=timeout) and ok
                stopped_any = True
            else:
                logger.error(
                    f"Port {port} is owned by non-Comfy process PID {owner_pid}; "
                    "refusing to kill automatically."
                )
                ok = False

        logger.info(f"Waiting for port {port} to be released...")
        released = False
        deadline = time.monotonic() + max(timeout, 1)
        while time.monotonic() < deadline:
            owner_pid = self._find_port_owner_pid(port)
            if owner_pid is None:
                logger.info(f"✓ Port {port} released")
                released = True
                break

            if self._is_comfy_process(owner_pid):
                logger.warning(
                    f"Port {port} still owned by ComfyUI-like PID {owner_pid}; forcing stop."
                )
                ok = self._terminate_tree(owner_pid, timeout=5) and ok
                stopped_any = True
            else:
                logger.warning(
                    f"Port {port} still owned by non-Comfy PID {owner_pid}; waiting."
                )
            time.sleep(1)

        if not released:
            owner_pid = self._find_port_owner_pid(port)
            if owner_pid is None:
                logger.info(f"✓ Port {port} released")
            else:
                logger.error(
                    f"Port {port} is still in use after stop attempts "
                    f"(owner PID: {owner_pid})"
                )
                ok = False

        # The banner claims success, so it needs both halves: something was
        # actually stopped AND every stop attempt worked.
        if not stopped_any:
            logger.info("No running ComfyUI process detected during stop check")
        elif ok:
            print("\n" + "="*60)
            print("\033[1;31m⏹ COMFYUI DESLIGADO! ⏹\033[0m")
            print("="*60 + "\n")

        if ok:
            self._tracked_identity = None
            self.state_manager.set_comfyui_status(
                status="stopped",
                pid=None,
                port=port,
                clear_pid=True
            )
        else:
            # Keep the record honest: only retain a PID that is genuinely still
            # a live ComfyUI, otherwise the next run inherits a stale PID again.
            tracked_still_running = self._is_comfy_process(tracked_pid) and self._pid_is_alive(tracked_pid)
            if not tracked_still_running:
                self._tracked_identity = None
            self.state_manager.set_comfyui_status(
                status="error",
                port=port,
                clear_pid=not tracked_still_running,
            )
        return ok

    def stop(self, timeout: int = 10, port: Optional[int] = None) -> bool:
        """Stop ComfyUI and ensure port release."""
        return self.ensure_stopped(port=port, timeout=timeout)


    def _is_port_in_use(self, port: int) -> bool:
        """Check if a listening process owns this port."""
        return self._find_port_owner_pid(port) is not None
    
    def restart(self, flags: Optional[List[str]] = None, port: Optional[int] = None) -> bool:
        """Restart ComfyUI with optional new flags"""
        logger.info("Restarting ComfyUI...")

        port = self._resolve_port(port)

        # Stop if running
        if self.is_running(port=port):
            if not self.stop(port=port):
                logger.error("Failed to stop ComfyUI for restart")
                return False

        # Wait a bit
        time.sleep(2)

        # Start with new flags
        return self.start(flags=flags, port=port)
    
    def get_logs(self, lines: int = 100) -> List[str]:
        """Get recent ComfyUI logs"""
        # TODO: Implement log reading from stdout capture
        return []


# Global instance
_process_manager = None

def get_process_manager(state_manager):
    """Get global process manager instance"""
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager(state_manager)
    return _process_manager
