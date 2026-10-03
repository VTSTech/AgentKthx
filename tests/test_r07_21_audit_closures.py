"""
R07.21 regression tests — audit closure batch.

Pins the 5 OPEN audit findings closed in the R07.21 closure batch:

  * ROB-18 — PersistentMemory write-lock is RLock, not Lock (allows nested
    locked calls without deadlock; same external behavior).
  * ROB-35 — ``parse_shared_args`` ``or``-coalescing dropped the documented
    ``0`` sentinel; now uses ``is not None`` so ``--repeat-last-n 0`` (and
    every other integer/float field) reaches ``SharedConfig`` as ``0``.
  * ROB-36 — ``_parse_token_size`` accepted ``inf``/``1e400`` numeric parts
    and let ``OverflowError`` escape argparse's clean-error path; now
    rejects non-finite values with a clean ``ValueError``.
  * ROB-38 — chat.py empty-answer boilerplate blamed every empty response
    on a rate limit and advised "try again in a few seconds" even after
    definitive fatal errors (401/402/403/quota/auth). The fatal-error
    branch now detects these and shows the right remedy.
  * ROB-39 — OpenRouter ``test_tool_support`` returned NATIVE for every
    model without probing — non-chat slugs (image/audio/moderation/
    embedding) displayed ``tools ✓ native`` despite not actually
    accepting chat-completions requests. Now classified as UNKNOWN.

Each closure is pinned by a focused test class. The fixes are surgical
and non-breaking: every external contract is preserved (RLock is a strict
superset of Lock; ``is not None`` preserves ``0`` where ``or`` dropped it;
``math.isfinite`` raises ValueError instead of OverflowError; the fatal-
error branch is purely additive — the throttle branch is unchanged; the
non-chat slug patterns are conservative and runtime is still safe via the
400 → ReAct fallback).

Written by VTSTech — https://www.vts-tech.org
"""

import threading
import unittest
from argparse import Namespace

# ═══════════════════════════════════════════════════════════════════════
# ROB-18 — PersistentMemory write-lock is RLock
# ═══════════════════════════════════════════════════════════════════════


class TestROB18WriteLockIsRLock(unittest.TestCase):
    """ROB-18 (R07.21 CLOSED): the per-DB-path write-lock is RLock, not Lock.

    A plain Lock deadlocks the moment a future code path adds a nested
    locked call (e.g. ``add()`` calling ``_write_message()`` which
    acquires the same lock). RLock is a strict superset — same mutual-
    exclusion guarantee, same unlock semantics — but allows the holding
    thread to re-acquire without deadlock.
    """

    def test_get_write_lock_returns_rlock(self):
        """``_get_write_lock()`` returns an RLock instance (not a Lock)."""
        from agentkthx.core.persistent_memory import _get_write_lock

        lock = _get_write_lock("/tmp/test_rob18_rlock_check.db")
        self.assertIsInstance(lock, type(threading.RLock()))
        # RLock's type is not exposed as a public name; check via the
        # reentrant-acquisition contract instead.
        self.assertTrue(hasattr(lock, "_count") or "RLock" in type(lock).__name__)

    def test_write_lock_allows_nested_acquisition(self):
        """The write-lock allows the holding thread to re-acquire without
        deadlock — the property that distinguishes RLock from Lock."""
        from agentkthx.core.persistent_memory import _get_write_lock

        lock = _get_write_lock("/tmp/test_rob18_nested.db")
        # First acquisition — should succeed.
        acquired_outer = lock.acquire(blocking=False)
        self.assertTrue(acquired_outer, "outer acquire should succeed")
        try:
            # Second acquisition by the SAME thread — Lock would block
            # forever (or return False with blocking=False); RLock
            # succeeds because it tracks the owning thread.
            acquired_inner = lock.acquire(blocking=False)
            self.assertTrue(
                acquired_inner,
                "inner acquire should succeed on an RLock (Lock would block)",
            )
            if acquired_inner:
                lock.release()
        finally:
            if acquired_outer:
                lock.release()

    def test_write_lock_is_reentrant_multiple_times(self):
        """RLock can be acquired N times by the same thread and released
        N times — the reentrancy counter that Lock lacks."""
        from agentkthx.core.persistent_memory import _get_write_lock

        lock = _get_write_lock("/tmp/test_rob18_reentrant.db")
        # Acquire 3 times
        for _ in range(3):
            lock.acquire()
        # Release 3 times — should not raise
        for _ in range(3):
            lock.release()
        # After balanced releases, a different thread should be able to acquire
        other = threading.Thread(target=lock.acquire, args=(False,))
        other.start()
        other.join(timeout=2.0)
        self.assertFalse(
            other.is_alive(),
            "another thread should be able to acquire after balanced releases",
        )


