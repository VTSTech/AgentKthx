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

R07.24 (MCP-01): per-call timeouts actually interrupt now
---------------------------------------------------------
The original ``_read_response`` called ``self._proc.stdout.readline()``
directly, which is a blocking stdlib call with no timeout parameter. The
per-call ``timeout`` was honored only via the loop's deadline check,
which never got a chance to fire if the subprocess produced no output.
R07.24 replaced this with a thread+queue pattern: a daemon thread does
the blocking ``readline()`` and pushes the result to a
:class:`queue.Queue`; the main thread does ``queue.get(timeout=remaining)``.
On timeout, the transport is marked as **poisoned** (subsequent calls
raise immediately) and the daemon thread dies naturally when the subprocess
closes. Bonus: this also fixes Ctrl+C interruptibility on POSIX — the
main thread no longer holds the GIL inside ``readline()``.
"""

from __future__ import annotations

import json
import os
import queue
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
        # R07.24 (MCP-01): poisoned transports raise immediately on the
        # next call. Set when a per-call timeout fires during a blocking
        # readline() — the subprocess is in an unknown state and the
        # daemon reader thread is likely stuck. Recovery: close() and
        # create a new StdioTransport instance.
        self._poisoned = False
        # R07.24 (MCP-05): optional callback invoked when the transport
        # reads a JSON-RPC notification (a message with no ``id`` field).
        # The callback receives the parsed message dict. Set by the
        # owning MCPClient via set_notification_callback(). If None,
        # notifications are logged to the stderr ring buffer (the
        # original R07.22 behavior).
        self._notification_callback: "callable | None" = None

    def set_notification_callback(self, callback: "callable | None") -> None:
        """R07.24 (MCP-05): register a callback for JSON-RPC notifications.

        When the transport reads a message with no ``id`` field (a
        notification per JSON-RPC 2.0), the callback is invoked with the
        parsed message dict. Pass ``None`` to revert to the default
        behavior (log to stderr ring buffer).

        The callback runs in the transport's reader-thread context —
        keep it fast and non-blocking. The owning MCPClient installs
        a trampoline that routes to per-method handlers it manages.
        """
        self._notification_callback = callback

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
        """Read one JSON-RPC response, with per-call timeout that actually interrupts.

        R07.24 (MCP-01): replaced the naive ``self._proc.stdout.readline()``
        loop with a thread+queue pattern. The blocking ``readline()`` runs on
        a daemon thread; the main thread does ``queue.get(timeout=remaining)``.
        On timeout, the transport is poisoned (subsequent calls raise
        immediately — the subprocess is in an unknown state and the daemon
        thread is likely stuck waiting for output that will never come).

        Notifications (no ``id`` field) are still tolerated and logged to
        the stderr ring buffer — this is also where MCP-05's
        ``notifications/tools/list_changed`` notification would land if a
        server pushed one mid-call. The MCP-05 handler in
        :class:`MCPClient` re-queries ``tools/list`` when it sees that
        notification; this transport only routes.
        """
        if self._proc is None or self._proc.stdout is None:
            raise MCPTransportError(f"MCP server '{self._config.name}': subprocess not running")

        if self._poisoned:
            raise MCPTransportError(
                f"MCP server '{self._config.name}': transport poisoned after a previous "
                f"timeout — close() and create a new instance to reconnect"
            )

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Mark poisoned — the daemon thread is likely still stuck
                # in readline(); future calls on this transport would hang.
                self._poisoned = True
                raise MCPTransportError(
                    f"MCP server '{self._config.name}': timeout waiting for "
                    f"response to id={expected_id} (transport poisoned — "
                    f"close() and reconnect to recover)"
                )

            # Thread+queue pattern: the daemon thread does the blocking
            # readline(); the main thread blocks on queue.get(timeout=…)
            # which DOES honor its deadline. On Empty, we loop + re-check
            # the deadline (handles partial-line servers that emit empty
            # lines between real responses).
            line_q: queue.Queue[str | None] = queue.Queue()
            reader = threading.Thread(
                target=self._blocking_readline,
                args=(line_q,),
                name=f"mcp-read-{self._config.name}-{expected_id}",
                daemon=True,
            )
            reader.start()
            try:
                line = line_q.get(timeout=remaining)
            except queue.Empty:
                # The daemon thread is still alive in readline() — it will
                # die naturally when the subprocess closes (close_fds=True
                # ensures the pipe is closed even if the main thread exits).
                # Mark poisoned so the next call fails fast.
                self._poisoned = True
                raise MCPTransportError(
                    f"MCP server '{self._config.name}': timeout waiting for "
                    f"response to id={expected_id} (transport poisoned — "
                    f"close() and reconnect to recover)"
                )

            if line is None:
                # The subprocess closed stdout (EOF) — no more output coming.
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

            # Route notifications (no id) — R07.24 (MCP-05): if a callback
            # is registered, route to it (typically the owning MCPClient's
            # handle_notification trampoline, which dispatches to per-method
            # handlers like notifications/tools/list_changed). Otherwise log
            # to the stderr ring buffer (the original R07.22 behavior).
            if "id" not in msg:
                if self._notification_callback is not None:
                    try:
                        self._notification_callback(msg)
                    except Exception:
                        # Buggy callback — log + continue. The transport
                        # must not die from a handler bug.
                        self._stderr_ring.append(f"HANDLER-ERR: {line[:200]}")
                else:
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

    def _blocking_readline(self, out_q: queue.Queue[str | None]) -> None:
        """Daemon-thread target: do one blocking ``readline()`` on stdout.

        Pushes the line (str) on success, or None if stdout hit EOF (the
        subprocess closed). Never raises — exceptions are swallowed and
        reported as None so the main thread's queue.get() deadline fires
        rather than the daemon thread crashing silently.

        This is the function that previously ran inline in the main thread.
        Moving it to a daemon thread is what makes per-call timeouts
        actually interrupt — :meth:`queue.Queue.get` honors its timeout
        parameter, ``readline()`` does not.
        """
        try:
            assert self._proc is not None and self._proc.stdout is not None
            line = self._proc.stdout.readline()
            out_q.put(line if line else None)
        except Exception:
            # Any IO error reading from a dead/EOF'd subprocess — surface
            # as None so the main thread's "subprocess closed stdout" branch
            # fires with the right diagnostic message.
            out_q.put(None)

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

    @property
    def is_poisoned(self) -> bool:
        """R07.24 (MCP-01): True if a per-call timeout fired and the transport
        is in an unknown state. Subsequent :meth:`request` calls raise
        immediately. Recovery: :meth:`close` and create a new instance.
        """
        return self._poisoned

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # R07.24 (MCP-01): the thread+queue pattern above replaces the original
    # blocking readline() loop. Per-call timeouts now actually interrupt —
    # a hung MCP server raises MCPTransportError within `timeout` seconds
    # rather than blocking the calling thread indefinitely. The transport
    # is poisoned on timeout so the next call fails fast; recovery requires
    # close() + new instance.
