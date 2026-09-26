"""
AgentKthx — Plugin Spec v0.2 Compliance Tests

Tests the PluginManager against docs/PLUGIN_SPEC_v0.2.md:
name constraints, dual-form manifest parsing, $schema handling,
honored entrypoint, warn-only compatibility, multi-root discovery,
placeholder expansion, hooks (declarative + imperative, error isolation),
tools (ownership + collision), config secret guard, and unload completeness.

Written by VTSTech — https://www.vts-tech.org
"""

import json
import sys
from pathlib import Path

import pytest

from agentkthx.plugins._loader import (
    CANONICAL_SCHEMA,
    EXT_NAMESPACE,
    Plugin,
    PluginManager,
    _parse_manifest,
    constraint_satisfied,
    expand_placeholders,
    parse_version,
    valid_plugin_name,
)

SCHEMA = CANONICAL_SCHEMA


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def write_plugin(
    root: Path,
    name: str,
    manifest: dict,
    init_py: str = 'def register(manager):\n    pass\n\ndef unregister(manager):\n    pass\n',
    extra_files: dict | None = None,
) -> Path:
    """Create a plugin directory with a manifest and optional extra files."""
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pdir / "__init__.py").write_text(init_py, encoding="utf-8")
    for fname, content in (extra_files or {}).items():
        fpath = pdir / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return pdir


def v02_manifest(
    name: str,
    *,
    description: str = "test plugin",
    type_: str = "feature",
    provides: dict | None = None,
    config: dict | None = None,
    compat: str = ">=0.1.0",
    entrypoint: str = "__init__",
    version: str = "0.1.0",
) -> dict:
    ext = {"type": type_, "entrypoint": entrypoint, "provides": provides or {}}
    if config is not None:
        ext["config"] = config
    if compat is not None:
        ext["compatibility"] = {"agentkthx": compat}
    return {
        "$schema": SCHEMA,
        "name": name,
        "version": version,
        "description": description,
        "author": {"name": "VTSTech"},
        "license": "MIT",
        "extensions": {EXT_NAMESPACE: ext},
    }


@pytest.fixture
def root(tmp_path) -> Path:
    r = tmp_path / "plugins"
    r.mkdir()
    return r


def make_manager(*roots: Path) -> PluginManager:
    return PluginManager(plugins_dir=[str(r) for r in roots])


# ---------------------------------------------------------------------------
# Name constraints (spec §Plugin name constraints)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["my-plugin", "acme.tools", "lint3r", "a", "v2-beta"])
def test_valid_names(name):
    assert valid_plugin_name(name)


@pytest.mark.parametrize(
    "name",
    ["My-Plugin", "-start", "has--double", "too.many..dots", "", "end-", "a" * 65, "sp ace"],
)
def test_invalid_names(name):
    assert not valid_plugin_name(name)


def test_name_directory_mismatch_rejected(root):
    write_plugin(root, "wrongdir", v02_manifest("right-name"))
    pm = make_manager(root)
    names = [m.name for m in pm.discover()]
    assert "right-name" not in names
    assert any("does not match directory" in w for w in pm.warnings)


def test_dotted_name_rejected_in_builtin_root(tmp_path):
    """Dotted names are only valid for external roots (spec §Name constraints)."""
    pdir = tmp_path / "acme.tools"
    pdir.mkdir()
    (pdir / "plugin.json").write_text(json.dumps(v02_manifest("acme.tools")))
    with pytest.raises(ValueError):
        _parse_manifest(pdir / "plugin.json", root_kind="builtin")


# ---------------------------------------------------------------------------
# Dual-form parsing + extensions precedence (spec §Extensions)
# ---------------------------------------------------------------------------

def test_v02_form_loads(root):
    write_plugin(root, "alpha", v02_manifest("alpha"))
    pm = make_manager(root)
    manifests = pm.discover()
    assert [m.name for m in manifests] == ["alpha"]
    assert manifests[0].schema == SCHEMA
    assert manifests[0].type == "feature"