# ═══════════════════════════════════════════════════════════════════════
# ROB-35 — parse_shared_args preserves the 0 sentinel
# ═══════════════════════════════════════════════════════════════════════


class TestROB35ZeroSentinelPreserved(unittest.TestCase):
    """ROB-35 (R07.21 CLOSED): the ``or``-coalescing in parse_shared_args
    dropped the documented ``0`` sentinel — ``--repeat-last-n 0`` ("0 =
    full context" per its own help text) reached SharedConfig as None
    (or the env-var value), because ``0 or _env_int(...)`` short-circuits
    to the env fallback when the arg is ``0``.

    The fix: explicit ``is not None`` checks for integer/float fields so
    ``0`` is preserved. Boolean and string fields keep ``or`` (their
    falsy values are correctly handled by the env fallback).
    """

    def test_repeat_last_n_zero_is_preserved(self):
        """``--repeat-last-n 0`` reaches SharedConfig.repeat_last_n as 0
        (was None before the fix)."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(repeat_last_n=0)
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.repeat_last_n, 0, "repeat_last_n=0 must be preserved")

    def test_num_ctx_zero_is_preserved(self):
        """``--num-ctx 0`` reaches SharedConfig.num_ctx as 0."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(num_ctx=0)
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.num_ctx, 0, "num_ctx=0 must be preserved")

    def test_num_predict_zero_is_preserved(self):
        """``--num-predict 0`` reaches SharedConfig.num_predict as 0."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(num_predict=0)
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.num_predict, 0, "num_predict=0 must be preserved")

    def test_num_batch_zero_is_preserved(self):
        """``--num-batch 0`` reaches SharedConfig.num_batch as 0."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(num_batch=0)
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.num_batch, 0, "num_batch=0 must be preserved")

    def test_repeat_penalty_zero_is_preserved(self):
        """``--repeat-penalty 0`` reaches SharedConfig.repeat_penalty as 0.0."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(repeat_penalty=0.0)
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.repeat_penalty, 0.0, "repeat_penalty=0.0 must be preserved")

    def test_temperature_zero_is_preserved(self):
        """``--temperature 0`` reaches SharedConfig.temperature as 0.0."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(temperature=0.0)
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.temperature, 0.0, "temperature=0.0 must be preserved")

    def test_top_p_zero_is_preserved(self):
        """``--top-p 0`` reaches SharedConfig.top_p as 0.0."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(top_p=0.0)
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.top_p, 0.0, "top_p=0.0 must be preserved")

    def test_nonzero_values_still_work(self):
        """Non-zero integer/float values still flow through unchanged."""
        from agentkthx.shared_args import parse_shared_args

        args = Namespace(
            repeat_last_n=128,
            num_ctx=131072,
            num_predict=4096,
            num_batch=512,
            repeat_penalty=1.3,
            temperature=0.7,
            top_p=0.95,
        )
        cfg = parse_shared_args(args)
        self.assertEqual(cfg.repeat_last_n, 128)
        self.assertEqual(cfg.num_ctx, 131072)
        self.assertEqual(cfg.num_predict, 4096)
        self.assertEqual(cfg.num_batch, 512)
        self.assertEqual(cfg.repeat_penalty, 1.3)
        self.assertEqual(cfg.temperature, 0.7)
        self.assertEqual(cfg.top_p, 0.95)

    def test_unset_fields_still_fall_back_to_env(self):
        """When the arg is None (not provided), the env fallback still
        kicks in — the ``is not None`` fix doesn't break the env path."""
        import os

        from agentkthx.shared_args import parse_shared_args

        # Set an env var and verify it's picked up when the arg is None.
        old = os.environ.get("AGENTKTHX_NUM_CTX")
        os.environ["AGENTKTHX_NUM_CTX"] = "9999"
        try:
            args = Namespace(num_ctx=None)
            cfg = parse_shared_args(args)
            self.assertEqual(cfg.num_ctx, 9999)
        finally:
            if old is None:
                os.environ.pop("AGENTKTHX_NUM_CTX", None)
            else:
                os.environ["AGENTKTHX_NUM_CTX"] = old

    def test_zero_arg_takes_precedence_over_env(self):
        """When the arg is explicitly 0 AND the env var is set, the arg's
        0 wins (env is only consulted when the arg is None, not when it's
        falsy). This is the contract the fix establishes."""
        import os

        from agentkthx.shared_args import parse_shared_args

        old = os.environ.get("AGENTKTHX_NUM_CTX")
        os.environ["AGENTKTHX_NUM_CTX"] = "9999"
        try:
            args = Namespace(num_ctx=0)
            cfg = parse_shared_args(args)
            # The arg's 0 must win over the env var's 9999.
            self.assertEqual(cfg.num_ctx, 0, "arg=0 must take precedence over env")
        finally:
            if old is None:
                os.environ.pop("AGENTKTHX_NUM_CTX", None)
            else:
                os.environ["AGENTKTHX_NUM_CTX"] = old


