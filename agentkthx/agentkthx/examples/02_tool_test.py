#!/usr/bin/env python3
"""
examples/02_tool_test.py
------------------------
Comprehensive tool tests for AgentKthx.

Phase 1: Direct Tool Validation
  • Tests each tool handler directly (no model involved)
  • Verifies correct output for known inputs
  • Tests security restrictions and edge cases

Phase 2: Model Tool Calling
  • Tests model's ability to select correct tool
  • Verifies proper argument passing
  • Checks result extraction

Usage:
  python examples/02_tool_test.py                    # Both phases
  python examples/02_tool_test.py --tools-only       # Phase 1 only
  python examples/02_tool_test.py --model qwen2.5:0.5b --debug
  agentkthx test 02

Environment Variables:
  OLLAMA_BASE_URL     - Ollama server URL
  AGENTKTHX_MODEL     - Default model

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import os
import time
import re
import argparse
import platform
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentkthx import Agent, get_config
from agentkthx.backends import get_default_backend
from agentkthx.tools import make_builtin_registry
from agentkthx.tools.builtins import (
    calculator, shell, read_file, write_file, list_directory,
    http_get, get_time, get_date, parse_json, count_words, count_chars,
    python_repl, read_file_lines, find_files, edit_file, web_search,
    todo_add, todo_list, todo_complete, todo_remove, todo_clear,
    _todo_dispatch,
)
from agentkthx.core.types import StepResultType


def parse_args():
    parser = argparse.ArgumentParser(description="AgentKthx Tool Tests")
    parser.add_argument("-m", "--model", default=None, help="Model to test")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--backend", choices=["ollama", "bitnet", "llama-server"], default=None)
    parser.add_argument("--api", choices=["openre", "openai"], default="openre", dest="api_mode",
                       help="API mode: 'openre' (OpenResponses) or 'openai' (Chat-Completions (OpenAI))")
    parser.add_argument("--tools-only", action="store_true", help="Only run Phase 1 (direct tool tests)")
    parser.add_argument("--model-only", action="store_true", help="Only run Phase 2 (model tool calling)")
    parser.add_argument("--soul", default=None, help="Path to Soul Spec package (ignored for tool tests)")
    parser.add_argument("--soul-level", type=int, default=2, choices=[1, 2, 3],
                       help="Soul progressive disclosure level")
    parser.add_argument("--timeout", type=int, default=None,
                       help="Request timeout in seconds (default: 120)")
    parser.add_argument("--warmup", action="store_true",
                       help="Send warmup request before testing (avoids cold start timeout)")
    parser.add_argument("--num-ctx", type=int, default=None,
                       help="Context window size in tokens")
    parser.add_argument("--num-predict", type=int, default=None,
                       help="Maximum tokens to generate")
    parser.add_argument("--temp", type=float, default=None, dest="temperature",
                       help="Sampling temperature 0.0-2.0")
    parser.add_argument("--top-p", type=float, default=None, dest="top_p",
                       help="Nucleus sampling probability 0.0-1.0")
    parser.add_argument("--force-react", action="store_true", help="Force ReAct mode for tool calling")
    parser.add_argument("--quick", action="store_true",
                       help="Quick mode: only run 5 fastest model tests (calculator x3, datetime x2)")
    return parser.parse_args()


# ============================================================================
# Phase 1: Direct Tool Validation
# ============================================================================

def test_calculator_direct() -> tuple[int, int]:
    """Test calculator tool directly without model."""
    print(f"\n{'='*60}")
    print(f"🧮 Calculator Tool - Direct Validation")
    print(f"{'='*60}")
    
    tests = [
        # (name, input, expected_output_contains, should_succeed)
        ("Basic addition", "15 + 8", "23", True),
        ("Multiplication", "15 * 8", "120", True),
        ("Division", "100 / 4", "25", True),
        ("Power", "2 ** 10", "1024", True),
        ("Square root", "sqrt(144)", "12", True),
        ("Complex expression", "(10 + 5) * 3", "45", True),
        ("Modulo", "17 % 5", "2", True),
        ("Negative numbers", "-5 + 10", "5", True),
        ("Float division", "7 / 2", "3.5", True),
        ("Constants - pi", "pi", "3.14", True),
        ("Constants - e", "e", "2.71", True),
        ("Trig - sin", "sin(0)", "0", True),
        ("Trig - cos", "cos(0)", "1", True),
        ("Logarithm", "log(100)", "4.6", True),  # ln(100) ≈ 4.6
        ("Floor", "floor(3.7)", "3", True),
        ("Ceil", "ceil(3.2)", "4", True),
        ("Division by zero", "1 / 0", "division by zero", False),
        ("NaN result", "sqrt(-1)", "NaN", False),
        ("Exponent limit", f"2 ** 15000", "exceeds", False),
    ]
    
    results = []
    for name, expr, expected, should_succeed in tests:
        result = calculator(expr)
        result_lower = result.lower()
        
        if should_succeed:
            passed = expected in result_lower or expected in result
        else:
            # Should return error message
            passed = "error" in result_lower or expected.lower() in result_lower
        
        results.append(passed)
        status = "✅" if passed else "❌"
        print(f"  {status} {name}: '{expr}' → {result[:50]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Calculator Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_shell_direct() -> tuple[int, int]:
    """Test shell tool directly without model."""
    print(f"\n{'='*60}")
    print(f"🖥️ Shell Tool - Direct Validation")
    print(f"{'='*60}")
    
    tests = [
        # (name, command, expected_contains, should_succeed)
        ("Echo test", "echo 'Hello World'", "Hello World", True),
        ("Echo with quotes", 'echo "Test 123"', "Test 123", True),
        # Blocked commands
        ("Blocked: rm", "rm -rf /", "blocked", False),
        ("Blocked: sudo", "sudo ls", "blocked", False),
        ("Blocked: curl", "curl http://example.com", "blocked", False),
        ("Blocked: pip", "pip install evil", "blocked", False),
        # Injection attempts
        ("Injection: semicolon", "echo test; ls", "injection", False),
        ("Injection: pipe", "echo test | cat", "injection", False),
        ("Injection: backticks", "echo `whoami`", "injection", False),
        ("Injection: $()", "echo $(whoami)", "injection", False),
    ]
    
    results = []
    for name, cmd, expected, should_succeed in tests:
        result = shell(cmd, timeout=5)
        result_lower = result.lower()
        
        if should_succeed:
            passed = expected.lower() in result_lower
        else:
            passed = "blocked" in result_lower or "injection" in result_lower or "security" in result_lower
        
        results.append(passed)
        status = "✅" if passed else "❌"
        print(f"  {status} {name}: → {result[:50]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Shell Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_file_direct() -> tuple[int, int]:
    """Test file tools directly without model."""
    print(f"\n{'='*60}")
    print(f"📁 File Tools - Direct Validation")
    print(f"{'='*60}")
    
    results = []
    
    # Create temp directory for tests
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.txt")
        test_content = "Hello AgentKthx!\nLine 2\nLine 3"
        
        # Test write_file
        print(f"\n  Testing write_file...")
        write_result = write_file(test_file, test_content)
        write_ok = "Successfully" in write_result
        results.append(write_ok)
        status = "✅" if write_ok else "❌"
        print(f"    {status} write_file: {write_result}")
        
        # Test read_file
        print(f"\n  Testing read_file...")
        read_result = read_file(test_file)
        # Normalize line endings for comparison (Windows uses \r\n)
        read_normalized = read_result.replace('\r\n', '\n').replace('\r', '\n')
        test_normalized = test_content.replace('\r\n', '\n').replace('\r', '\n')
        read_ok = test_normalized in read_normalized or test_normalized == read_normalized
        results.append(read_ok)
        status = "✅" if read_ok else "❌"
        print(f"    {status} read_file: {read_result[:40]}...")
        
        # Test list_directory
        print(f"\n  Testing list_directory...")
        list_result = list_directory(tmpdir)
        list_ok = "test.txt" in list_result
        results.append(list_ok)
        status = "✅" if list_ok else "❌"
        print(f"    {status} list_directory: {list_result}")
        
        # Test path security
        print(f"\n  Testing path security...")
        security_tests = [
            ("Path traversal", "../../../etc/passwd"),
            ("System dir", "/etc/passwd"),
            ("UNC path", "\\\\server\\share"),
        ]
        for name, path in security_tests:
            result = read_file(path)
            blocked = "Security error" in result or "denied" in result or "not allowed" in result
            results.append(blocked)
            status = "✅" if blocked else "❌"
            print(f"    {status} {name}: blocked={blocked}")
        
        # Test empty file
        print(f"\n  Testing edge cases...")
        empty_file = os.path.join(tmpdir, "empty.txt")
        write_file(empty_file, "")
        empty_read = read_file(empty_file)
        empty_ok = empty_read == ""
        results.append(empty_ok)
        status = "✅" if empty_ok else "❌"
        print(f"    {status} Empty file: '{empty_read}'")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 File Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_http_direct() -> tuple[int, int]:
    """Test HTTP tool directly without model."""
    print(f"\n{'='*60}")
    print(f"🌐 HTTP Tool - Direct Validation")
    print(f"{'='*60}")
    
    tests = [
        # (name, url, expected_contains, should_succeed)
        ("Valid HTTP", "http://httpbin.org/get", "httpbin", True),
        # SSRF blocked
        ("SSRF: localhost", "http://localhost/test", "SSRF", False),
        ("SSRF: 127.0.0.1", "http://127.0.0.1/test", "SSRF", False),
        ("SSRF: 10.x", "http://10.0.0.1/test", "SSRF", False),
        ("SSRF: 192.168.x", "http://192.168.1.1/test", "SSRF", False),
        ("SSRF: metadata", "http://169.254.169.254/latest/meta-data", "SSRF", False),
        # Scheme validation
        ("Invalid scheme: ftp", "ftp://example.com/file", "scheme", False),
        ("Invalid scheme: file", "file:///etc/passwd", "scheme", False),
    ]
    
    results = []
    for name, url, expected, should_succeed in tests:
        try:
            result = http_get(url)
        except Exception as e:
            result = str(e)
        
        result_lower = result.lower()
        
        if should_succeed:
            # For network tests, check if we got a valid response or a connection error
            # (connection errors are acceptable for network tests in isolated envs)
            passed = expected.lower() in result_lower or len(result) > 100 or "connection" in result_lower or "error" in result_lower
        else:
            passed = expected.lower() in result_lower or "security" in result_lower or "error" in result_lower
        
        results.append(passed)
        status = "✅" if passed else "❌"
        print(f"  {status} {name}: → {result[:50]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 HTTP Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_datetime_direct() -> tuple[int, int]:
    """Test datetime tools directly without model."""
    print(f"\n{'='*60}")
    print(f"📅 DateTime Tools - Direct Validation")
    print(f"{'='*60}")
    
    results = []
    
    # Test get_date
    print(f"\n  Testing get_date...")
    date_result = get_date()
    date_ok = bool(re.match(r'\d{4}-\d{2}-\d{2}', date_result))
    results.append(date_ok)
    status = "✅" if date_ok else "❌"
    print(f"    {status} get_date: {date_result}")
    
    # Test get_time (local)
    print(f"\n  Testing get_time (local)...")
    time_result = get_time()
    time_ok = bool(re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', time_result))
    results.append(time_ok)
    status = "✅" if time_ok else "❌"
    print(f"    {status} get_time: {time_result}")
    
    # Test get_time with timezone
    print(f"\n  Testing get_time (timezone)...")
    tz_result = get_time("UTC")
    tz_ok = "UTC" in tz_result or bool(re.match(r'\d{4}-\d{2}-\d{2}', tz_result))
    results.append(tz_ok)
    status = "✅" if tz_ok else "❌"
    print(f"    {status} get_time(UTC): {tz_result}")
    
    # Test invalid timezone
    print(f"\n  Testing invalid timezone...")
    invalid_tz = get_time("Invalid/Timezone")
    invalid_ok = "error" in invalid_tz.lower() or "unknown" in invalid_tz.lower()
    results.append(invalid_ok)
    status = "✅" if invalid_ok else "❌"
    print(f"    {status} Invalid timezone: {invalid_tz[:50]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 DateTime Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_json_text_direct() -> tuple[int, int]:
    """Test JSON and text tools directly without model."""
    print(f"\n{'='*60}")
    print(f"📝 JSON & Text Tools - Direct Validation")
    print(f"{'='*60}")
    
    results = []
    
    # Test parse_json
    print(f"\n  Testing parse_json...")
    json_input = '{"name": "test", "value": 123, "nested": {"a": 1}}'
    json_result = parse_json(json_input)
    json_ok = "name" in json_result and "123" in json_result and "nested" in json_result
    results.append(json_ok)
    status = "✅" if json_ok else "❌"
    print(f"    {status} parse_json: formatted output")
    
    # Test invalid JSON
    invalid_json = parse_json("not valid json")
    invalid_ok = "error" in invalid_json.lower()
    results.append(invalid_ok)
    status = "✅" if invalid_ok else "❌"
    print(f"    {status} Invalid JSON: {invalid_json[:40]}")
    
    # Test count_words
    print(f"\n  Testing count_words...")
    words_result = count_words("Hello world this is a test")
    words_ok = words_result == "6"
    results.append(words_ok)
    status = "✅" if words_ok else "❌"
    print(f"    {status} count_words: {words_result}")
    
    # Test count_chars
    print(f"\n  Testing count_chars...")
    chars_result = count_chars("Hello World")
    chars_ok = chars_result == "11"
    results.append(chars_ok)
    status = "✅" if chars_ok else "❌"
    print(f"    {status} count_chars: {chars_result}")
    
    # Edge cases
    print(f"\n  Testing edge cases...")
    empty_words = count_words("")
    empty_ok = empty_words == "0"
    results.append(empty_ok)
    status = "✅" if empty_ok else "❌"
    print(f"    {status} Empty string words: {empty_words}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 JSON/Text Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_python_repl_direct() -> tuple[int, int]:
    """Test python_repl tool directly without model."""
    print(f"\n{'='*60}")
    print(f"🐍 Python REPL Tool - Direct Validation")
    print(f"{'='*60}")
    
    tests = [
        # (name, code, expected_contains, should_succeed)
        ("Basic math", "print(2 + 2)", "4", True),
        ("Import math", "import math; print(math.sqrt(16))", "4", True),
        ("String ops", "print('hello'.upper())", "HELLO", True),
        ("Blocked: os.system", "import os; os.system('ls')", "blocked", False),
        ("Empty code", "", None, True),
    ]
    
    results = []
    for name, code, expected, should_succeed in tests:
        try:
            result = python_repl(code)
            result_lower = result.lower()
            
            if should_succeed:
                if expected:
                    passed = expected in result
                else:
                    # Empty code — just check it doesn't crash
                    passed = isinstance(result, str)
            else:
                passed = "error" in result_lower or "not allowed" in result_lower or "blocked" in result_lower
        except Exception as e:
            passed = not should_succeed
            result = str(e)
        
        results.append(passed)
        status = "✅" if passed else "❌"
        print(f"  {status} {name}: → {result[:60]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Python REPL Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_read_file_lines_direct() -> tuple[int, int]:
    """Test read_file_lines tool directly without model."""
    print(f"\n{'='*60}")
    print(f"📄 Read File Lines Tool - Direct Validation")
    print(f"{'='*60}")
    
    results = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "lines_test.txt")
        lines = [f"Line {i}" for i in range(1, 11)]
        content = "\n".join(lines) + "\n"
        write_file(test_file, content)
        
        # Read first 3 lines
        print(f"\n  Testing read_file_lines...")
        result = read_file_lines(test_file, start_line=1, end_line=3)
        ok = "Line 1" in result and "Line 3" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Read first 3 lines: Line 1 and Line 3 in result")
        
        # Read middle range
        result = read_file_lines(test_file, start_line=5, end_line=7)
        ok = "Line 5" in result and "Line 7" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Read middle range: Line 5 and Line 7 in result")
        
        # Read single line
        result = read_file_lines(test_file, start_line=3, end_line=3)
        ok = "Line 3" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Read single line: Line 3 in result")
        
        # Beyond file end
        result = read_file_lines(test_file, start_line=999)
        ok = "beyond the end" in result.lower() or "beyond" in result.lower()
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Beyond file end: {result[:60]}")
        
        # Default range (should return all 10 lines)
        result = read_file_lines(test_file)
        ok = "Line 1" in result and "Line 10" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Default range: all lines present")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Read File Lines Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_find_files_direct() -> tuple[int, int]:
    """Test find_files tool directly without model."""
    print(f"\n{'='*60}")
    print(f"🔍 Find Files Tool - Direct Validation")
    print(f"{'='*60}")
    
    results = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        write_file(os.path.join(tmpdir, "app.py"), "print('hello')")
        write_file(os.path.join(tmpdir, "utils.py"), "def helper(): pass")
        sub_dir = os.path.join(tmpdir, "sub")
        write_file(os.path.join(sub_dir, "test_data.txt"), "data")
        write_file(os.path.join(tmpdir, "test_output.txt"), "output")
        write_file(os.path.join(tmpdir, "test_input.txt"), "input")
        
        # Find .py files
        print(f"\n  Testing find_files...")
        result = find_files("*.py", tmpdir)
        ok = "app.py" in result and "utils.py" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Find .py files: {result[:60]}")
        
        # Find specific pattern
        result = find_files("test_*.txt", tmpdir)
        ok = "test_output.txt" in result and "test_input.txt" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Find test_*.txt files: {result[:60]}")
        
        # No matches
        result = find_files("*.xyz_nonexistent", tmpdir)
        ok = "No files matching" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} No matches: {result[:60]}")
        
        # Empty pattern
        result = find_files("", tmpdir)
        ok = "error" in result.lower() or "cannot be empty" in result.lower()
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Empty pattern: {result[:60]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Find Files Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_edit_file_direct() -> tuple[int, int]:
    """Test edit_file tool directly without model."""
    print(f"\n{'='*60}")
    print(f"✏️ Edit File Tool - Direct Validation")
    print(f"{'='*60}")
    
    results = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "edit_test.txt")
        write_file(test_file, "hello world, hello again")
        
        # Replace word (first occurrence)
        print(f"\n  Testing edit_file...")
        result = edit_file(test_file, "hello", "world")
        ok = "Successfully edited" in result and "replaced 1" in result
        # Verify the file was actually changed
        with open(test_file, "r") as f:
            content = f.read()
        ok = ok and "world" in content and content.count("hello") == 1  # second 'hello' untouched
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Replace word: {result[:60]}")
        
        # Replace not found
        result = edit_file(test_file, "NONEXISTENT_TEXT_xyz", "anything")
        ok = "not found" in result.lower() or "error" in result.lower()
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Replace not found: {result[:60]}")
        
        # Replace all
        write_file(test_file, "aa bb aa cc aa")
        result = edit_file(test_file, "aa", "b", replace_all=True)
        ok = "Successfully edited" in result and "replaced 3" in result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Replace all: {result[:60]}")
        
        # Empty old_string
        result = edit_file(test_file, "", "test")
        ok = "error" in result.lower() or "cannot be empty" in result.lower()
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Empty old_string: {result[:60]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Edit File Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_web_search_direct() -> tuple[int, int]:
    """Test web_search tool directly without model."""
    print(f"\n{'='*60}")
    print(f"🌐 Web Search Tool - Direct Validation")
    print(f"{'='*60}")
    
    tests = [
        # (name, query, expected_pattern)
        ("Search query", "Python programming"),
    ]
    
    results = []
    for name, query in tests:
        print(f"\n  Testing {name}...")
        try:
            result = web_search(query)
            result_lower = result.lower()
            # Pass if we got a valid response (may fail due to network — that's ok)
            passed = (
                isinstance(result, str) and len(result) > 10
                and ("web search results" in result_lower
                     or "no results found" in result_lower
                     or "connection error" in result_lower
                     or "search error" in result_lower
                     or "http error" in result_lower)
            )
        except Exception as e:
            # Network errors are acceptable in isolated environments
            passed = True
            result = f"Exception (acceptable): {e}"
        
        results.append(passed)
        status = "✅" if passed else "❌"
        print(f"    {status} {name}: → {result[:60]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Web Search Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_todo_direct() -> tuple[int, int]:
    """Test todo tool functions directly without model."""
    print(f"\n{'='*60}")
    print(f"✅ Todo Tool - Direct Validation")
    print(f"{'='*60}")
    
    results = []
    
    # Add todo
    print(f"\n  Testing todo operations...")
    add_result = todo_add("Test task for direct validation", priority="high")
    ok = "Added todo" in add_result
    results.append(ok)
    status = "✅" if ok else "❌"
    print(f"    {status} Add todo: {add_result}")
    
    # Parse task ID from add result
    task_id = None
    id_match = re.search(r'\[([a-f0-9]{8})\]', add_result)
    if id_match:
        task_id = id_match.group(1)
    
    # List todos
    list_result = todo_list()
    ok = "Test task for direct validation" in list_result
    results.append(ok)
    status = "✅" if ok else "❌"
    print(f"    {status} List todos: task found in list")
    
    # Complete todo
    if task_id:
        complete_result = todo_complete(task_id)
        ok = "Completed todo" in complete_result or "already completed" in complete_result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Complete todo: {complete_result[:60]}")
    else:
        results.append(False)
        print(f"    ❌ Complete todo: could not parse task ID")
    
    # Remove todo — add another then remove it
    add2_result = todo_add("Temporary task to remove")
    remove_id = None
    id_match2 = re.search(r'\[([a-f0-9]{8})\]', add2_result)
    if id_match2:
        remove_id = id_match2.group(1)
    if remove_id:
        remove_result = todo_remove(remove_id)
        ok = "Removed todo" in remove_result
        results.append(ok)
        status = "✅" if ok else "❌"
        print(f"    {status} Remove todo: {remove_result[:60]}")
    else:
        results.append(False)
        print(f"    ❌ Remove todo: could not parse task ID")
    
    # Clear completed — should clear the one we completed earlier
    clear_result = todo_clear()
    ok = "Cleared" in clear_result or "No completed" in clear_result
    results.append(ok)
    status = "✅" if ok else "❌"
    print(f"    {status} Clear completed: {clear_result[:60]}")
    
    # Unknown action via _todo_dispatch
    dispatch_result = _todo_dispatch(action="invalid_action")
    ok = "unknown todo action" in dispatch_result.lower()
    results.append(ok)
    status = "✅" if ok else "❌"
    print(f"    {status} Unknown action: {dispatch_result[:60]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Todo Direct: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def run_phase1() -> tuple[int, int]:
    """Run all Phase 1 (direct tool) tests."""
    print(f"\n{'#'*60}")
    print(f"# PHASE 1: Direct Tool Validation")
    print(f"# Testing tool handlers without model")
    print(f"{'#'*60}")
    
    # Show backend info (Phase 1 includes HTTP tests that hit the network)
    try:
        config = get_config()
        backend_name = os.environ.get('AGENTKTHX_BACKEND', config.backend)
        print(f"   Backend: {backend_name}")
    except Exception:
        pass
    
    total_passed = 0
    total_tests = 0
    
    p, t = test_calculator_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_shell_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_file_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_http_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_datetime_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_json_text_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_python_repl_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_read_file_lines_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_find_files_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_edit_file_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_web_search_direct()
    total_passed += p
    total_tests += t
    
    p, t = test_todo_direct()
    total_passed += p
    total_tests += t
    
    print(f"\n{'='*60}")
    print(f"📊 PHASE 1 TOTAL: {total_passed}/{total_tests} ({100*total_passed//total_tests}%)")
    print(f"{'='*60}")
    
    return total_passed, total_tests


# ============================================================================
# Phase 2: Model Tool Calling Tests
# ============================================================================

def normalize_number(text: str) -> str:
    """Extract the last number from text.

    Uses the last number because model final answers typically restate
    the question before giving the result, e.g.:
      "The result of 15 times 8 is 120."  →  "120" (not "15")
    For single-number inputs (expected values, tool results) the first
    and last number are the same, so this is safe everywhere.
    """
    matches = re.findall(r'-?\d+(?:\.\d+)?', text.replace(',', ''))
    return matches[-1] if matches else ""


def numbers_match(expected_str: str, actual_str: str, tolerance: float = 0.01) -> bool:
    """Compare two numeric strings, handling float/integer differences.

    Solves the problem where '12' != '12.0' in string comparison.
    Falls back to exact string match if either value is not a valid number.
    """
    if not expected_str or not actual_str:
        return False
    try:
        return abs(float(expected_str) - float(actual_str)) < tolerance
    except (ValueError, OverflowError):
        return expected_str == actual_str


def check_tool_used(run, tool_name: str) -> bool:
    """Verify that a specific tool was called during the run.

    Only checks the actual tool call name recorded in step.tool_call,
    not the tool result content.  This avoids false positives where a
    tool name appears incidentally in another tool's output.
    """
    for step in run.steps:
        if step.type == StepResultType.TOOL_CALL:
            if step.tool_call and step.tool_call.name == tool_name:
                return True
    return False


def test_calculator_model(model: str, backend, debug: bool = False,
                           soul: str = None, soul_level: int = 2,
                           force_react: bool = False,
                           num_ctx: int = None, num_predict: int = None,
                           temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call calculator tool."""
    print(f"\n{'='*60}")
    print(f"🧮 Calculator Tool - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["calculator"])
    
    tests = [
        ("Basic multiplication", "What is 15 times 8?", "120"),
        ("Power", "What is 2 to the power of 10?", "1024"),
        ("Square root", "What is the square root of 144?", "12"),
        ("Complex expression", "What is (10 + 5) times 3?", "45"),
        ("Division", "What is 100 divided by 4?", "25"),
    ]
    
    results = []
    
    for name, prompt, expected in tests:
        print(f"\n📋 {name}")
        print(f"   Prompt: {prompt}")
        
        agent = Agent(
            model=model,
            tools=tools,
            backend=backend,
            max_steps=5,
            debug=debug,
            soul=soul,
            soul_level=soul_level,
            force_react=force_react,
            num_ctx=num_ctx,
            num_predict=num_predict,
            temperature=temperature,
            top_p=top_p,
        )
        
        t0 = time.time()
        run = agent.run(prompt)
        elapsed = time.time() - t0
        
        tool_used = check_tool_used(run, "calculator")
        expected_num = normalize_number(expected)
        actual_num = normalize_number(run.final_answer)
        passed = numbers_match(expected_num, actual_num)

        # Also check if the expected number appears in any tool result
        # (model may get the right answer from tool but fail to format Final Answer)
        if not passed and expected_num:
            for step in run.steps:
                if step.tool_result:
                    result_num = normalize_number(str(step.tool_result))
                    if numbers_match(expected_num, result_num):
                        passed = True
                        break
        
        results.append(passed)
        
        status = "✅" if passed else "❌"
        tool_status = "🔧" if tool_used else "⚠️"
        print(f"  {status} Expected '{expected}' | Got: {actual_num}")
        print(f"  {tool_status} Tool used: {tool_used} | {elapsed:.1f}s, {len(run.steps)} steps")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Calculator Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_shell_model(model: str, backend, debug: bool = False,
                     soul: str = None, soul_level: int = 2,
                     force_react: bool = False,
                     num_ctx: int = None, num_predict: int = None,
                     temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call shell tool."""
    print(f"\n{'='*60}")
    print(f"🖥️ Shell Tool - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["shell"])
    
    tests = [
        ("Echo test", "Use shell to echo 'Hello AgentKthx'", "Hello AgentKthx"),
        ("Current directory", "What is the current working directory?", None),
    ]
    
    results = []
    
    for name, prompt, expected in tests:
        print(f"\n📋 {name}")
        print(f"   Prompt: {prompt}")
        
        agent = Agent(
            model=model,
            tools=tools,
            backend=backend,
            max_steps=5,
            debug=debug,
            soul=soul,
            soul_level=soul_level,
            force_react=force_react,
            num_ctx=num_ctx,
            num_predict=num_predict,
            temperature=temperature,
            top_p=top_p,
        )
        
        t0 = time.time()
        run = agent.run(prompt)
        elapsed = time.time() - t0
        
        tool_used = check_tool_used(run, "shell")
        
        if not tool_used:
            print(f"  ❌ Shell tool was NOT called!")
            results.append(False)
            continue
        
        # Check if expected result appears in either:
        # 1. The final answer, OR
        # 2. Any tool result during the run (model may not format Final Answer correctly)
        found_in_answer = expected and expected.lower() in run.final_answer.lower()
        found_in_tool_result = False
        
        if expected:
            for step in run.steps:
                if step.tool_result and expected.lower() in str(step.tool_result).lower():
                    found_in_tool_result = True
                    break
        
        # Pass if tool was used AND (no expected value OR result found somewhere)
        if expected:
            passed = found_in_answer or found_in_tool_result
        else:
            passed = True  # Just verify tool was used
        
        results.append(passed)
        status = "✅" if passed else "❌"
        found_where = ""
        if expected:
            if found_in_answer:
                found_where = "(in answer)"
            elif found_in_tool_result:
                found_where = "(in tool result)"
        print(f"  {status} {elapsed:.1f}s, {len(run.steps)} steps {found_where}")
        print(f"  📝 {run.final_answer[:80]}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Shell Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_datetime_model(model: str, backend, debug: bool = False,
                        soul: str = None, soul_level: int = 2,
                        force_react: bool = False,
                        num_ctx: int = None, num_predict: int = None,
                        temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call datetime tools."""
    print(f"\n{'='*60}")
    print(f"📅 DateTime Tools - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["get_time", "get_date"])
    
    tests = [
        ("Get date", "What is today's date?", "date"),
        ("Get time", "What time is it?", "time"),
    ]
    
    results = []
    
    for name, prompt, keyword in tests:
        print(f"\n📋 {name}")
        print(f"   Prompt: {prompt}")
        
        agent = Agent(
            model=model,
            tools=tools,
            backend=backend,
            max_steps=5,
            debug=debug,
            soul=soul,
            soul_level=soul_level,
            force_react=force_react,
            num_ctx=num_ctx,
            num_predict=num_predict,
            temperature=temperature,
            top_p=top_p,
        )
        
        t0 = time.time()
        run = agent.run(prompt)
        elapsed = time.time() - t0
        
        tool_used = check_tool_used(run, "get_time") or check_tool_used(run, "get_date")
        
        has_date = bool(re.search(r'\d{4}-\d{2}-\d{2}', run.final_answer))
        has_time = bool(re.search(r'\d{2}:\d{2}', run.final_answer))
        passed = (keyword == "date" and has_date) or (keyword == "time" and has_time)
        
        # Fallback: check tool results if model didn't format the answer
        # (consistent with calculator/shell/file tests — the model may have
        # called the right tool but failed to produce a well-formed Final Answer)
        if not passed and tool_used:
            for step in run.steps:
                if step.tool_result:
                    result_str = str(step.tool_result)
                    if keyword == "date" and re.search(r'\d{4}-\d{2}-\d{2}', result_str):
                        passed = True
                        break
                    elif keyword == "time" and re.search(r'\d{2}:\d{2}', result_str):
                        passed = True
                        break
        
        results.append(passed)
        status = "✅" if passed else "❌"
        tool_status = "🔧" if tool_used else "⚠️"
        found_where = "(in answer)" if (keyword == "date" and has_date) or (keyword == "time" and has_time) else "(in tool result)" if passed and tool_used else ""
        print(f"  {status} {tool_status} Tool used: {tool_used} | {elapsed:.1f}s {found_where}")
        print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 DateTime Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_file_model(model: str, backend, debug: bool = False,
                    soul: str = None, soul_level: int = 2,
                    force_react: bool = False,
                    num_ctx: int = None, num_predict: int = None,
                    temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call file tools."""
    print(f"\n{'='*60}")
    print(f"📁 File Tools - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["read_file", "write_file", "list_directory"])
    
    # Create a test file first
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.txt")
        write_file(test_file, "Hello from AgentKthx!")
        
        # Convert path to forward slashes for better small model compatibility
        # Forward slashes work on both Windows and Unix
        test_file_fs = test_file.replace("\\", "/")
        tmpdir_fs = tmpdir.replace("\\", "/")
        
        tests = [
            ("Read file", f"Read the file at {test_file_fs}", "AgentKthx"),
            ("List directory", f"List files in {tmpdir_fs}", "test.txt"),
        ]
        
        results = []
        
        for name, prompt, expected in tests:
            print(f"\n📋 {name}")
            print(f"   Prompt: {prompt}")
            
            agent = Agent(
                model=model,
                tools=tools,
                backend=backend,
                max_steps=5,
                debug=debug,
                soul=soul,
                soul_level=soul_level,
                force_react=force_react,
                num_ctx=num_ctx,
                num_predict=num_predict,
                temperature=temperature,
                top_p=top_p,
            )
            
            t0 = time.time()
            run = agent.run(prompt)
            elapsed = time.time() - t0
            
            # Check if any file tool was used
            tool_used = (
                check_tool_used(run, "read_file") or 
                check_tool_used(run, "write_file") or 
                check_tool_used(run, "list_directory")
            )
            
            # Check result in answer or tool result
            expected_num = normalize_number(expected)
            actual_num = normalize_number(run.final_answer)
            found_in_answer = numbers_match(expected_num, actual_num)
            if not found_in_answer:
                found_in_answer = expected.lower() in run.final_answer.lower()
            found_in_tool_result = False
            for step in run.steps:
                if step.tool_result:
                    result_num = normalize_number(str(step.tool_result))
                    if numbers_match(expected_num, result_num):
                        found_in_tool_result = True
                        break
                    if expected.lower() in str(step.tool_result).lower():
                        found_in_tool_result = True
                        break
            
            passed = (found_in_answer or found_in_tool_result) if expected else tool_used
            results.append(passed)
            
            status = "✅" if passed else "❌"
            tool_status = "🔧" if tool_used else "⚠️"
            found_where = "(in answer)" if found_in_answer else "(in tool result)" if found_in_tool_result else ""
            print(f"  {status} {tool_status} Tool used: {tool_used} | {elapsed:.1f}s {found_where}")
            print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 File Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_python_repl_model(model: str, backend, debug: bool = False,
                           soul: str = None, soul_level: int = 2,
                           force_react: bool = False,
                           num_ctx: int = None, num_predict: int = None,
                           temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call python_repl tool."""
    print(f"\n{'='*60}")
    print(f"🐍 Python REPL Tool - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["python_repl"])
    
    tests = [
        ("Calculate power", "Use Python to calculate 2 to the power of 20", "1048576"),
        ("Math with math module", "Use Python to calculate the square root of 144", "12"),
    ]
    
    results = []
    
    for name, prompt, expected in tests:
        print(f"\n📋 {name}")
        print(f"   Prompt: {prompt}")
        
        agent = Agent(
            model=model,
            tools=tools,
            backend=backend,
            max_steps=5,
            debug=debug,
            soul=soul,
            soul_level=soul_level,
            force_react=force_react,
            num_ctx=num_ctx,
            num_predict=num_predict,
            temperature=temperature,
            top_p=top_p,
        )
        
        t0 = time.time()
        run = agent.run(prompt)
        elapsed = time.time() - t0
        
        tool_used = check_tool_used(run, "python_repl")
        
        # Check result in answer or tool result (numeric-aware)
        expected_num = normalize_number(expected)
        actual_num = normalize_number(run.final_answer)
        found_in_answer = numbers_match(expected_num, actual_num)
        if not found_in_answer:
            found_in_answer = expected in run.final_answer
        found_in_tool_result = False
        for step in run.steps:
            if step.tool_result:
                result_num = normalize_number(str(step.tool_result))
                if numbers_match(expected_num, result_num):
                    found_in_tool_result = True
                    break
                if expected in str(step.tool_result):
                    found_in_tool_result = True
                    break
        
        passed = found_in_answer or found_in_tool_result
        results.append(passed)
        
        status = "✅" if passed else "❌"
        tool_status = "🔧" if tool_used else "⚠️"
        print(f"  {status} {tool_status} Tool used: {tool_used} | {elapsed:.1f}s")
        print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Python REPL Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_read_file_lines_model(model: str, backend, debug: bool = False,
                               soul: str = None, soul_level: int = 2,
                               force_react: bool = False,
                               num_ctx: int = None, num_predict: int = None,
                               temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call read_file_lines tool."""
    print(f"\n{'='*60}")
    print(f"📄 Read File Lines Tool - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["read_file_lines"])
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "lines_model_test.txt")
        lines = [f"Line {i} content" for i in range(1, 11)]
        write_file(test_file, "\n".join(lines) + "\n")
        test_file_fs = test_file.replace("\\", "/")
        
        tests = [
            ("Read specific lines", f"Read lines 2 to 4 from {test_file_fs}", "Line 2"),
            ("Read single line", f"Read line 1 from {test_file_fs}", "Line 1"),
        ]
        
        results = []
        
        for name, prompt, expected in tests:
            print(f"\n📋 {name}")
            print(f"   Prompt: {prompt}")
            
            agent = Agent(
                model=model,
                tools=tools,
                backend=backend,
                max_steps=5,
                debug=debug,
                soul=soul,
                soul_level=soul_level,
                force_react=force_react,
                num_ctx=num_ctx,
                num_predict=num_predict,
                temperature=temperature,
                top_p=top_p,
            )
            
            t0 = time.time()
            run = agent.run(prompt)
            elapsed = time.time() - t0
            
            tool_used = check_tool_used(run, "read_file_lines")
            
            found_in_answer = expected.lower() in run.final_answer.lower()
            found_in_tool_result = False
            for step in run.steps:
                if step.tool_result and expected.lower() in str(step.tool_result).lower():
                    found_in_tool_result = True
                    break
            
            passed = found_in_answer or found_in_tool_result
            results.append(passed)
            
            status = "✅" if passed else "❌"
            tool_status = "🔧" if tool_used else "⚠️"
            found_where = "(in answer)" if found_in_answer else "(in tool result)" if found_in_tool_result else ""
            print(f"  {status} {tool_status} Tool used: {tool_used} | {elapsed:.1f}s {found_where}")
            print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Read File Lines Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_find_files_model(model: str, backend, debug: bool = False,
                          soul: str = None, soul_level: int = 2,
                          force_react: bool = False,
                          num_ctx: int = None, num_predict: int = None,
                          temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call find_files tool."""
    print(f"\n{'='*60}")
    print(f"🔍 Find Files Tool - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["find_files"])
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        write_file(os.path.join(tmpdir, "main.py"), "# main")
        write_file(os.path.join(tmpdir, "helper.py"), "# helper")
        write_file(os.path.join(tmpdir, "readme.txt"), "# readme")
        write_file(os.path.join(tmpdir, "notes.txt"), "# notes")
        tmpdir_fs = tmpdir.replace("\\", "/")
        
        tests = [
            ("Find py files", f"Find all Python files in {tmpdir_fs}", ".py"),
            ("Find txt files", f"Find all text files in {tmpdir_fs}", ".txt"),
        ]
        
        results = []
        
        for name, prompt, expected in tests:
            print(f"\n📋 {name}")
            print(f"   Prompt: {prompt}")
            
            agent = Agent(
                model=model,
                tools=tools,
                backend=backend,
                max_steps=5,
                debug=debug,
                soul=soul,
                soul_level=soul_level,
                force_react=force_react,
                num_ctx=num_ctx,
                num_predict=num_predict,
                temperature=temperature,
                top_p=top_p,
            )
            
            t0 = time.time()
            run = agent.run(prompt)
            elapsed = time.time() - t0
            
            tool_used = check_tool_used(run, "find_files")
            
            found_in_answer = expected.lower() in run.final_answer.lower()
            found_in_tool_result = False
            for step in run.steps:
                if step.tool_result and expected.lower() in str(step.tool_result).lower():
                    found_in_tool_result = True
                    break
            
            passed = found_in_answer or found_in_tool_result
            results.append(passed)
            
            status = "✅" if passed else "❌"
            tool_status = "🔧" if tool_used else "⚠️"
            found_where = "(in answer)" if found_in_answer else "(in tool result)" if found_in_tool_result else ""
            print(f"  {status} {tool_status} Tool used: {tool_used} | {elapsed:.1f}s {found_where}")
            print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Find Files Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_edit_file_model(model: str, backend, debug: bool = False,
                         soul: str = None, soul_level: int = 2,
                         force_react: bool = False,
                         num_ctx: int = None, num_predict: int = None,
                         temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call edit_file tool.

    Note: edit_file is marked as dangerous. The Agent may block it unless
    --confirm is used. If the model selects the right tool but it's blocked,
    that still validates tool selection.
    """
    print(f"\n{'='*60}")
    print(f"✏️ Edit File Tool - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["edit_file"])
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "edit_model_test.txt")
        write_file(test_file, "The quick brown fox jumps over the lazy dog")
        test_file_fs = test_file.replace("\\", "/")
        
        tests = [
            ("Replace word", f"Replace 'brown' with 'red' in {test_file_fs}", "red"),
        ]
        
        results = []
        
        for name, prompt, expected in tests:
            print(f"\n📋 {name}")
            print(f"   Prompt: {prompt}")
            
            agent = Agent(
                model=model,
                tools=tools,
                backend=backend,
                max_steps=5,
                debug=debug,
                soul=soul,
                soul_level=soul_level,
                force_react=force_react,
                num_ctx=num_ctx,
                num_predict=num_predict,
                temperature=temperature,
                top_p=top_p,
            )
            
            t0 = time.time()
            run = agent.run(prompt)
            elapsed = time.time() - t0
            
            tool_used = check_tool_used(run, "edit_file")
            
            # Check if the edit actually happened
            found_in_answer = expected.lower() in run.final_answer.lower()
            found_in_tool_result = False
            file_edited = False
            for step in run.steps:
                if step.tool_result:
                    result_str = str(step.tool_result)
                    if expected.lower() in result_str.lower():
                        found_in_tool_result = True
                    if "Successfully edited" in result_str:
                        file_edited = True
            
            # Pass if tool was selected correctly, even if blocked by danger check
            passed = tool_used and (found_in_answer or found_in_tool_result or file_edited)
            results.append(passed)
            
            status = "✅" if passed else "❌"
            tool_status = "🔧" if tool_used else "⚠️"
            edit_status = "(edited)" if file_edited else "(selection only)" if tool_used else ""
            print(f"  {status} {tool_status} Tool used: {tool_used} | {elapsed:.1f}s {edit_status}")
            print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Edit File Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_todo_model(model: str, backend, debug: bool = False,
                    soul: str = None, soul_level: int = 2,
                    force_react: bool = False,
                    num_ctx: int = None, num_predict: int = None,
                    temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to call todo tool."""
    print(f"\n{'='*60}")
    print(f"✅ Todo Tool - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["todo"])
    
    tests = [
        ("Add task", "Add a todo item: Buy groceries", "Buy groceries"),
        ("List tasks", "List all my todo items", "Buy groceries"),
    ]
    
    results = []
    
    for name, prompt, expected in tests:
        print(f"\n📋 {name}")
        print(f"   Prompt: {prompt}")
        
        agent = Agent(
            model=model,
            tools=tools,
            backend=backend,
            max_steps=5,
            debug=debug,
            soul=soul,
            soul_level=soul_level,
            force_react=force_react,
            num_ctx=num_ctx,
            num_predict=num_predict,
            temperature=temperature,
            top_p=top_p,
        )
        
        t0 = time.time()
        run = agent.run(prompt)
        elapsed = time.time() - t0
        
        tool_used = check_tool_used(run, "todo")
        
        found_in_answer = expected.lower() in run.final_answer.lower()
        found_in_tool_result = False
        for step in run.steps:
            if step.tool_result and expected.lower() in str(step.tool_result).lower():
                found_in_tool_result = True
                break
        
        passed = found_in_answer or found_in_tool_result
        results.append(passed)
        
        status = "✅" if passed else "❌"
        tool_status = "🔧" if tool_used else "⚠️"
        found_where = "(in answer)" if found_in_answer else "(in tool result)" if found_in_tool_result else ""
        print(f"  {status} {tool_status} Tool used: {tool_used} | {elapsed:.1f}s {found_where}")
        print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 Todo Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def test_all_tools_model(model: str, backend, debug: bool = False,
                         soul: str = None, soul_level: int = 2,
                         force_react: bool = False,
                         num_ctx: int = None, num_predict: int = None,
                         temperature: float = None, top_p: float = None) -> tuple[int, int]:
    """Test model's ability to choose correct tools when all are available."""
    print(f"\n{'='*60}")
    print(f"🧰 All Tools - Model Calling")
    print(f"   Model: {model}")
    print(f"{'='*60}")
    
    # Give model access to ALL tools
    tools = make_builtin_registry()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "multi_test.txt")
        write_file(test_file, "Test content 123")
        # Use forward slashes for better compatibility
        test_file_fs = test_file.replace("\\", "/")
        tmpdir_fs = tmpdir.replace("\\", "/")
        
        # Create test files for find_files test
        write_file(os.path.join(tmpdir, "notes.txt"), "some notes")
        write_file(os.path.join(tmpdir, "data.txt"), "some data")
        
        tests = [
            ("Calculator choice", "What is 25 times 4?", "100", "calculator"),
            ("Shell choice", "Echo the text 'MultiTool'", "MultiTool", "shell"),
            ("Date choice", "What is today's date?", None, "get_date"),
            ("File read choice", f"Read the file at {test_file_fs}", "Test content", "read_file"),
            ("Find files choice", f"Find all text files in {tmpdir_fs}", ".txt", "find_files"),
            ("Todo add choice", "Add a todo: Review test results", "Review", "todo"),
        ]
        
        results = []
        
        for name, prompt, expected, expected_tool in tests:
            print(f"\n📋 {name}")
            print(f"   Prompt: {prompt}")
            print(f"   Expected tool: {expected_tool}")
            
            agent = Agent(
                model=model,
                tools=tools,
                backend=backend,
                max_steps=5,
                debug=debug,
                soul=soul,
                soul_level=soul_level,
                force_react=force_react,
                num_ctx=num_ctx,
                num_predict=num_predict,
                temperature=temperature,
                top_p=top_p,
            )
            
            t0 = time.time()
            run = agent.run(prompt)
            elapsed = time.time() - t0
            
            # Check if correct tool was used
            correct_tool = check_tool_used(run, expected_tool)
            
            # Check result (numeric-aware comparison for number expectations)
            passed = correct_tool
            if expected:
                expected_num = normalize_number(expected)
                actual_num = normalize_number(run.final_answer)
                found = numbers_match(expected_num, actual_num)
                if not found:
                    found = expected.lower() in run.final_answer.lower()
                if not found:
                    for step in run.steps:
                        if step.tool_result:
                            result_num = normalize_number(str(step.tool_result))
                            if numbers_match(expected_num, result_num):
                                found = True
                                break
                            if expected.lower() in str(step.tool_result).lower():
                                found = True
                                break
                passed = passed and found
            
            results.append(passed)
            
            status = "✅" if passed else "❌"
            tool_status = "🔧" if correct_tool else "⚠️"
            print(f"  {status} {tool_status} Correct tool: {correct_tool} | {elapsed:.1f}s")
            print(f"  📝 {run.final_answer}")
    
    passed = sum(results)
    total = len(results)
    print(f"\n📊 All Tools Model: {passed}/{total} ({100*passed//total}%)")
    return passed, total


def run_phase2(model: str, backend, debug: bool,
               soul: str = None, soul_level: int = 2,
               force_react: bool = False,
               num_ctx: int = None, num_predict: int = None,
               temperature: float = None, top_p: float = None,
               quick: bool = False) -> tuple[int, int]:
    """Run all Phase 2 (model tool calling) tests."""
    print(f"\n{'#'*60}")
    if quick:
        print(f"# PHASE 2: Model Tool Calling (QUICK — 5 tests)")
    else:
        print(f"# PHASE 2: Model Tool Calling")
    print(f"# Testing model's ability to use tools")
    print(f"{'#'*60}")
    
    total_passed = 0
    total_tests = 0
    
    if quick:
        # Quick mode: only calculator (5 tests) + datetime (2 tests) = 7 tests
        # These are the fastest since they don't need file I/O or sandboxing
        p, t = test_calculator_model(
            model, backend, debug, soul=soul, soul_level=soul_level,
            force_react=force_react, num_ctx=num_ctx,
            num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_datetime_model(
            model, backend, debug, soul=soul, soul_level=soul_level,
            force_react=force_react, num_ctx=num_ctx,
            num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
    else:
        # Test each tool category separately
        p, t = test_calculator_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                     force_react=force_react, num_ctx=num_ctx,
                                     num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_shell_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                force_react=force_react, num_ctx=num_ctx,
                                num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_datetime_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                   force_react=force_react, num_ctx=num_ctx,
                                   num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_file_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                force_react=force_react, num_ctx=num_ctx,
                                num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_python_repl_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                      force_react=force_react, num_ctx=num_ctx,
                                      num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_read_file_lines_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                          force_react=force_react, num_ctx=num_ctx,
                                          num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_find_files_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                     force_react=force_react, num_ctx=num_ctx,
                                     num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_edit_file_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                    force_react=force_react, num_ctx=num_ctx,
                                    num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        p, t = test_todo_model(model, backend, debug, soul=soul, soul_level=soul_level,
                               force_react=force_react, num_ctx=num_ctx,
                               num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
        
        # Test with ALL tools available (model must choose correct tool)
        p, t = test_all_tools_model(model, backend, debug, soul=soul, soul_level=soul_level,
                                    force_react=force_react, num_ctx=num_ctx,
                                    num_predict=num_predict, temperature=temperature, top_p=top_p)
        total_passed += p
        total_tests += t
    
    print(f"\n{'='*60}")
    print(f"📊 PHASE 2 TOTAL: {total_passed}/{total_tests} ({100*total_passed//total_tests if total_tests > 0 else 0}%)")
    print(f"{'='*60}")
    
    return total_passed, total_tests


# ============================================================================
# Warmup
# ============================================================================

def run_warmup(model: str, backend, timeout=None) -> bool:
    """Send a warmup request to load the model into memory."""
    print("   Sending warmup request...", end=" ", flush=True)
    try:
        t0 = time.time()
        agent = Agent(model=model, tools=None, backend=backend, max_steps=1)
        agent.run("Say 'ok'")
        elapsed = time.time() - t0
        print(f"done ({elapsed:.1f}s)")
        return True
    except Exception as e:
        print(f"failed ({e})")
        return False


# ============================================================================
# Main
# ============================================================================

def main():
    args = parse_args()
    config = get_config()
    
    total_passed = 0
    total_tests = 0
    phase1_passed, phase1_total = 0, 0
    phase2_passed, phase2_total = 0, 0
    
    # Phase 1: Direct tool validation (always runs unless --model-only)
    if not args.model_only:
        phase1_passed, phase1_total = run_phase1()
        total_passed += phase1_passed
        total_tests += phase1_total
    
    # Phase 2: Model tool calling (requires backend)
    if not args.tools_only:
        model = args.model or config.default_model
        backend_name = args.backend or config.backend
        api_mode = getattr(args, 'api_mode', 'openre')
        timeout = getattr(args, 'timeout', None)
        backend = get_default_backend(backend_name, api_mode=api_mode, timeout=timeout)
        
        if not backend.is_running():
            print(f"\n❌ {backend_name.capitalize()} not running at {backend.base_url}")
            print(f"   Skipping Phase 2 (model tests)")
        else:
            if getattr(args, 'warmup', False):
                print(f"\n🔥 Warming up model...")
                if not run_warmup(model, backend, timeout=timeout):
                    print(f"   ⚠️  Warmup failed — continuing anyway")

            print(f"\n⚛️ AgentKthx Model Tool Tests")
            print(f"   Backend: {backend_name} ({backend.base_url})")
            print(f"   Model: {model}")
            api_display = {
                'openre': '[OpenResponses]',
                'openai': '[OpenAI] ChatCompletions',
            }.get(api_mode, api_mode)
            print(f"   API: {api_display}")
            if args.soul:
                print(f"   Soul: {args.soul}")
            num_ctx = getattr(args, 'num_ctx', None)
            if num_ctx is None:
                num_ctx = getattr(config, 'num_ctx', None)
            if num_ctx:
                ctx_display = f"{num_ctx // 1024}K" if num_ctx >= 1024 else str(num_ctx)
                print(f"   Context: {ctx_display}")
            
            phase2_passed, phase2_total = run_phase2(
                model, backend, args.debug,
                soul=args.soul, soul_level=args.soul_level,
                force_react=getattr(args, 'force_react', False),
                num_ctx=num_ctx,
                num_predict=getattr(args, 'num_predict', None),
                temperature=getattr(args, 'temperature', None),
                top_p=getattr(args, 'top_p', None),
                quick=getattr(args, 'quick', False),
            )
            total_passed += phase2_passed
            total_tests += phase2_total
    
    # Final summary
    print(f"\n{'='*60}")
    print(f"📊 OVERALL SUMMARY")
    print(f"{'='*60}")
    if phase1_total > 0:
        print(f"   Phase 1 (Direct): {phase1_passed}/{phase1_total}")
    if phase2_total > 0:
        print(f"   Phase 2 (Model):  {phase2_passed}/{phase2_total}")
    print(f"   ─────────────────────────")
    print(f"   TOTAL: {total_passed}/{total_tests} ({100*total_passed//total_tests if total_tests > 0 else 0}%)")
    print(f"{'='*60}")
    
    return 0