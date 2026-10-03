"""
⚛️ AgentKthx — MCP stdio transport

JSON-RPC 2.0 over stdio for MCP server subprocesses.

Wire format
-----------
Each direction is a sequence of newline-delimited JSON objects. We do NOT
implement the HTTP/SSE transport (single stdio only for v0.1).

Lifecycle
---------
* The transport spawns the subprocess on first use (lazy).
* stdin/stdout are pipes; stderr is captured to a small ring buffer for
  diagnostics ( surfaced via :attr:`StdioTransport.stderr_tail`).
* On close, we send the MCP ``shutdown`` notification, then ``exit``,
  then ``terminate`` if the process hasn't exited within 2 seconds.

Concurrency
-----------
A single transport is single-threaded by design. Concurrent tool calls to
the same MCP server are serialized via a threading.Lock inside
:meth:`StdioTransport.request`. This is correct (JSON-RPC over a single
stdio pair is inherently serial) but means a slow MCP server blocks all
other tools on that server.

If you need parallel tool calls across servers, use multiple
:class:`StdioTransport` instances (one per server) — :class:`MCPManager`
does this automatically.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from typing import Any

from .config import MCPServerConfig


class MCPTransportError(RuntimeError):
    """Raised when the stdio transport cannot send/receive a message."""


class StdioTransport:
    """Stdio JSON-RPC 2.0 transport for one MCP server subprocess.

    Not safe to share across threads for *concurrent* calls — use the
    internal lock, which :meth:`request` acquires automatically. Multiple
    transports may be used in parallel from different threads.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stderr_ring: deque[str] = deque(maxlen=64)
        self._stderr_thread: threading.Thread | None = None
        self._next_id = 1
        self._closed = False

    # ---- lifecycle ----

    def start(self) -> None:
        """Spawn the subprocess if not already running.

        Raises:
            MCPTransportError: If the subprocess cannot be started.
        """
        if self._proc is not None and self._proc.poll() is None:
            return  # already running
        if self._closed:
            raise MCPTransportError(
                f"MCP server '{self._config.name}': transport closed; "
                f"create a new instance to reconnect"
            )

        try:
            argv0, argv_rest, env_overrides = self._config.to_launch_args()
        except Exception as e:
            raise MCPTransportError(
                f"MCP server '{self._config.name}': cannot resolve command: {e}"
            ) from e

        # Inherit the current env, then apply overrides
        env = dict(os.environ)
        env.update(env_overrides)

        try:
            self._proc = subprocess.Popen(
                [argv0, *argv_rest],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                close_fds=True,
                # No shell=True — we never want the shell in the path
                shell=False,
                # Use text mode for stdin/stdout (JSON is UTF-8)
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,  # line-buffered
            )
        except OSError as e:
            raise MCPTransportError(
                f"MCP server '{self._config.name}': failed to spawn " f"'{argv0}': {e}"
            ) from e

        # Drain stderr in a background thread to avoid deadlock when the
        # subprocess writes a lot of diagnostic output.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name=f"mcp-stderr-{self._config.name}",
            daemon=True,
        )
        self._stderr_thread.start()

    def close(self, timeout: float = 2.0) -> None:
        """Send shutdown, then terminate the subprocess if needed.

        Safe to call multiple times.
        """
        if self._closed:
            return
        self._closed = True

        if self._proc is None:
            return

        if self._proc.poll() is None:
            # Best-effort MCP shutdown notification (no response expected)
            try:
                self._send_raw({"jsonrpc": "2.0", "method": "shutdown", "id": self._next_id})
                self._send_raw({"jsonrpc": "2.0", "method": "exit"})
            except Exception:
                pass  # we're tearing down anyway

            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=timeout)

        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
            self._proc.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass
        self._proc = None

    # ---- JSON-RPC ----

    def request(
        self, method: str, params: dict | list | None = None, timeout: float | None = None
    ) -> Any:
        """Send a JSON-RPC request and wait for the response.

        Args:
            method: JSON-RPC method name (e.g. ``"tools/list"``).
            params: Params (positional or named). Sent as ``params``.
            timeout: Per-call timeout in seconds. If None, uses the
                server config's ``timeout_seconds``.

        Returns:
            The ``result`` field of the JSON-RPC response.

        Raises:
            MCPTransportError: On transport failure, timeout, or
                JSON-RPC error response.
        """
        if timeout is None:
            timeout = float(self._config.timeout_seconds)

        with self._lock:
            self.start()
            msg_id = self._next_id
            self._next_id += 1

            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": method,
            }
            if params is not None:
                payload["params"] = params

            self._send_raw(payload)
            return self._read_response(msg_id, timeout)

    def notify(self, method: str, params: dict | list | None = None) -> None:
        """Send a JSON-RPC notification (no response expected).

        Acquires the lock so it doesn't interleave with a concurrent
        request on the same transport.
        """
        with self._lock:
            payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            self._send_raw(payload)

    # ---- internals ----

    def _send_raw(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPTransportError(f"MCP server '{self._config.name}': subprocess not running")
        try:
            data = json.dumps(obj) + "\n"
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPTransportError(f"MCP server '{self._config.name}': write failed: {e}") from e

    def _read_response(self, expected_id: int, timeout: float) -> Any:
        if self._proc is None or self._proc.stdout is None:
            raise MCPTransportError(f"MCP server '{self._config.name}': subprocess not running")

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTransportError(
                    f"MCP server '{self._config.name}': timeout waiting for "
                    f"response to id={expected_id}"
                )

            # We can't trivially timeout on a blocking readline; use a
            # separate thread + queue in production. For the scaffold, we
            # rely on the subprocess producing output within the timeout.
            # This is a known limitation — see TODO below.
            line = self._proc.stdout.readline()
            if not line:
                raise MCPTransportError(
                    f"MCP server '{self._config.name}': subprocess closed "
                    f"stdout while waiting for id={expected_id}"
                )

            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines (servers sometimes print diagnostics)
                # but record them for debugging.
                self._stderr_ring.append(f"BAD-JSON: {line[:200]}")
                continue

            if msg.get("jsonrpc") != "2.0":
                self._stderr_ring.append(f"BAD-MSG: {line[:200]}")
                continue

            # Skip notifications (no id) — we don't expect any but tolerate
            if "id" not in msg:
                self._stderr_ring.append(f"NOTIFICATION: {line[:200]}")
                continue

            if msg["id"] != expected_id:
                # Out-of-order or response to a previous (timed-out) request
                self._stderr_ring.append(f"UNEXPECTED-ID: got {msg['id']}, want {expected_id}")
                continue

            if "error" in msg:
                err = msg["error"]
                raise MCPTransportError(
                    f"MCP server '{self._config.name}': JSON-RPC error on "
                    f"{expected_id}: {err.get('code')} {err.get('message')}"
                )

            return msg.get("result")

    def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            for line in self._proc.stderr:
                self._stderr_ring.append(line.rstrip())
        except Exception:
            pass  # daemon thread; never propagate

    # ---- diagnostics ----

    @property
    def stderr_tail(self) -> list[str]:
        """Return a copy of the last ~64 stderr lines from the subprocess."""
        return list(self._stderr_ring)

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # TODO(v0.2): Replace the blocking readline() in _read_response with a
    # thread+queue pattern so that per-call timeouts actually work. Today,
    # a hung MCP server will block the calling thread for the full timeout
    # window *and* cannot be interrupted by Ctrl+C cleanly. This is the
    # same shape as ROB-06 (streaming generator close on Windows) —
    # document as MCP-01 in the audit register when this lands.
