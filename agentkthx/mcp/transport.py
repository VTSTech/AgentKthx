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

R07.35 (MCP-07): a per-call timeout is NOT connection-fatal
-----------------------------------------------------------
The MCP-01 poison-on-timeout design was too harsh: a SLOW tool (e.g.
``search_files`` globbing a large workspace on a slow VM disk) exceeded
its per-call timeout, the transport poisoned itself, and R07.35's
auto-reconnect then burned its rounds re-dispatching the SAME slow call
at the SAME timeout — guaranteed to fail again — while churning the
subprocess. Reported live by the owner: "auto-reconnect failed after
2 attempt(s)" on a server that was alive the whole time.

The read side is now a **persistent reader thread + id router**: one
daemon thread reads stdout forever and routes each JSON-RPC response
to the queue registered for its id (``_inflight``). A timed-out request
simply abandons its queue — the late response arrives later and is
dropped ("LATE-RESPONSE" in the stderr ring) — and the connection
stays usable. A per-call timeout now raises
:class:`MCPRequestTimeoutError` (a plain ``MCPTransportError``
subclass, NOT :class:`MCPTransportGone`): the manager surfaces a clean
"server alive, call too slow" message instead of reconnecting.

``_poisoned`` now marks only REAL death: EOF on stdout (subprocess
died), write failure (broken pipe), or an explicitly closed transport.
Fast-fail semantics are preserved (the next ``request()`` on a poisoned
transport raises immediately). The manager watches
:attr:`consecutive_timeouts` — repeated timeouts on an "alive" server
are treated as a WEDGED server and take the reconnect path.

Also fixed (latent MCP-05 deadlock): notifications are no longer
dispatched from the reader thread. The reader only QUEUES them;
:meth:`request` dispatches them on the caller thread outside the
transport lock. Previously a ``notifications/tools/list_changed``
arriving mid-call ran the manager's refresh handler inline, which
called ``list_tools()`` → ``request()`` → the NON-reentrant
``threading.Lock`` held by the in-flight call → permanent deadlock.

R07.35 (MCP-06): reconnectable vs protocol failures
---------------------------------------------------
Poisoning left the transport dead forever — every later call in the
session raised "transport poisoned — close() and reconnect" and the only
recovery was restarting the whole agent. R07.35 splits the failure
surface in two so callers can react automatically:

* :class:`MCPTransportGone` (subclass of :class:`MCPTransportError`) —
  the pipe/subprocess is in an unknown or dead state: poisoning, per-call
  timeout, EOF, write failure, transport closed. The transport instance
  must be discarded; :meth:`MCPClient.reconnect` rebuilds it.
* plain :class:`MCPTransportError` — the subprocess is alive and
  answered: JSON-RPC error responses, config/resolve failures. There is
  nothing to reconnect; the error is informational.

``except MCPTransportError`` handlers written before R07.35 keep working
unchanged — ``Gone`` is a subclass.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections import deque
from typing import Any

from .config import MCPServerConfig


class MCPTransportError(RuntimeError):
    """Raised when the stdio transport cannot send/receive a message."""


class MCPRequestTimeoutError(MCPTransportError):
    """R07.35 (MCP-07): one request exceeded its per-call timeout.

    The subprocess is ALIVE and the pipe is intact — the persistent
    reader thread keeps consuming and drops the late response when it
    eventually arrives. This is intentionally NOT
    :class:`MCPTransportGone`: tearing down a healthy connection because
    one call was slow is what made R07.35 churn (reconnect → same slow
    call → same timeout → fail) on the owner's session.

    Callers should surface a clean "server alive, call too slow" error
    (the manager does), optionally retrying with a longer timeout.
    Back-compat: still an ``MCPTransportError`` subclass, so
    ``except MCPTransportError`` handlers keep working.
    """