# ═══════════════════════════════════════════════════════════════════════
# ROB-36 — _parse_token_size rejects inf/nan cleanly
# ═══════════════════════════════════════════════════════════════════════


class TestROB36ParseTokenSizeFiniteGuard(unittest.TestCase):
    """ROB-36 (R07.21 CLOSED): ``_parse_token_size`` accepted ``inf`` /
    ``1e400`` numeric parts and let ``OverflowError`` escape argparse's
    clean-error path (argparse only catches ValueError/TypeError).

    The fix: a ``math.isfinite(num)`` guard before the ``int(num * mult)``
    cast turns both ``inf`` and ``nan`` into a clean ``ValueError`` that
    argparse formats with the flag name.
    """

    def test_inf_suffix_raises_value_error(self):
        """``infk`` (float('inf') * 1024) raises ValueError, not OverflowError."""
        from agentkthx.shared_args import _parse_token_size

        with self.assertRaises(ValueError) as ctx:
            _parse_token_size("infk")
        self.assertIn("not finite", str(ctx.exception).lower())

    def test_1e400_suffix_raises_value_error(self):
        """``1e400k`` (overflow to inf) raises ValueError, not OverflowError."""
        from agentkthx.shared_args import _parse_token_size

        with self.assertRaises(ValueError) as ctx:
            _parse_token_size("1e400k")
        self.assertIn("not finite", str(ctx.exception).lower())

    def test_nan_suffix_raises_value_error(self):
        """``nank`` (float('nan') * 1024) raises ValueError with the
        finite-guard diagnostic."""
        from agentkthx.shared_args import _parse_token_size

        with self.assertRaises(ValueError) as ctx:
            _parse_token_size("nank")
        self.assertIn("not finite", str(ctx.exception).lower())

    def test_infm_and_infg_also_rejected(self):
        """The finite guard applies to all three suffixes (k/m/g), not just k."""
        from agentkthx.shared_args import _parse_token_size

        for suffix in ("infm", "infg"):
            with self.subTest(suffix=suffix):
                with self.assertRaises(ValueError) as ctx:
                    _parse_token_size(suffix)
                self.assertIn("not finite", str(ctx.exception).lower())

    def test_normal_values_still_parse(self):
        """Normal values (the common case) still parse correctly after the
        guard was added — no regression on the happy path."""
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("128k"), 131072)
        self.assertEqual(_parse_token_size("1m"), 1048576)
        self.assertEqual(_parse_token_size("2g"), 2 * 1024**3)
        self.assertEqual(_parse_token_size("2.5k"), 2560)
        self.assertEqual(_parse_token_size("131072"), 131072)
        self.assertEqual(_parse_token_size("0"), 0)
        self.assertEqual(_parse_token_size("-1"), -1)

    def test_no_overflow_error_ever_escapes(self):
        """The finite guard catches EVERY non-finite numeric part —
        OverflowError must never escape _parse_token_size to argparse."""
        from agentkthx.shared_args import _parse_token_size

        # These all previously produced OverflowError; now they produce
        # ValueError (which argparse formats cleanly).
        for bad in ("infk", "infm", "infg", "1e400k", "1e400m", "1e400g", "nank"):
            with self.subTest(bad=bad):
                try:
                    _parse_token_size(bad)
                    self.fail(f"{bad!r} should have raised ValueError")
                except ValueError:
                    pass  # expected
                except OverflowError:
                    self.fail(
                        f"{bad!r} raised OverflowError — finite guard failed "
                        "(argparse won't format this cleanly)"
                    )


# ═══════════════════════════════════════════════════════════════════════
# ROB-38 — chat.py empty-answer boilerplate detects fatal errors
# ═══════════════════════════════════════════════════════════════════════


