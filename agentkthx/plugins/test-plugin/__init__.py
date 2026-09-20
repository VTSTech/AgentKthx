"""
AgentKthx Plugin — Plugin System Test

Validates discovery, loading, backend registration, CLI commands,
config defaults, and lifecycle (register/unregister).

Written by Super-Z (GLM-5)
"""

from __future__ import annotations

_registered = False


def register(manager) -> None:
    """
    Register all test plugin features.

    - Registers a test backend
    - Registers a plugin-test CLI command
    - Registers config defaults
    """
    global _registered
    _registered = True

    # Register test backend
    from .test_backend import TestBackend
    manager.register_backend("test-backend", TestBackend)

    # Register CLI command
    def handler(args):
        print("\n" + "=" * 50)
        print("  Plugin System Validation")
        print("=" * 50)

        # 1. Discovery
        manifests = manager.discover()
        names = [m.name for m in manifests]
        print(f"\n  Discovery: {len(names)} plugin(s) found")
        for n in sorted(names):
            print(f"    - {n}")

        # 2. Backend registration
        cls = manager.get_backend_class("test-backend")
        print(f"\n  Backend:  test-backend -> {'OK' if cls else 'FAIL'}")
        print(f"  Backends: {manager.list_backend_names()}")

        # 3. Backend choices (includes core)
        choices = manager.get_backend_choices()
        has_test = "test-backend" in choices
        print(f"\n  CLI --backend choices: {len(choices)} options")
        print(f"  test-backend in choices: {'OK' if has_test else 'FAIL'}")

        # 4. Config defaults
        defaults = manager.get_all_config_defaults()
        test_defaults = manager.get_config_defaults_by_prefix("TEST_PLUGIN")
        print(f"\n  Config defaults (all): {len(defaults)} key(s)")
        print(f"  Config defaults (TEST_PLUGIN): {test_defaults}")

        # 5. Plugin state
        loaded = manager.is_loaded("test-plugin")
        info = manager.get_plugin_info("test-plugin")
        print(f"\n  Plugin loaded: {'OK' if loaded else 'FAIL'}")
        if info:
            print(f"  Name:    {info.name}")
            print(f"  Version: {info.version}")
            print(f"  Type:    {info.type}")
            print(f"  Author:  {info.author}")

        # 6. Dependency resolution
        ordered = manager._resolve_load_order(manifests)
        print(f"\n  Load order: {' -> '.join(m.name for m in ordered)}")

        print(f"\n  Overall: {'PASS' if loaded and has_test and cls else 'FAIL'}")
        print("=" * 50 + "\n")

    manager.register_cli_command("plugin-test", handler)

    # Register config defaults explicitly
    manager.register_config_defaults("TEST_PLUGIN", {
        "TEST_PLUGIN_VALUE": "hello from plugin",
        "TEST_PLUGIN_NUMBER": "42",
    })


def unregister(manager) -> None:
    """Unregister all test plugin features."""
    global _registered
    _registered = False

    manager.unregister_backend("test-backend")
    manager.unregister_cli_command("plugin-test")
