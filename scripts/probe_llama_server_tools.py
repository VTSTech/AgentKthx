#!/usr/bin/env python3
"""
Diagnostic probe for llama-server (TurboQuant fork) tool-calling behavior.

Hits the running llama-server's /v1/chat/completions endpoint directly with
three different request shapes and dumps the raw HTTP responses, so we can
see exactly which layer is generating the "Tool 'X' is not available.
Available tools: none." error.

Usage:
    python3 probe_llama_server_tools.py [model_name] [base_url]

Defaults:
    model_name = nemotron-3-nano:4b   (or whatever's in ~/.agentkthx/turbo.state)
    base_url   = http://localhost:8764 (or whatever's in turbo.state)

What it does:
    1. GET /health                — server health check
    2. GET /v1/models             — list loaded models + any tool-support hints
    3. POST /v1/chat/completions  with NO tools field             — bare chat
    4. POST /v1/chat/completions  with tools=[get_weather]        — OpenAI tools
    5. POST /v1/chat/completions  with tools=[get_weather] + tool_choice="auto"
    6. POST /v1/chat/completions  with tools=[get_weather] + tool_choice="required"
    7. GET /tools                 — probe for llama-server's built-in tools system

For each POST it prints:
    - Request body (full JSON, indented)
    - HTTP status code
    - Response headers (subset)
    - Response body (full JSON, indented, truncated to 4KB)

The agentkthx repo's code paths that this script bypasses:
    - agent.py:_generate_stream_chunks  (which calls backend.generate_completions_stream)
    - openai_compat.py:_build_openai_body (which adds tools to body)
    - openai_compat.py:generate_completions_stream (which sends to /v1/chat/completions)

So if the error appears in this script's output, it's coming from
llama-server (or the model) — NOT from AgentKthx.

Written by VTSTech — https://www.vts-tech.org
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_turbo_state() -> dict | None:
    """Try to read ~/.agentkthx/turbo.state for model + port info."""
    state_file = Path.home() / ".agentkthx" / "turbo.state"
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except Exception:
        return None


def http_request(method: str, url: str, body: dict | None = None, timeout: int = 30) -> tuple[int, dict, str]:
    """Make an HTTP request, return (status, headers, body_text)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.getheaders()), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, dict(e.getheaders()) if hasattr(e, "getheaders") else {}, body_text
    except urllib.error.URLError as e:
        return -1, {}, f"URLError: {e}"
    except Exception as e:
        return -2, {}, f"Exception: {type(e).__name__}: {e}"