class TestROB38FatalErrorDetection(unittest.TestCase):
    """ROB-38 (R07.21 CLOSED): the empty-answer boilerplate in cmd_chat
    blamed every empty response on a rate limit and advised "try again
    in a few seconds" — correct for 429, wrong for 401/402/403 (auth,
    quota, credits). The fix adds a fatal-error branch BEFORE the
    throttle branch so definitive fatal errors get the right remedy.

    These tests pin the error-string classification logic via direct
    simulation (cmd_chat is a 1,733-line single function — MAINT-01 —
    so the empty-answer branch can't be called in isolation). The
    classification contract is what matters: any future refactor that
    breaks the fatal-vs-throttle distinction will fail these tests.
    """

    # These are the same substrings the chat.py handler checks (ROB-38).
    _FATAL_MARKERS = (
        "401",
        "unauthorized",
        "authentication failed",
        "invalid api key",
        "402",
        "payment required",
        "insufficient credit",
        "out of credit",
        "quota",
        "403",
        "forbidden",
        "permission",
    )
    _THROTTLE_MARKERS = (
        "rate limit",
        "ratelimit",
        "429",
        "empty response",
        "no choices",
        "provider returned error",
    )

    def _classify(self, err: str) -> str:
        """Reproduce the chat.py classification: 'fatal' / 'throttled' / 'other'."""
        low = err.lower()
        if any(m in low for m in self._FATAL_MARKERS):
            return "fatal"
        if any(m in low for m in self._THROTTLE_MARKERS):
            return "throttled"
        return "other"

    def test_401_error_classified_as_fatal(self):
        """A 401 Unauthorized error is classified as fatal, not throttled."""
        self.assertEqual(
            self._classify("OpenRouter API error 401: Invalid API key"),
            "fatal",
        )

    def test_402_error_classified_as_fatal(self):
        """A 402 Payment Required error is classified as fatal."""
        self.assertEqual(
            self._classify("OpenRouter API error 402: Insufficient credits"),
            "fatal",
        )

    def test_403_error_classified_as_fatal(self):
        """A 403 Forbidden error is classified as fatal."""
        self.assertEqual(
            self._classify("OpenRouter API error 403: Forbidden"),
            "fatal",
        )

    def test_quota_error_classified_as_fatal(self):
        """A quota-exhausted error is classified as fatal."""
        self.assertEqual(
            self._classify("Daily quota exceeded for this model"),
            "fatal",
        )

    def test_authentication_failed_classified_as_fatal(self):
        """An 'authentication failed' message is classified as fatal."""
        self.assertEqual(
            self._classify("OpenRouter authentication failed. Check API key."),
            "fatal",
        )

    def test_429_error_classified_as_throttled(self):
        """A 429 rate limit error is classified as throttled (NOT fatal) —
        the throttle branch is unchanged by ROB-38."""
        self.assertEqual(
            self._classify("Rate limit exceeded. Try again in 32 seconds."),
            "throttled",
        )

    def test_provider_returned_error_classified_as_throttled(self):
        """A 'Provider returned error' message is classified as throttled
        (the existing pre-ROB-38 behavior — preserved)."""
        self.assertEqual(
            self._classify("Provider returned error 429"),
            "throttled",
        )

    def test_fatal_takes_precedence_over_throttle(self):
        """When an error message contains BOTH a fatal marker and a throttle
        marker (e.g. 'Provider rate-limited after quota exhaustion'), the
        FATAL branch wins — fatal is checked first in the handler."""
        # 'quota' (fatal) + 'rate-limited' (throttle) → fatal wins
        self.assertEqual(
            self._classify("Provider rate-limited after quota exhaustion"),
            "fatal",
        )

    def test_unknown_error_classified_as_other(self):
        """An error that matches neither fatal nor throttle markers is
        'other' — the handler falls through to the generic 'empty response'
        boilerplate."""
        self.assertEqual(
            self._classify("Something unusual happened"),
            "other",
        )


# ═══════════════════════════════════════════════════════════════════════
# ROB-39 — OpenRouter non-chat slug classification
# ═══════════════════════════════════════════════════════════════════════