def test_legacy_form_still_loads(root):
    legacy = {
        "name": "legacy",
        "version": "0.1.0",
        "description": "old style",
        "author": "VTSTech",
        "license": "MIT",
        "type": "backend",
        "entrypoint": "__init__",
        "depends": [],
        "config": {"env_prefix": "LEGACY", "defaults": {"LEGACY_X": "1"}},
        "provides": {"cli_commands": []},
        "compatibility": {"agentnova": ">=0.4.0"},
    }
    write_plugin(root, "legacy", legacy)
    pm = make_manager(root)
    manifests = pm.discover()
    assert manifests[0].name == "legacy"
    assert manifests[0].type == "backend"
    assert manifests[0].schema is None
    # absent $schema SHOULD produce a deprecation warning (spec §$schema)
    assert any("no $schema" in w for w in pm.warnings)


def test_extensions_take_precedence_over_top_level(root):
    raw = v02_manifest("both")
    raw["type"] = "backend"  # legacy top-level conflicting with extensions
    write_plugin(root, "both", raw)
    pm = make_manager(root)
    m = pm.discover()[0]
    assert m.type == "feature"  # extensions value wins
    assert "type" in m.legacy_fields_used
    assert any("present in both" in w for w in pm.warnings)


def test_unknown_fields_warn_and_ignore(root):
    raw = v02_manifest("unknowny")
    raw["mystery_field"] = 123
    raw["extensions"] = {
        EXT_NAMESPACE: {**raw["extensions"][EXT_NAMESPACE], "bogus": True},
        "com.other.client": {"whatever": [1, 2, 3]},  # ignored silently
    }
    write_plugin(root, "unknowny", raw)
    pm = make_manager(root)
    assert pm.discover()[0].name == "unknowny"
    assert any("mystery_field" in w for w in pm.warnings)
    assert any("bogus" in w for w in pm.warnings)
    assert not any("com.other.client" in w for w in pm.warnings)


def test_author_object_form(root):
    raw = v02_manifest("auth")
    raw["author"] = {"name": "VTSTech", "email": "x@vts-tech.org", "url": "https://www.vts-tech.org"}
    write_plugin(root, "auth", raw)
    m = make_manager(root).discover()[0]
    assert m.author == "VTSTech"


def test_author_object_invalid_keys_rejected(root):
    raw = v02_manifest("authbad")
    raw["author"] = {"name": "X", "twitter": "@x"}
    write_plugin(root, "authbad", raw)
    pm = make_manager(root)
    assert pm.discover() == []
    assert any("author object" in w for w in pm.warnings)


def test_spdx_license_warns_only(root):
    raw = v02_manifest("lic", )
    raw["license"] = "Not-A-Real-License"
    write_plugin(root, "lic", raw)
    pm = make_manager(root)
    assert pm.discover()[0].name == "lic"
    assert any("SPDX" in w for w in pm.warnings)


# ---------------------------------------------------------------------------
# $schema handling (spec §$schema)
# ---------------------------------------------------------------------------

def test_unrecognized_schema_warns_but_loads(root):
    raw = v02_manifest("weirdschema")
    raw["$schema"] = "https://example.com/some-other-schema.json"
    write_plugin(root, "weirdschema", raw)
    pm = make_manager(root)
    assert pm.discover()[0].name == "weirdschema"
    assert any("unrecognized $schema" in w for w in pm.warnings)


# ---------------------------------------------------------------------------
# Compatibility: warn-only + agentnova alias (spec §Compatibility)
# ---------------------------------------------------------------------------

def test_compatibility_mismatch_warns_but_loads(root):
    init_py = (
        "loaded = []\n"
        "def register(manager):\n"
        "    loaded.append(1)\n"
        "    manager.register_config_defaults('COMPAT', {'COMPAT_X': '1'}, plugin='compat')\n"
        "def unregister(manager):\n"
        "    loaded.clear()\n"
    )
    write_plugin(root, "compat", v02_manifest("compat", compat=">=999.0.0"), init_py=init_py)
    pm = make_manager(root)
    assert pm.load("compat") is not None
    assert pm.is_loaded("compat")
    assert any("requires agentkthx >=999.0.0" in w for w in pm.warnings)