class MCPTransportGone(MCPTransportError):
    """R07.35 (MCP-06): connection-fatal transport failure.

    Raised at every site where the pipe/subprocess is in an unknown or
    dead state — poisoning (MCP-01), per-call timeout, EOF on stdout,
    write failure, closed transport. The transport instance must be
    discarded; :meth:`MCPClient.reconnect` builds a fresh one and
    redoes the handshake.

    Distinct from a plain :class:`MCPTransportError`, which the
    transport raises when the subprocess is ALIVE and answered (JSON-RPC
    error response) or when the failure is config-level (unresolvable
    command) — those are informational, not reconnect triggers.
    """


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
        # R07.24 (MCP-01) / R07.35 (MCP-07): poisoned transports raise
        # immediately on the next call. Since MCP-07, poisoning marks
        # only REAL death (EOF on stdout, write failure, closed) — a
        # per-call timeout no longer poisons (the persistent reader
        # thread keeps the pipe in sync and drops late responses).
        # Recovery: close() and create a new StdioTransport instance
        # (the manager does this automatically via MCPClient.reconnect).
        self._poisoned = False
        # R07.35 (MCP-07): persistent reader thread + id-routed responses.
        # One daemon thread owns stdout; each in-flight request registers
        # a queue keyed by its JSON-RPC id and waits on it. Late
        # responses (requester timed out) are dropped to the ring.
        self._reader_thread: threading.Thread | None = None
        self._inflight: dict[int, "queue.Queue[Any]"] = {}
        self._inflight_lock = threading.Lock()
        self._dead = False
        # Consecutive per-call timeouts with NO response in between.
        # Reset on any response (even a JSON-RPC error — the server is
        # clearly responsive). The manager treats >= 2 as a WEDGED
        # server (alive pipe, stuck event loop) and reconnects.
        self._consecutive_timeouts = 0
        # R07.35 (MCP-07): notifications are deferred — the reader thread
        # only queues them; request() dispatches them on the caller
        # thread OUTSIDE the transport lock (a handler that calls back
        # into request() — e.g. the manager's tools/list refresh — would
        # otherwise deadlock on the non-reentrant lock).
        self._pending_notifications: list[dict] = []
        self._pending_lock = threading.Lock()
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
            # R07.35 (MCP-06): gone, not a protocol error — the manager's
            # reconnect path builds a new transport and retries.
            raise MCPTransportGone(
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

        # R07.35 (MCP-07): the persistent reader owns stdout for the
        # lifetime of the subprocess and routes responses by id.
        self._start_reader()

    def _start_reader(self) -> None:
        """R07.35 (MCP-07): spawn the persistent stdout reader if not running."""
        if self._reader_thread is not None and self._reader_thread.is_alive():
            return
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"mcp-reader-{self._config.name}",
            daemon=True,
        )
        self._reader_thread.start()

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
            # Best-effort JSON-RPC goodbye while the pipe is still open.
            try:
                self._send_raw({"jsonrpc": "2.0", "method": "shutdown", "id": self._next_id})
                self._next_id += 1
                self._send_raw({"jsonrpc": "2.0", "method": "exit"})
            except Exception:
                pass  # we're tearing down anyway

            # R07.35 (MCP-07): close stdin BEFORE waiting. MCP stdio
            # servers exit when stdin hits EOF, so teardown takes
            # milliseconds instead of always burning the full wait()
            # window and falling through to SIGTERM.
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except Exception:
                pass

            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=timeout)

        # Closing stdout also unblocks the persistent reader (its
        # readline() returns EOF and it fails any remaining waiters).
        try:
            self._proc.stdout.close()  # type: ignore[union-attr]
            self._proc.stderr.close()  # type: ignore[union-attr]
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

        # R07.35 (MCP-07): dispatch notifications queued by earlier
        # traffic BEFORE taking the lock — a handler may call back into
        # request() (e.g. the manager's tools/list refresh on
        # notifications/tools/list_changed) and must never do so while
        # the non-reentrant lock is held.
        self._dispatch_pending_notifications()

        with self._lock:
            # R07.35 (MCP-07): fast-fail BEFORE start() so a poisoned or
            # closed transport doesn't spawn a subprocess just to raise.
            if self._closed:
                raise MCPTransportGone(
                    f"MCP server '{self._config.name}': transport closed; "
                    f"create a new instance to reconnect"
                )
            if self._poisoned:
                raise MCPTransportGone(
                    f"MCP server '{self._config.name}': transport poisoned after a "
                    f"previous fatal error — reconnect to recover"
                )

            self.start()
            # Defensive: if the reader thread died while the subprocess
            # is alive (should not happen), revive it — otherwise every
            # request would burn its full timeout window.
            if (
                self._reader_thread is None or not self._reader_thread.is_alive()
            ) and self._proc is not None and self._proc.poll() is None:
                self._start_reader()

            msg_id = self._next_id
            self._next_id += 1

            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": method,
            }
            if params is not None:
                payload["params"] = params

            try:
                self._send_raw(payload)
            except MCPTransportGone:
                # Broken pipe — the subprocess is definitively dead.
                self._poisoned = True
                raise

            result = self._read_response(msg_id, timeout)

        # Lock released. R07.35 (MCP-07): dispatch any notifications the
        # reader queued while we were waiting — still on the caller
        # thread, still outside the lock, so handlers can re-enter
        # request() safely (the MCP-05 deadlock fix).
        self._dispatch_pending_notifications()
        return result

    def notify(self, method: str, params: dict | list | None = None) -> None:
        """Send a JSON-RPC notification (no response expected).

        Acquires the lock so it doesn't interleave with a concurrent
        request on the same transport.
        """
        with self._lock:
            payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            try:
                self._send_raw(payload)
            except MCPTransportGone:
                # R07.35 (MCP-07): a broken pipe on notify is just as
                # fatal as on request — mark poisoned so the fast-fail
                # fires instead of respawning on the next request().
                self._poisoned = True
                raise

    # ---- internals ----

    def _send_raw(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            # R07.35 (MCP-06): gone — the pipe is unusable.
            raise MCPTransportGone(
                f"MCP server '{self._config.name}': subprocess not running"
            )
        try:
            data = json.dumps(obj) + "\n"
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            # R07.35 (MCP-06): a broken pipe means the subprocess died —
            # gone (reconnectable), not a protocol error.
            raise MCPTransportGone(
                f"MCP server '{self._config.name}': write failed: {e}"
            ) from e

    def _read_response(self, expected_id: int, timeout: float) -> Any:
        """Wait for the response to ``expected_id``, routed by the reader thread.

        R07.35 (MCP-07): the persistent reader thread owns stdout. This
        method registers a queue keyed by the request id, waits on it
        with the caller's deadline, and unregisters it on the way out.

        On expiry the request is **abandoned, not poisoned**: the reader
        keeps consuming stdout, the late response is dropped as a
        ``LATE-RESPONSE`` ring entry when it eventually arrives, and the
        connection stays usable for the next call. The raise is
        :class:`MCPRequestTimeoutError` — a plain (non-Gone)
        ``MCPTransportError`` — so the manager reports "server alive,
        call too slow" instead of churning a reconnect that re-runs the
        same slow call at the same timeout.

        Only a REAL death fails the connection while waiting: the reader
        hitting EOF (subprocess died) pushes a ``None`` sentinel into
        every registered queue, which raises :class:`MCPTransportGone`.
        """
        q: "queue.Queue[Any]" = queue.Queue()
        with self._inflight_lock:
            self._inflight[expected_id] = q

        if self._dead:
            # Reader already inactive (subprocess died earlier) — don't
            # make the caller burn its whole timeout window on a dead pipe.
            with self._inflight_lock:
                self._inflight.pop(expected_id, None)
            raise MCPTransportGone(
                f"MCP server '{self._config.name}': subprocess closed stdout "
                f"(reader inactive) — reconnect to recover"
            )

        remaining = float(timeout)
        if remaining <= 0:
            with self._inflight_lock:
                self._inflight.pop(expected_id, None)
            self._consecutive_timeouts += 1
            raise MCPRequestTimeoutError(
                f"MCP server '{self._config.name}': timeout waiting for "
                f"response to id={expected_id} (non-positive window)"
            )

        try:
            msg = q.get(timeout=remaining)
        except queue.Empty:
            # R07.35 (MCP-07): abandon the request — do NOT poison. The
            # persistent reader is still consuming; the late response
            # will be dropped as LATE-RESPONSE when it arrives.
            self._consecutive_timeouts += 1
            raise MCPRequestTimeoutError(
                f"MCP server '{self._config.name}': timeout waiting for "
                f"response to id={expected_id} after {timeout:.1f}s "
                f"(subprocess alive — connection kept open)"
            ) from None
        finally:
            with self._inflight_lock:
                self._inflight.pop(expected_id, None)

        if msg is None:
            # EOF sentinel from the reader — the subprocess died mid-call.
            self._poisoned = True
            raise MCPTransportGone(
                f"MCP server '{self._config.name}': subprocess closed "
                f"stdout while waiting for id={expected_id}"
            )

        # Any response (even a JSON-RPC error) proves the server is
        # processing requests — reset the wedged-detection counter.
        self._consecutive_timeouts = 0

        if "error" in msg:
            # Deliberately stays a PLAIN MCPTransportError — the
            # subprocess is alive and answered. A JSON-RPC error is
            # informational (bad method/params), not a connection
            # failure; reconnecting would be wrong.
            err = msg["error"]
            raise MCPTransportError(
                f"MCP server '{self._config.name}': JSON-RPC error on "
                f"{expected_id}: {err.get('code')} {err.get('message')}"
            )

        return msg.get("result")

    def _reader_loop(self) -> None:
        """R07.35 (MCP-07): persistent daemon — read stdout, route by id.

        For every line on the subprocess's stdout:

        * JSON-RPC **response** (has ``id``) → hand to the queue
          registered in ``_inflight`` for that id. Unknown/late ids (the
          requester timed out) are dropped to the stderr ring.
        * **Notification** (no ``id``) → appended to
          ``_pending_notifications``; dispatched later on a caller thread
          by :meth:`_dispatch_pending_notifications` (running handlers
          here could re-enter ``request()`` and deadlock on the
          non-reentrant transport lock).
        * Bad JSON / wrong envelope → stderr ring (servers sometimes
          print diagnostics to stdout).

        On EOF or an IO error the loop exits, marks the transport dead,
        and fails every in-flight waiter with the ``None`` sentinel
        (→ :class:`MCPTransportGone`). Never raises.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break  # EOF — subprocess closed stdout
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self._stderr_ring.append(f"BAD-JSON: {line[:200]}")
                    continue
                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                    self._stderr_ring.append(f"BAD-MSG: {line[:200]}")
                    continue
                if "id" not in msg:
                    with self._pending_lock:
                        self._pending_notifications.append(msg)
                    continue
                rid = msg["id"]
                with self._inflight_lock:
                    waiter = self._inflight.pop(rid, None)
                if waiter is None:
                    self._stderr_ring.append(
                        f"LATE-RESPONSE: id={rid} (requester timed out or unknown)"
                    )
                    continue
                waiter.put(msg)
        except Exception:
            pass  # IO error on a dead/closed subprocess — handled below
        finally:
            # EOF or reader crash: fail every waiter with the EOF sentinel.
            self._dead = True
            if not self._closed:
                self._poisoned = True
            with self._inflight_lock:
                waiters = list(self._inflight.values())
                self._inflight.clear()
            for w in waiters:
                w.put(None)

    def _dispatch_pending_notifications(self) -> None:
        """R07.35 (MCP-07): run queued notification handlers on THIS thread.

        Called by :meth:`request` outside the transport lock (before
        acquiring it and again after the response arrives). Handlers are
        the owning MCPClient's trampoline → per-method handlers (e.g.
        the manager's tools/list refresh) — those may call back into
        ``request()``, which is only safe because the lock is NOT held
        here. Exceptions are swallowed (handler bug ≠ transport fault).
        """
        while True:
            with self._pending_lock:
                if not self._pending_notifications:
                    return
                msg = self._pending_notifications.pop(0)
            if self._notification_callback is not None:
                try:
                    self._notification_callback(msg)
                except Exception:
                    self._stderr_ring.append(
                        f"HANDLER-ERR: {str(msg.get('method', '?'))[:180]}"
                    )

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
        """R07.24 (MCP-01): True if the transport suffered a REAL fatal
        failure (EOF, write failure, closed) and subsequent
        :meth:`request` calls raise immediately.

        R07.35 (MCP-07): a per-call timeout does NOT poison anymore —
        the persistent reader keeps the pipe in sync and the connection
        stays usable. :meth:`close` + a new instance remains the manual
        escape hatch; the manager's auto-reconnect path rebuilds
        poisoned transports via MCPClient.reconnect() automatically.
        """
        return self._poisoned

    @property
    def consecutive_timeouts(self) -> int:
        """R07.35 (MCP-07): consecutive per-call timeouts with no response
        in between (reset on ANY response, including JSON-RPC errors).

        The manager uses this to distinguish "one slow call" (keep the
        connection) from a WEDGED server (pipe alive, event loop stuck —
        every call times out) and reconnects once the count reaches its
        wedged threshold.
        """
        return self._consecutive_timeouts

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # R07.24 (MCP-01): per-call timeouts interrupt — the calling thread
    # waits on a queue with a deadline rather than inside readline().
    #
    # R07.35 (MCP-06): fatal sites raise MCPTransportGone so the manager
    # can distinguish "connection dead — reconnect" from "server answered
    # with an error". Poisoned transports self-heal via reconnect.
    #
    # R07.35 (MCP-07): the per-call reader threads were replaced by ONE
    # persistent reader + id router. A per-call timeout now raises
    # MCPRequestTimeoutError (plain, not Gone) and does NOT poison — slow
    # tools no longer kill the connection. EOF / write failure / close
    # remain Gone; consecutive timeouts surface via consecutive_timeouts
    # so the manager can reconnect a WEDGED server.