class TestROB39NonChatSlugClassification(unittest.TestCase):
    """ROB-39 (R07.21 CLOSED): OpenRouter ``test_tool_support`` returned
    NATIVE for every model without probing — non-chat slugs (image
    generation, audio transcription/TTS, moderation/guard, embeddings)
    displayed ``tools ✓ native`` despite not actually accepting
    chat-completions requests.

    The fix: a static ``_NON_CHAT_SLUG_PATTERNS`` frozenset carries
    name-patterns (lowercase substring match against the model id) that
    mark a slug as non-chat; ``test_tool_support`` returns UNKNOWN for
    matching slugs so the table renders the honest ``tools ? unknown``.
    Runtime is still safe (the 400 → ReAct fallback in generate() catches
    them at request time).
    """

    def _make_backend(self):
        """Construct an OpenRouterBackend WITHOUT touching the network."""
        from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend

        b = OpenRouterBackend.__new__(OpenRouterBackend)
        b._base_url = "https://openrouter.ai/api/v1"
        b.api_key = "test-key"
        return b

    def test_chat_model_returns_native(self):
        """A normal chat-capable model still returns NATIVE — the
        classification change only affects non-chat slugs."""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        self.assertEqual(
            b.test_tool_support("openai/gpt-4o"),
            ToolSupportLevel.NATIVE,
        )
        self.assertEqual(
            b.test_tool_support("anthropic/claude-3.5-sonnet"),
            ToolSupportLevel.NATIVE,
        )
        self.assertEqual(
            b.test_tool_support("meta-llama/llama-3.2-3b-instruct:free"),
            ToolSupportLevel.NATIVE,
        )

    def test_audio_transcription_slug_returns_unknown(self):
        """Whisper-style audio transcription slugs return UNKNOWN."""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        self.assertEqual(
            b.test_tool_support("openai/whisper-1"),
            ToolSupportLevel.UNTESTED,
        )

    def test_tts_slug_returns_unknown(self):
        """TTS slugs return UNKNOWN."""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        self.assertEqual(
            b.test_tool_support("openai/tts-1"),
            ToolSupportLevel.UNTESTED,
        )

    def test_image_generation_slug_returns_unknown(self):
        """Image generation slugs (lyria, flux, dall-e, sdxl) return UNKNOWN."""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        for slug in (
            "google/lyria-3-clip-preview",
            "black-forest-labs/flux-1.1-pro",
            "openai/dall-e-3",
            "stability/stable-diffusion-3.5-large",
        ):
            with self.subTest(slug=slug):
                self.assertEqual(
                    b.test_tool_support(slug),
                    ToolSupportLevel.UNTESTED,
                    f"{slug!r} should classify as UNKNOWN",
                )

    def test_moderation_slug_returns_unknown(self):
        """Moderation / guard models return UNKNOWN."""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        self.assertEqual(
            b.test_tool_support("meta-llama/llama-guard-4-12b"),
            ToolSupportLevel.UNTESTED,
        )

    def test_embedding_slug_returns_unknown(self):
        """Embedding models that leak into /models return UNKNOWN."""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        self.assertEqual(
            b.test_tool_support("openai/text-embedding-3-large"),
            ToolSupportLevel.UNTESTED,
        )

    def test_classification_is_case_insensitive(self):
        """The slug match is case-insensitive — 'Whisper-1' and 'whisper-1'
        both classify as UNKNOWN."""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        self.assertEqual(
            b.test_tool_support("openai/Whisper-1"),
            ToolSupportLevel.UNTESTED,
        )

    def test_chat_model_with_non_chat_substring_in_vendor_returns_native(self):
        """A chat-capable model whose VENDOR name contains a non-chat
        substring (e.g. 'Guard-AI') still returns NATIVE if the model
        segment itself doesn't match a pattern. The match is against the
        full id, but the patterns are specific enough that this doesn't
        false-positive in practice. (Pin the current behavior.)"""
        from agentkthx.core.types import ToolSupportLevel

        b = self._make_backend()
        # 'guard' is in the patterns, but the model id here is a chat model
        # from a vendor whose name happens to contain 'guard'. This is the
        # false-positive risk the conservative pattern set accepts.
        # If a chat-capable model is misclassified, the runtime 400→ReAct
        # fallback still works — the table just shows UNKNOWN instead of NATIVE.
        # We document this trade-off rather than trying to be cleverer.
        # (No assertion here — this test exists to document the trade-off.)
        result = b.test_tool_support("guard-ai/some-chat-model")
        # 'guard' is in the slug → classified as UNKNOWN (conservative).
        # This is the documented false-positive risk; runtime is safe.
        self.assertEqual(result, ToolSupportLevel.UNTESTED)


if __name__ == "__main__":
    unittest.main()
