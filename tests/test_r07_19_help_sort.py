"""R07.19 (follow-up #6) — alphabetical ``-h`` output for the whole CLI.

User report: ``agentkthx chat -h`` listed options in registration order
(the ``add_agent_args()`` build order), so flags were hard to find once
the set grew past 30 entries. The same complaint applies to every other
``-h`` in the CLI.

Fix (two layers):

1. ``SortedHelpFormatter`` — renders the "options:" listing AND the
   "usage:" line with optionals sorted by primary long name (``-m,
   --model`` sorts under "model"); positionals keep their semantic
   registration order. Applied recursively by ``apply_sorted_help()`` at
   the end of ``create_parser()`` and again in ``main()`` after plugin
   CLI subparsers are registered (idempotent).
2. Registration order — subcommands are registered alphabetically
   (plugins before run, tools before turbo, turbo status before stop)
   and ``apply_sorted_help()`` re-sorts the subparsers registries in
   place so late-registered plugin commands also land in alphabetical
   position instead of being appended after ``version``.

These tests pin the rendered contract for every parser in the tree
(top level, all 15 subcommands, all 4 turbo sub-subcommands): the
"options:" section reads A→Z, the "usage:" line follows the same order
as the listing, positionals stay put, and plugin commands slot in
alphabetically. Pure string/AST checks — no network, no LLM calls.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agentkthx.cli.main as cli_main
import agentkthx.cli.parser as cli_parser
from agentkthx.cli.parser import SortedHelpFormatter, apply_sorted_help, create_parser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A subcommand-listing entry in root/turbo help is indented 4 spaces and
# starts with a word character; wrapped help continuations start deeper.
_SUBCOMMAND_ENTRY_RE = re.compile(r"^    (\w[\w-]*)")


def _option_entries(help_text: str) -> list[str]:
    """Return the header line of every option entry in the 'options:' section.

    Entry headers start with exactly two spaces then '-' (wrapped help
    continuations are indented to column 24+, so they never match).
    """
    idx = help_text.index("\noptions:\n")
    section = help_text[idx + len("\noptions:\n") :]
    return [ln for ln in section.splitlines() if ln.startswith("  -")]


def _primary_keys(headers: list[str]) -> list[str]:
    """Derive each entry's sort key the same way SortedHelpFormatter does."""
    keys = []
    for header in headers:
        longs = re.findall(r"--[\w-]*", header)
        primary = longs[0] if longs else header[2:].split()[0]
        keys.append(primary.lstrip("-").lower())
    return keys