def test_agentnova_alias_warns(root):
    legacy = {
        "name": "oldkey",
        "description": "old compat key",
        "type": "feature",
        "compatibility": {"agentnova": ">=0.4.0"},
    }
    write_plugin(root, "oldkey", legacy)
    pm = make_manager(root)
    assert pm.load("oldkey") is not None
    assert any("'agentnova' is deprecated" in w for w in pm.warnings)


def test_constraint_satisfied_math():
    v = (0, 6, 41)
    assert constraint_satisfied(v, ">=0.5.0")
    assert constraint_satisfied(v, ">=0.5.0,<0.7.0")
    assert not constraint_satisfied(v, ">=0.7.0")
    assert constraint_satisfied(v, "!=0.7.0")
    assert constraint_satisfied(v, "bogus")  # malformed fails open
    assert constraint_satisfied(None, ">=0.1.0")  # unparseable fails open


def test_parse_version():
    assert parse_version("0.6.41") == (0, 6, 41)
    assert parse_version("0.6.41-beta+hash") == (0, 6, 41)
    assert parse_version("nonsense") is None


# ---------------------------------------------------------------------------
# Entrypoint honored (spec §Entrypoint contract)
# ---------------------------------------------------------------------------

def test_entrypoint_submodule_is_honored(root):
    custom_init = "# no register here on purpose\n"
    custom_mod = (
        "def register(manager):\n"
        "    manager.register_config_defaults('ENTRY', {'ENTRY_X': '1'}, plugin='entryp')\n"
        "def unregister(manager):\n"
        "    pass\n"
    )
    write_plugin(
        root,
        "entryp",
        v02_manifest("entryp", entrypoint="custom"),
        init_py=custom_init,
        extra_files={"custom.py": custom_mod},
    )
    pm = make_manager(root)
    plugin = pm.load("entryp")
    assert plugin is not None
    # proof that custom.py's register ran (v0.1 would have imported __init__)
    assert pm.get_config_defaults_by_prefix("ENTRY") == {"ENTRY_X": "1"}


def test_missing_register_fails_load(root):
    write_plugin(root, "noreg", v02_manifest("noreg"), init_py="x = 1\n")
    pm = make_manager(root)
    assert pm.load("noreg") is None
    assert pm.get_plugin_state("noreg") == "failed"
    assert any("no register()" in w for w in pm.warnings)


def test_register_raises_cleanup(root):
    init_py = (
        "def register(manager):\n"
        "    manager.register_config_defaults('BOOM', {'BOOM_X': '1'}, plugin='boom')\n"
        "    raise RuntimeError('kaboom')\n"
        "def unregister(manager):\n"
        "    manager.unregister_config_defaults if False else None\n"
    )
    write_plugin(root, "boom", v02_manifest("boom"), init_py=init_py)
    pm = make_manager(root)
    assert pm.load("boom") is None
    assert pm.get_plugin_state("boom") == "failed"
    # partial registration was purged
    assert pm.get_config_defaults_by_prefix("BOOM") == {}
    assert any("kaboom" in w for w in pm.warnings)


# ---------------------------------------------------------------------------
# Multi-root discovery + collision priority (spec §Plugin roots)
# ---------------------------------------------------------------------------

def test_multi_root_collision_priority(tmp_path):
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    r1.mkdir()
    r2.mkdir()
    write_plugin(r1, "dup", v02_manifest("dup", description="from r1"))
    write_plugin(r2, "dup", v02_manifest("dup", description="from r2"))
    write_plugin(r2, "only2", v02_manifest("only2"))
    pm = PluginManager(plugins_dir=[str(r1), str(r2)])
    manifests = pm.discover()
    names = sorted(m.name for m in manifests)
    assert names == ["dup", "only2"]
    dup = next(m for m in manifests if m.name == "dup")
    assert dup.description == "from r1"
    assert any("duplicate plugin" in w for w in pm.warnings)