def print_section(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


def print_response(status: int, headers: dict, body: str, max_body: int = 4096) -> None:
    print(f"  HTTP status: {status}")
    interesting = {k: v for k, v in headers.items()
                   if k.lower() in ("content-type", "content-length", "server", "x-llama-server")}
    if interesting:
        print(f"  Headers: {interesting}")
    if body:
        try:
            parsed = json.loads(body)
            pretty = json.dumps(parsed, indent=2)
            if len(pretty) > max_body:
                pretty = pretty[:max_body] + f"\n... [truncated, {len(body) - max_body} more bytes]"
            print(f"  Body:")
            for line in pretty.split("\n"):
                print(f"    {line}")
        except json.JSONDecodeError:
            text = body if len(body) <= max_body else body[:max_body] + f"\n... [truncated, {len(body) - max_body} more bytes]"
            print(f"  Body (raw):")
            for line in text.split("\n"):
                print(f"    {line}")
    else:
        print("  Body: (empty)")


def main() -> int:
    args = sys.argv[1:]
    model_arg = args[0] if len(args) > 0 else None
    base_url_arg = args[1] if len(args) > 1 else None

    state = load_turbo_state()
    if model_arg:
        model = model_arg
    elif state and state.get("model_name"):
        model = state["model_name"]
    else:
        model = "nemotron-3-nano:4b"

    if base_url_arg:
        base_url = base_url_arg.rstrip("/")
    elif state and state.get("port"):
        base_url = f"http://localhost:{state['port']}"
    else:
        base_url = "http://localhost:8764"

    print(f"Probe target:  {base_url}")
    print(f"Model:         {model}")
    if state:
        print(f"Turbo state:   ctx={state.get('ctx')}, num_predict={state.get('num_predict')}, "
              f"cache={state.get('cache_type_k')}/{state.get('cache_type_v')}")
    else:
        print("Turbo state:   (no ~/.agentkthx/turbo.state file)")

    # 1. GET /health
    print_section("1. GET /health - server health check")
    status, headers, body = http_request("GET", f"{base_url}/health")
    print_response(status, headers, body, max_body=512)

    # 2. GET /v1/models
    print_section("2. GET /v1/models - list loaded models + tool-support hints")
    status, headers, body = http_request("GET", f"{base_url}/v1/models")
    print_response(status, headers, body, max_body=2048)

    # 3. Bare chat (no tools)
    print_section("3. POST /v1/chat/completions - NO tools field (bare chat)")
    body_req = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Reply briefly."},
            {"role": "user", "content": "Say hello in one short sentence."},
        ],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 100,
    }
    print("  Request body:")
    print(f"    {json.dumps(body_req, indent=2)}")
    status, headers, body = http_request("POST", f"{base_url}/v1/chat/completions", body=body_req)
    print_response(status, headers, body, max_body=2048)

    # 4. OpenAI tools
    print_section("4. POST /v1/chat/completions - with tools=[get_weather] (OpenAI format)")
    weather_tool = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and country, e.g. 'Paris, France'",
                    }
                },
                "required": ["location"],
            },
        },
    }
    body_req = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Use the available tools when needed."},
            {"role": "user", "content": "What's the weather like in Tokyo?"},
        ],
        "tools": [weather_tool],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 200,
    }
    print("  Request body:")
    print(f"    {json.dumps(body_req, indent=2)}")
    status, headers, body = http_request("POST", f"{base_url}/v1/chat/completions", body=body_req)
    print_response(status, headers, body, max_body=3072)

    # 5. OpenAI tools + tool_choice="auto"
    print_section("5. POST /v1/chat/completions - with tools + tool_choice='auto'")
    body_req["tool_choice"] = "auto"
    print("  Request body:")
    print(f"    {json.dumps(body_req, indent=2)}")
    status, headers, body = http_request("POST", f"{base_url}/v1/chat/completions", body=body_req)
    print_response(status, headers, body, max_body=3072)

    # 6. OpenAI tools + tool_choice="required"
    print_section("6. POST /v1/chat/completions - with tools + tool_choice='required'")
    body_req["tool_choice"] = "required"
    print("  Request body:")
    print(f"    {json.dumps(body_req, indent=2)}")
    status, headers, body = http_request("POST", f"{base_url}/v1/chat/completions", body=body_req)
    print_response(status, headers, body, max_body=3072)

    # 7. /tools endpoint probe
    print_section("7. GET /tools - probe for llama-server's built-in tools system")
    status, headers, body = http_request("GET", f"{base_url}/tools", timeout=5)
    print_response(status, headers, body, max_body=1024)
    if status == 404:
        print("  (404 = endpoint not exposed; the server-side built-in tools system may")
        print("   still be active internally - the chat template can reference it even")
        print("   without an HTTP endpoint.)")

    print()
    print("=" * 78)
    print("  DIAGNOSTIC SUMMARY")
    print("=" * 78)
    print()
    print("Look at sections 4, 5, 6 - the 'tools=' requests:")
    print("  - If the response body contains a 'tool_calls' array -> server DOES")
    print("    honor OpenAI function calling, and the model is calling tools natively.")
    print("    AgentKthx's chat-side code should work as-is - re-check AgentKthx's")
    print("    tool-call parsing in agent.py / streaming.py.")
    print()
    print("  - If the response content contains the literal text")
    print("    \"<error>Tool '...' is not available. Available tools: none.</error>\"")
    print("    -> the server-side built-in tools system is rejecting the call.")
    print("    Fix: restart llama-server with `--tools exec_shell_command` (closest")
    print("    built-in match for AgentKthx's 'shell' tool), OR check whether the")
    print("    model's chat template is translating OpenAI tools -> server-side tools.")
    print()
    print("  - If the response is a 4xx error -> the server doesn't accept the OpenAI")
    print("    'tools' parameter at all. AgentKthx's test_tool_support should be")
    print("    classifying this as REACT (and the agent should fall back to ReAct")
    print("    prompting instead of OpenAI function calling).")
    print()
    print("  - If sections 4/5/6 succeed and look normal, but you still see the")
    print("    error in AgentKthx chat -> the issue is in AgentKthx's chat-side code")
    print("    path (streaming.py:_generate_stream_chunks or agentic_loop.py).")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