def _all_parsers() -> dict[str, argparse.ArgumentParser]:
    """Map '<root>' plus every subcommand name (nested) to its parser."""
    root = create_parser()
    out = {"<root>": root}

    def walk(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    out[name] = sub
                    walk(sub)

    walk(root)
    return out


ALL_PARSERS = _all_parsers()


# ---------------------------------------------------------------------------
# Every -h renders its options A→Z
# ---------------------------------------------------------------------------


class TestOptionsListingSorted:
    @pytest.mark.parametrize("name", sorted(ALL_PARSERS))
    def test_options_section_alphabetical(self, name):
        parser = ALL_PARSERS[name]
        headers = _option_entries(parser.format_help())
        assert headers, f"{name} -h rendered no option entries"
        keys = _primary_keys(headers)
        assert keys == sorted(keys), f"{name} -h options not alphabetical: {keys}"

    def test_chat_flags_render_under_long_names(self):
        """-m/--model sorts under 'model', -u/--user under 'user' (last)."""
        headers = _option_entries(ALL_PARSERS["chat"].format_help())
        keys = _primary_keys(headers)
        assert "model" in keys  # despite the entry rendering as "-m MODEL, ..."
        assert keys[-1] == "user"  # -u/--user is chat-specific → sorts last

    def test_chat_help_flag_sorts_under_help(self):
        """-h/--help lands between --force-react and --max-retries."""
        headers = _option_entries(ALL_PARSERS["chat"].format_help())
        keys = _primary_keys(headers)
        assert keys[keys.index("help") - 1] == "force-react"
        assert keys[keys.index("help") + 1] == "max-retries"

    def test_turbo_start_double_dash_sorts_first(self):
        """The bare '--' llama-server passthrough renders as first option."""
        headers = _option_entries(ALL_PARSERS["start"].format_help())
        assert headers[0].startswith("  -- ")
        assert _primary_keys(headers)[0] == ""

    def test_root_help_has_no_option_entries_beyond_help(self):
        headers = _option_entries(ALL_PARSERS["<root>"].format_help())
        assert _primary_keys(headers) == ["help"]


# ---------------------------------------------------------------------------
# Subcommand listings (root + turbo) are alphabetical
# ---------------------------------------------------------------------------


class TestSubcommandListingSorted:
    def test_root_choices_registered_alphabetically(self):
        names = list(create_parser()._subparsers_action.choices)
        assert names == sorted(names)
        assert names == [
            "agent",
            "auth",
            "chat",
            "config",
            "mcp",
            "modelfile",
            "models",
            "plugins",
            "run",
            "sessions",
            "skills",
            "soul",
            "souls",
            "test",
            "tools",
            "turbo",
            "update",
            "version",
        ]

    def test_root_help_metavar_alphabetical(self):
        text = create_parser().format_usage()
        metavar = re.search(r"\{([^}]+)\}", text)
        assert metavar is not None
        names = metavar.group(1).split(",")
        assert names == sorted(names)

    def test_root_help_lists_commands_alphabetically(self):
        text = create_parser().format_help()
        entries = [m.group(1) for ln in text.splitlines() if (m := _SUBCOMMAND_ENTRY_RE.match(ln))]
        assert entries, "root -h did not list subcommands individually"
        assert entries == sorted(entries)

    def test_turbo_subcommands_alphabetical(self):
        turbo = create_parser()._subparsers_action.choices["turbo"]
        turbo_sub = next(a for a in turbo._actions if isinstance(a, argparse._SubParsersAction))
        assert list(turbo_sub.choices) == ["list", "start", "status", "stop"]

    def test_plugin_command_sorted_into_listing(self):
        """Late-registered plugin commands slot in alphabetically, not last."""
        root = create_parser()
        sub_action = root._subparsers_action
        sub_action.add_parser("aardvark-plugin", help="* aardvark-plugin [plugin]")
        apply_sorted_help(root)
        names = list(sub_action.choices)
        assert names == sorted(names)
        assert names.index("aardvark-plugin") < names.index("version")
        assert sub_action.choices["aardvark-plugin"].formatter_class is SortedHelpFormatter


# ---------------------------------------------------------------------------
# Formatter contract
# ---------------------------------------------------------------------------


class TestFormatterContract:
    def test_sort_key_keeps_positionals_first_and_in_order(self):
        pos = argparse.Action(option_strings=[], dest="prompt")
        opt = argparse.Action(option_strings=["-m", "--model"], dest="model")
        assert SortedHelpFormatter._sort_key(pos) < SortedHelpFormatter._sort_key(opt)
        assert SortedHelpFormatter._sort_key(pos)[0] == 0

    def test_sort_key_uses_primary_long_name(self):
        act = argparse.Action(option_strings=["-m", "--model"], dest="model")
        assert SortedHelpFormatter._sort_key(act) == (1, ("model",))

    def test_sort_key_uses_canonical_short_when_no_long(self):
        act = argparse.Action(option_strings=["--"], dest="extra_args")
        assert SortedHelpFormatter._sort_key(act) == (1, ("",))

    def test_apply_sorted_help_is_recursive_and_idempotent(self):
        root = create_parser()
        apply_sorted_help(root)
        apply_sorted_help(root)  # second sweep must not raise or unsort
        assert root.formatter_class is SortedHelpFormatter
        for name, sub in root._subparsers_action.choices.items():
            assert sub.formatter_class is SortedHelpFormatter, name

    def test_positional_arguments_stay_in_semantic_order(self):
        """run's positional section still leads with 'prompt' (unsorted zone)."""
        text = ALL_PARSERS["run"].format_help()
        pos_section = text.split("positional arguments:")[1].split("options:")[0]
        assert pos_section.strip().startswith("prompt")


# ---------------------------------------------------------------------------
# usage: line follows the same order as the options listing
# ---------------------------------------------------------------------------


class TestUsageLineSorted:
    @pytest.mark.parametrize("name", sorted(ALL_PARSERS))
    def test_usage_flags_match_options_listing_order(self, name):
        parser = ALL_PARSERS[name]
        text = parser.format_help()
        # usage is one paragraph: from 'usage:' up to the first blank line
        usage_block = text.split("usage:", 1)[1].split("\n\n", 1)[0]
        usage_flags = re.findall(r"(?<![\w-])--?[\w-]*", usage_block)
        headers = _option_entries(text)
        first_tokens = [h[2:].split()[0].rstrip(",") for h in headers]
        assert usage_flags == first_tokens, f"{name} usage/listing order diverged"


# ---------------------------------------------------------------------------
# Wiring + behavior pins
# ---------------------------------------------------------------------------


class TestWiringPins:
    def test_create_parser_applies_formatter(self):
        src = inspect.getsource(cli_parser)
        assert "apply_sorted_help(parser)" in src
        assert src.index("apply_sorted_help(parser)") < src.index("return parser")

    def test_main_reapplies_after_plugin_registration(self):
        src = inspect.getsource(cli_main)
        assert "apply_sorted_help(parser)" in src
        assert src.index("apply_sorted_help(parser)") < src.index("parser.parse_args(argv)")


class TestParsingUnchanged:
    def test_chat_args_still_parse(self):
        ns = create_parser().parse_args(["chat", "--model", "qwen", "-u", "VTSTech"])
        assert (ns.command, ns.model, ns.user) == ("chat", "qwen", "VTSTech")

    def test_turbo_start_args_still_parse(self):
        ns = create_parser().parse_args(["turbo", "start", "--batch-size", "4096", "qwen2.5:7b"])
        assert (ns.turbo_command, ns.batch_size, ns.model) == ("start", 4096, "qwen2.5:7b")

    def test_soul_positional_and_level_still_parse(self):
        ns = create_parser().parse_args(["soul", "--level", "3", "path/to/soul"])
        assert (ns.path, ns.level) == ("path/to/soul", 3)