def test_env_path_parsing_and_roots(monkeypatch, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("AGENTKTHX_PLUGIN_PATH", f"{a}:{b}")
    pm = PluginManager()
    roots = [p for p, kind in pm._roots]
    assert a in roots and b in roots
    assert PluginManager.parse_plugin_path_env(f"{a}::{b}") == [a, b]


def test_default_roots_include_user_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENTKTHX_PLUGIN_PATH", raising=False)
    pm = PluginManager()
    kinds = [kind for _, kind in pm._roots]
    assert kinds[:2] == ["builtin", "user"]


def test_underscore_and_hidden_dirs_skipped(root):
    write_plugin(root, "_private", v02_manifest("_private"))
    write_plugin(root, ".hidden", v02_manifest(".hidden"))
    pm = make_manager(root)
    assert pm.discover() == []


# ---------------------------------------------------------------------------
# Config: placeholder expansion + secret guard (spec §Config, §Expansion)
# ---------------------------------------------------------------------------

def test_placeholder_expansion(root):
    config = {
        "env_prefix": "PH",
        "defaults": {
            "PH_CACHE": "${PLUGIN_DATA}/cache",
            "PH_MODEL": "${PLUGIN_ROOT}/models/m.bin",
            "PH_UNKNOWN": "${NOT_A_PLACEHOLDER}/x",
        },
    }
    write_plugin(
        root,
        "ph",
        v02_manifest("ph", config=config),
        init_py=(
            "def register(manager):\n"
            "    pass\n"
            "def unregister(manager):\n"
            "    pass\n"
        ),
    )
    pm = make_manager(root)
    assert pm.load("ph") is not None
    defaults = pm.get_config_defaults_by_prefix("PH")
    data_dir = pm.get_plugin_data_dir("ph")
    assert defaults["PH_CACHE"] == f"{data_dir}/cache"
    assert defaults["PH_MODEL"].endswith("/models/m.bin")
    assert (pm.get_plugin("ph").root / "models/m.bin") == Path(defaults["PH_MODEL"])
    assert defaults["PH_UNKNOWN"] == "${NOT_A_PLACEHOLDER}/x"  # stays literal


def test_secret_shaped_default_warns(root):
    config = {
        "env_prefix": "SEC",
        "defaults": {"SEC_PASS": "hunter2", "SEC_TOKEN": "", "SEC_URL": "http://x"},
    }
    write_plugin(root, "sec", v02_manifest("sec", config=config))
    pm = make_manager(root)
    pm.load("sec")
    assert any("SEC_PASS" in w and "secret" in w for w in pm.warnings)
    assert not any("SEC_TOKEN" in w for w in pm.warnings)  # empty default is fine


# ---------------------------------------------------------------------------
# Hooks (spec §Hooks)
# ---------------------------------------------------------------------------

def _hook_plugin(root, name):
    extra = {
        "hooks.py": (
            "calls = []\n"
            "def on_run_start(ctx):\n"
            "    calls.append(('ok', ctx.get('prompt')))\n"
            "def boom(ctx):\n"
            "    raise RuntimeError('hook exploded')\n"
        )
    }
    init_py = (
        "from . import hooks as _h\n"
        "def register(manager):\n"
        "    manager.register_hook('on_run_start', _h.boom, plugin='{name}')\n"
        "def unregister(manager):\n"
        "    pass\n".format(name=name)
    )
    manifest = v02_manifest(
        name,
        provides={"hooks": {"on_run_start": "hooks.on_run_start"}},
    )
    write_plugin(root, name, manifest, init_py=init_py, extra_files=extra)
    return root / name / "hooks.py"


def test_hooks_declarative_and_imperative_with_error_isolation(tmp_path):
    # Use two separate plugin dirs sharing the hooks module via import by path.
    root = tmp_path / "plugins"
    root.mkdir()
    _hook_plugin(root, "hooky")
    pm = make_manager(root)
    assert pm.load("hooky") is not None

    import importlib
    mod = importlib.import_module("agentkthx_ext_plugins_hooky.hooks")
    mod.calls.clear()

    pm.emit("on_run_start", {"prompt": "hello"})
    # declarative handler ran with context; imperative 'boom' raised but was isolated
    assert mod.calls == [("ok", "hello")]
    assert any("hook exploded" in w for w in pm.warnings)


def test_hook_unresolvable_warns_once(root):
    write_plugin(
        root,
        "badhook",
        v02_manifest("badhook", provides={"hooks": {"on_run_start": "nope.missing"}}),
    )
    pm = make_manager(root)
    assert pm.load("badhook") is not None
    pm.emit("on_run_start", {})
    pm.emit("on_run_start", {})
    assert sum(1 for w in pm.warnings if "not found or not callable" in w) == 1


def test_unknown_hook_event_warns(root):
    pm = PluginManager(plugins_dir=[])
    pm.register_hook("on_whatever", lambda ctx: None, plugin="x")
    assert any("unknown hook event" in w for w in pm.warnings)


def test_unload_removes_hooks(root):
    write_plugin(
        root,
        "hunload",
        v02_manifest("hunload", provides={"hooks": {"on_run_end": "hooks.on_end"}}),
        extra_files={"hooks.py": "def on_end(ctx):\n    pass\n"},
    )
    pm = make_manager(root)
    pm.load("hunload")
    assert pm.list_hooks("on_run_end")
    pm.unload("hunload")
    assert pm.list_hooks("on_run_end") == []


# ---------------------------------------------------------------------------
# Tools (spec §Tools)
# ---------------------------------------------------------------------------

def test_tool_registration_ownership_and_collision(root):
    from agentkthx.core.models import Tool, ToolParam

    t1 = Tool(name="my-tool", description="d", params=[])
    pm = PluginManager(plugins_dir=[])
    assert pm.register_tool(t1, plugin="p1") is True
    assert pm.get_tool("my-tool") is t1
    assert pm.list_tool_names() == ["my-tool"]

    # collision rejected, original untouched
    t2 = Tool(name="my-tool", description="other", params=[])
    assert pm.register_tool(t2, plugin="p2") is False
    assert pm.get_tool("my-tool") is t1
    assert any("already registered" in w for w in pm.warnings)

    # registry merge
    class FakeRegistry:
        def __init__(self):
            self.names_ = []
        def register_tool(self, tool):
            self.names_.append(tool.name)

    reg = FakeRegistry()
    assert pm.apply_to_registry(reg) == 1
    assert reg.names_ == ["my-tool"]

    pm.unregister_tool("my-tool")
    assert pm.list_tool_names() == []


def test_unload_removes_declared_tools(root):
    from agentkthx.core.models import Tool

    init_py = (
        "from agentkthx.core.models import Tool\n"
        "def register(manager):\n"
        "    manager.register_tool(Tool(name='plug-tool', description='d', params=[]), plugin='toolp')\n"
        "def unregister(manager):\n"
        "    manager.unregister_tool('plug-tool')\n"
    )
    write_plugin(
        root,
        "toolp",
        v02_manifest("toolp", provides={"tools": ["plug-tool"]}),
        init_py=init_py,
    )
    pm = make_manager(root)
    pm.load("toolp")
    assert pm.list_tool_names() == ["plug-tool"]
    pm.unload("toolp")
    assert pm.list_tool_names() == []


# ---------------------------------------------------------------------------
# Unload completeness (spec §Lifecycle / §Migration item 6)
# ---------------------------------------------------------------------------

def test_unload_purges_config(root):
    init_py = (
        "def register(manager):\n"
        "    manager.register_config_defaults('PURGE', {'PURGE_X': '1'}, plugin='purge')\n"
        "def unregister(manager):\n"
        "    pass\n"
    )
    write_plugin(root, "purge", v02_manifest("purge"), init_py=init_py)
    pm = make_manager(root)
    pm.load("purge")
    assert "PURGE_X" in pm.get_all_config_defaults()
    assert pm.unload("purge") is True
    assert "PURGE_X" not in pm.get_all_config_defaults()
    assert pm.get_config_defaults_by_prefix("PURGE") == {}


# ---------------------------------------------------------------------------
# PLUGIN_ROOT / PLUGIN_DATA (spec §Environment)
# ---------------------------------------------------------------------------

def test_plugin_root_and_data_dir_attributes(root):
    write_plugin(root, "paths", v02_manifest("paths"))
    pm = make_manager(root)
    plugin = pm.load("paths")
    assert plugin is not None
    assert plugin.root == root / "paths"
    assert plugin.data_dir is not None
    assert "agentkthx" in str(plugin.data_dir)
    assert plugin.data_dir.is_dir()
    assert pm.get_plugin_data_dir("paths") == plugin.data_dir


# ---------------------------------------------------------------------------
# Dependency resolution (spec §Dependency resolution)
# ---------------------------------------------------------------------------

def test_missing_dependency_skips_plugin(root):
    write_plugin(root, "orphan", v02_manifest("orphan"))
    raw = v02_manifest("dependent")
    raw["extensions"][EXT_NAMESPACE]["depends"] = ["orphan", "ghost"]
    write_plugin(root, "dependent", raw)
    pm = make_manager(root)
    loaded = pm.load_all()
    assert [p.manifest.name for p in loaded] == ["orphan"]
    assert any("missing dependency 'ghost'" in w for w in pm.warnings)


def test_circular_dependencies_rejected(root):
    a = v02_manifest("cyla")
    a["extensions"][EXT_NAMESPACE]["depends"] = ["cylb"]
    b = v02_manifest("cylb")
    b["extensions"][EXT_NAMESPACE]["depends"] = ["cyla"]
    write_plugin(root, "cyla", a)
    write_plugin(root, "cylb", b)
    pm = make_manager(root)
    assert pm.load_all() == []
    assert any("circular dependency" in w and "cyla" in w and "cylb" in w for w in pm.warnings)


def test_dependency_order_respected(root):
    base = v02_manifest("dep-base")
    top = v02_manifest("dep-top")
    top["extensions"][EXT_NAMESPACE]["depends"] = ["dep-base"]
    write_plugin(root, "dep-base", base)
    write_plugin(root, "dep-top", top)
    pm = make_manager(root)
    loaded = pm.load_all()
    assert [p.manifest.name for p in loaded] == ["dep-base", "dep-top"]


# ---------------------------------------------------------------------------
# on_init emission + failure isolation (spec §Lifecycle)
# ---------------------------------------------------------------------------

def test_on_init_emitted_once_after_load_all(root):
    init_py = (
        "seen = []\n"
        "def register(manager):\n"
        "    manager.register_hook('on_init', lambda ctx: seen.append(1), plugin='initp')\n"
        "def unregister(manager):\n"
        "    pass\n"
    )
    write_plugin(root, "initp", v02_manifest("initp"), init_py=init_py)
    pm = make_manager(root)
    pm.load_all()
    pm.load_all()  # second call must not re-emit
    import agentkthx_ext_plugins_initp as mod
    assert len(mod.seen) == 1


def test_backend_rejection_for_non_basebackend(root):
    init_py = (
        "class Fake:\n"
        "    pass\n"
        "def register(manager):\n"
        "    manager.register_backend('fake', Fake, plugin='badbackend')\n"
        "def unregister(manager):\n"
        "    manager.unregister_backend('fake')\n"
    )
    write_plugin(root, "badbackend", v02_manifest("badbackend", type_="backend"), init_py=init_py)
    pm = make_manager(root)
    pm.load("badbackend")
    assert "fake" not in pm.list_backend_names()
    assert any("not a BaseBackend subclass" in w for w in pm.warnings)


# ---------------------------------------------------------------------------
# Bundled plugins: v0.2 manifests parse and load (integration smoke)
# ---------------------------------------------------------------------------

def test_builtin_manifests_are_v02_form():
    plugins_dir = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins"
    manifests = []
    for entry in sorted(plugins_dir.iterdir()):
        mpath = entry / "plugin.json"
        if entry.is_dir() and mpath.exists():
            m = _parse_manifest(mpath, root_kind="builtin")
            manifests.append(m)
    names = {m.name for m in manifests}
    assert {"bitnet", "zai", "openrouter", "turboquant", "acp", "test-plugin"} <= names
    for m in manifests:
        assert m.schema == SCHEMA, f"{m.name} missing $schema"
        assert m.legacy_fields_used == [], f"{m.name} still uses legacy top-level fields"
        assert "agentnova" not in m.compatibility, f"{m.name} still uses agentnova key"
