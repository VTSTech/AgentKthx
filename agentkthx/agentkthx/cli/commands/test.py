"""`agentkthx test` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import os
import sys

from ...backends import get_backend
from ...colors import yellow, dim, green, cyan, bright_green, bright_cyan, red, bright_magenta
from ...config import get_config

from ..banner import print_banner
from ..utils import resolve_model_pattern




def cmd_test(args: argparse.Namespace) -> int:
    """Execute the test command."""
    # Available tests
    TESTS = {
        "00": {
            "name": "Basic Agent",
            "desc": "Simple conversation without tools",
            "module": "agentkthx.examples.00_basic_agent",
        },
        "01": {
            "name": "Quick Diagnostic",
            "desc": "5-question math reasoning test",
            "module": "agentkthx.examples.01_quick_diagnostic",
        },
        "02": {
            "name": "Tool Tests",
            "desc": "Calculator, shell, datetime tools",
            "module": "agentkthx.examples.02_tool_test",
        },
        "03": {
            "name": "Reasoning Test",
            "desc": "Multi-step reasoning challenges",
            "module": "agentkthx.examples.03_reasoning_test",
        },
        "04": {
            "name": "GSM8K Benchmark",
            "desc": "Grade school math problems",
            "module": "agentkthx.examples.04_gsm8k_benchmark",
        },
        "05": {
            "name": "Common Sense",
            "desc": "Everyday knowledge and reasoning (25 questions)",
            "module": "agentkthx.examples.05_common_sense",
        },
        "06": {
            "name": "Causal Reasoning",
            "desc": "Cause and effect understanding (25 questions)",
            "module": "agentkthx.examples.06_causal_reasoning",
        },
        "07": {
            "name": "Logical Deduction",
            "desc": "Syllogisms and logic puzzles (25 questions)",
            "module": "agentkthx.examples.07_logical_deduction",
        },
        "08": {
            "name": "Reading Comprehension",
            "desc": "Text understanding and inference (25 questions)",
            "module": "agentkthx.examples.08_reading_comprehension",
        },
        "09": {
            "name": "General Knowledge",
            "desc": "Geography, science, and facts (25 questions)",
            "module": "agentkthx.examples.09_general_knowledge",
        },
        "10": {
            "name": "Implicit Reasoning",
            "desc": "Understanding implied meanings (25 questions)",
            "module": "agentkthx.examples.10_implicit_reasoning",
        },
        "11": {
            "name": "Analogical Reasoning",
            "desc": "Pattern and relationship mapping (25 questions)",
            "module": "agentkthx.examples.11_analogical_reasoning",
        },
    }
    
    # List tests
    if args.list:
        print(f"\n{bright_cyan('⚛ AgentKthx')} - Available Tests")
        print(dim("-" * 50))
        for tid, info in TESTS.items():
            print(f"  {cyan(tid)}  {info['name']:<20} {dim(info['desc'])}")
        print(dim("-" * 50))
        print(f"\n  Usage: {cyan('agentkthx test 01')} or {cyan('agentkthx test all')}")
        return 0
    
    # Determine which tests to run
    test_id = args.test_id.lower()
    if test_id == "all":
        tests_to_run = list(TESTS.keys())
    elif test_id in TESTS:
        tests_to_run = [test_id]
    else:
        print(f"{red('Error:')} Unknown test '{test_id}'")
        print(f"  Run {cyan('agentkthx test --list')} to see available tests")
        return 1
    
    # Check backend
    config = get_config()
    backend_name = args.backend or config.backend
    api_mode = getattr(args, 'api_mode', 'openre')
    timeout = getattr(args, 'timeout', None)
    backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
    
    if not backend.is_running():
        print(f"{red('Error:')} {backend_name.capitalize()} not running at {backend.base_url}")
        if backend_name == "ollama":
            print(f"  Start with: {cyan('ollama serve')}")
            print(f"  Or set OLLAMA_BASE_URL to your remote server")
        return 1
    
    # Initialize ACP plugin if requested
    acp = None
    if args.acp:
        try:
            from ...plugins.acp.acp_plugin import ACPPlugin
            acp_url = args.acp_url or config.acp_base_url
            acp = ACPPlugin(
                base_url=acp_url,
                agent_name="AgentKthx-Test",
                model_name=args.model or config.default_model,
                debug=args.debug,
            )
            # Bootstrap ACP connection
            bootstrap_result = acp.bootstrap()
            if bootstrap_result.get("stop_flag"):
                print(f"{red('Error:')} ACP STOP flag is set: {bootstrap_result.get('warnings')}")
                return 1
            acp_enabled = True
        except ImportError:
            print(f"{yellow('Warning:')} ACP plugin not available, continuing without ACP logging")
            acp = None
        except Exception as e:
            print(f"{yellow('Warning:')} Failed to connect to ACP: {e}")
            acp = None
    
    # Set environment for tests
    if args.debug:
        os.environ["AGENTKTHX_DEBUG"] = "1"
    if args.backend:
        os.environ["AGENTKTHX_BACKEND"] = args.backend
    if getattr(args, 'num_ctx', None):
        os.environ["AGENTKTHX_NUM_CTX"] = str(args.num_ctx)
    
    # Reload config to pick up new env vars
    config = get_config(reload=True)
    
    # Resolve model pattern to actual model(s)
    model_pattern = args.model

    # BitNet model discovery: when --backend bitnet without --model,
    # discover the actual model name from the server via list_models().
    # This avoids using the generic "bitnet-b1.58-2b-4t" placeholder and
    # ensures correct family config resolution (stop tokens, prompt format).
    # Mirrors the same logic in _build_agent().
    if not model_pattern and backend_name == "bitnet":
        try:
            discovered = backend.list_models()
            if (discovered and discovered[0].get("name")
                    and discovered[0]["name"] not in ("bitnet", "default")):
                model_pattern = discovered[0]["name"]
                if args.debug:
                    print(f"  [bitnet] Discovered model: {model_pattern}")
        except Exception:
            pass  # Fall through to config.default_model

    if model_pattern:
        models_to_test = resolve_model_pattern(model_pattern, backend_name, allow_multiple=True)
        if not models_to_test:
            return 1  # Error already printed
    else:
        models_to_test = [config.default_model]
    
    # Run tests
    print_banner()
    print(f"{bright_magenta('Test Runner')} — {len(tests_to_run)} test(s), {len(models_to_test)} model(s)")
    print(f"{dim('Backend:')} {backend_name} ({backend.base_url})")
    if len(models_to_test) == 1:
        print(f"{dim('Model:')} {cyan(models_to_test[0])}")
    else:
        print(f"{dim('Models:')} {cyan(str(len(models_to_test)))} matching '{model_pattern}'")
    num_ctx_val = getattr(args, 'num_ctx', None) if getattr(args, 'num_ctx', None) is not None else config.num_ctx
    if num_ctx_val:
        ctx_display = f"{num_ctx_val // 1024}K" if num_ctx_val >= 1024 else str(num_ctx_val)
        print(f"{dim('Context:')} {yellow(ctx_display)}")
    if acp:
        print(f"{dim('ACP:')} {green('✓ Connected')} ({acp.base_url})")
    print()
    
    # Track results per model
    all_results = {}  # model -> {test_id -> result}
    
    for model in models_to_test:
        model_results = {}
        print(f"\n{dim('═' * 50)}")
        print(f"{bright_magenta('Model:')} {cyan(model)}")
        print(dim("═" * 50))
        
        # Set model env var for this run
        os.environ["AGENTKTHX_MODEL"] = model
        
        for tid in tests_to_run:
            info = TESTS[tid]
            print(f"\n{dim('─' * 50)}")
            print(f"{cyan(f'[{tid}]')} {bright_magenta(info['name'])}")
            print(f"{dim(info['desc'])}")
            print(dim("─" * 50))
            
            # Log test start to ACP
            if acp:
                acp.log_chat("user", f"[{model}] Starting test: {info['name']}")
            
            try:
                # Import and run the test module
                import importlib
                module = importlib.import_module(info["module"])
                
                # Build argv for test modules (they have their own argparse)
                test_argv = ["-m", model]
                if args.debug:
                    test_argv.append("--debug")
                if args.backend:
                    test_argv.extend(["--backend", args.backend])
                if getattr(args, 'api_mode', 'openre') != 'openre':
                    test_argv.extend(["--api", args.api_mode])
                if getattr(args, 'force_react', False):
                    test_argv.append("--force-react")
                if getattr(args, 'use_modelfile_system', False):
                    test_argv.append("--use-mf-sys")
                if getattr(args, 'soul', None):
                    test_argv.extend(["--soul", args.soul])
                    test_argv.extend(["--soul-level", str(getattr(args, 'soul_level', 2))])
                if getattr(args, 'timeout', None):
                    test_argv.extend(["--timeout", str(args.timeout)])
                if getattr(args, 'warmup', False):
                    test_argv.append("--warmup")
                if getattr(args, 'num_ctx', None) is not None:
                    test_argv.extend(["--num-ctx", str(args.num_ctx)])
                if getattr(args, 'num_predict', None) is not None:
                    test_argv.extend(["--num-predict", str(args.num_predict)])
                if getattr(args, 'temperature', None) is not None:
                    test_argv.extend(["--temp", str(args.temperature)])
                if getattr(args, 'top_p', None) is not None:
                    test_argv.extend(["--top-p", str(args.top_p)])
                if getattr(args, 'tools_only', False):
                    test_argv.append("--tools-only")
                if getattr(args, 'model_only', False):
                    test_argv.append("--model-only")
                if getattr(args, 'quick', False):
                    test_argv.append("--quick")
                
                # Override sys.argv for the test module's argparse
                old_argv = sys.argv
                sys.argv = ["test"] + test_argv
                
                try:
                    result = module.main()
                finally:
                    sys.argv = old_argv
                
                # Handle both old-style exit code and new-style result dict
                if isinstance(result, dict):
                    # New-style: granular results
                    passed = result.get("passed", 0)
                    total = result.get("total", 1)
                    time_s = result.get("time", 0)
                    exit_code = result.get("exit_code", 0)
                    model_results[tid] = {
                        "passed": exit_code == 0,
                        "exit_code": exit_code,
                        "granular": f"{passed}/{total}",
                        "time": time_s,
                    }
                else:
                    # Old-style: just exit code
                    exit_code = result if result is not None else 1
                    model_results[tid] = {"passed": exit_code == 0, "exit_code": exit_code}
                
                # Log test result to ACP
                if acp:
                    status = "passed" if exit_code == 0 else "failed"
                    granular = model_results[tid].get("granular", "")
                    acp.log_chat("assistant", f"[{model}] Test {info['name']}: {status} {granular}")
                
            except ImportError as e:
                print(f"{red('Error:')} Could not import test module: {e}")
                model_results[tid] = {"passed": False, "error": str(e)}
                if acp:
                    acp.log_chat("assistant", f"[{model}] Test {info['name']}: import error - {e}")
            except Exception as e:
                print(f"{red('Error:')} {e}")
                model_results[tid] = {"passed": False, "error": str(e)}
                if acp:
                    acp.log_chat("assistant", f"[{model}] Test {info['name']}: error - {e}")
        
        all_results[model] = model_results
    
    # Summary
    print(f"\n{dim('=' * 50)}")
    print(f"{bright_magenta('Test Summary')}")
    print(dim("=" * 50))
    
    # If multiple models, show per-model summary
    if len(models_to_test) > 1:
        print(f"\n{bright_magenta('Results by Model:')}")
        for model in models_to_test:
            model_results = all_results.get(model, {})
            passed = sum(1 for r in model_results.values() if r.get("passed"))
            total = len(model_results)
            granular_sum = ""
            # Sum up granular scores if available
            total_score = 0
            total_possible = 0
            total_time = 0
            for r in model_results.values():
                if "granular" in r:
                    try:
                        parts = r["granular"].split("/")
                        total_score += int(parts[0])
                        total_possible += int(parts[1])
                    except (ValueError, IndexError):
                        pass
                total_time += r.get("time", 0)
            
            if total_possible > 0:
                granular_sum = f"  {cyan(f'{total_score}/{total_possible}')}"
            time_str = f"  {dim(f'({total_time:.1f}s)')}" if total_time else ""
            status = bright_green("✓") if passed == total else red("✗")
            print(f"  {status} {cyan(model):<30} {passed}/{total}{granular_sum}{time_str}")
    else:
        # Single model - show per-test breakdown
        model = models_to_test[0]
        model_results = all_results.get(model, {})
        
        for tid, result in model_results.items():
            status = bright_green("✓ PASS") if result.get("passed") else red("✗ FAIL")
            granular = result.get("granular", "")
            time_s = result.get("time", 0)
            
            # Show granular results if available
            if granular:
                time_str = f" ({time_s:.1f}s)" if time_s else ""
                print(f"  [{tid}] {TESTS[tid]['name']:<20} {status}  {cyan(granular)}{time_str}")
            else:
                print(f"  [{tid}] {TESTS[tid]['name']:<20} {status}")
        
        print(dim("-" * 50))
        passed = sum(1 for r in model_results.values() if r.get("passed"))
        total = len(model_results)
        pct = 100 * passed // total if total > 0 else 0
        print(f"  {bright_green(str(passed))}/{total} tests passed ({pct}%)")
    
    # Log final summary to ACP and unregister (don't shutdown the server!)
    if acp:
        total_passed = sum(
            1 for model_results in all_results.values() 
            for r in model_results.values() if r.get("passed")
        )
        total_tests = sum(len(mr) for mr in all_results.values())
        acp.log_chat("assistant", f"All tests complete: {total_passed}/{total_tests} passed")
        # Only unregister from A2A, don't shutdown the ACP server
        acp.a2a_unregister()
        acp._log("Test complete, unregistered from A2A (ACP server remains running)")
    
    # Return success only if all tests passed
    total_passed = sum(
        1 for model_results in all_results.values() 
        for r in model_results.values() if r.get("passed")
    )
    total_tests = sum(len(mr) for mr in all_results.values())
    return 0 if total_passed == total_tests else 1
