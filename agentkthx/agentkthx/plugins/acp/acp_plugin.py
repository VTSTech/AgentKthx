"""
⚛️ AgentKthx ACP Plugin - Bridge to Agent Control Panel

This plugin connects AgentKthx agents to an ACP (Agent Control Panel) server,
enabling real-time monitoring, token tracking, and STOP/Resume control.

ACP Specification: v1.0.6 (A2A Compliance)
Compliance: Full (mandatory requirements, hints, orphan handling, nudge support, batch ops, shutdown, A2A, JSON-RPC 2.0, primary agent nudge delivery)

Features:
- Logs all tool calls to ACP (using combined /api/action endpoint)
- Logs shell commands to /api/shell/add (MANDATORY per spec §5.0)
- Tracks tokens using ACP's estimation
- Respects ACP STOP flag (raises StopIteration)
- Processes hints field for context-aware decisions
- Handles orphan_warning by completing orphaned activities
- Processes nudge field for mid-task guidance
- Syncs final answers as AI notes
- Batch operations for efficient multi-file reads (v1.0.3)
- Graceful shutdown support (v1.0.2)
- A2A Agent Registry and Messaging (v1.0.4)
- JSON-RPC 2.0 support for A2A compliance (1.0.4)
- Agent Card discovery via well-known URI (1.0.4)
- AgentSkill registration support (1.0.4)
- contextId tracking for session continuity (1.0.4)
- primary_agent in /api/whoami response (1.0.5)
- Nudges delivered only to primary agent (1.0.5)
- CSRF protection optional, disabled by default (1.0.6)
- CORS preflight support (1.0.6)
- GET /api/nudge polling support (1.0.6)
- POST /api/todos/toggle (1.0.6)
- Bootstrap activity auto-completed (no orphans)

Usage:
    from agentkthx import Agent
    from agentkthx.tools import make_builtin_registry
    from agentkthx import ACPPlugin

    # Create plugin (uses config.py defaults)
    acp = ACPPlugin()

    # Or with custom URL and callbacks:
    acp = ACPPlugin(
        base_url="http://localhost:8766",
        on_hint=lambda h: print(f"Hint: {h}"),
        on_nudge=lambda n: print(f"Nudge: {n['message']}"),
    )

    # Attach to agent
    agent = Agent(
        model="qwen2.5-coder:0.5b",
        tools=make_builtin_registry(),
    )

    # Run with ACP logging
    result = agent.run("What is 2^20?")
    # After each step, call acp.on_step(step) to log

    # Batch operations (efficient for multiple files):
    result = acp.batch_start([
        {"action": "READ", "target": "/file1.py", "content_size": 5000},
        {"action": "READ", "target": "/file2.py", "content_size": 3000},
    ])

    # Graceful shutdown:
    acp.shutdown("Work complete")

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import base64
import json
import random
import time
import urllib.request
import urllib.error
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Generator

# Default retry configuration
ACP_RETRY_CONFIG = {
    "max_retries": 3,
    "base_delay": 0.5,
    "max_delay": 8.0,
    "jitter": True,  # Add randomness to avoid thundering herd
}

# Import StepResult for type hints (optional - works without it)
try:
    from agentkthx.core.models import StepResult
except ImportError:
    # Allow standalone usage
    StepResult = Any

# Import centralized config
from agentkthx.config import ACP_BASE_URL as DEFAULT_ACP_URL
from agentkthx.config import ACP_USER as DEFAULT_ACP_USER
from agentkthx.config import ACP_PASS as DEFAULT_ACP_PASS


# ═══════════════════════════════════════════════════════════════════════════════
# COST ESTIMATION (Merged from acp_streaming.py)
# ═══════════════════════════════════════════════════════════════════════════════

# Approximate costs per 1M tokens (as of 2024, will vary)
MODEL_COSTS = {
    # OpenAI
    "gpt-4": {"input": 30.0, "output": 60.0},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
    # Anthropic
    "claude-3-opus": {"input": 15.0, "output": 75.0},
    "claude-3-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    # Local (free)
    "local": {"input": 0.0, "output": 0.0},
    # Default for unknown
    "default": {"input": 0.0, "output": 0.0},
}


@dataclass
class CostTracker:
    """Track approximate costs for API calls."""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "local"
    
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
    
    @property
    def estimated_cost(self) -> float:
        """Return estimated cost in USD."""
        costs = MODEL_COSTS.get(self.model, MODEL_COSTS["default"])
        input_cost = (self.input_tokens / 1_000_000) * costs["input"]
        output_cost = (self.output_tokens / 1_000_000) * costs["output"]
        return input_cost + output_cost
    
    def add(self, input_tokens: int, output_tokens: int):
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


@dataclass
class SessionHealth:
    """Track session health metrics."""
    acp_connected: bool = True
    last_successful_heartbeat: float = 0.0
    failed_requests: int = 0
    total_requests: int = 0
    last_error: str | None = None
    
    @property
    def health_score(self) -> float:
        """Return 0.0-1.0 health score."""
        if self.total_requests == 0:
            return 1.0
        success_rate = 1.0 - (self.failed_requests / self.total_requests)
        return success_rate
    
    @property
    def is_healthy(self) -> bool:
        return self.health_score > 0.8 and self.acp_connected


class ACPPlugin:
    """
    Plugin that bridges AgentKthx to ACP (Agent Control Panel).

    Features:
    - Logs all tool calls to ACP (using combined /api/action endpoint)
    - Logs shell commands to /api/shell/add (MANDATORY per spec §5.0)
    - Tracks tokens using ACP's estimation
    - Respects ACP STOP flag (raises StopIteration)
    - Processes hints field for context-aware decisions
    - Handles orphan_warning by completing orphaned activities
    - Processes nudge field for mid-task guidance
    - Syncs final answers as AI notes

    Parameters
    ----------
    base_url : str | None
        Full ACP URL. If None, uses DEFAULT_ACP_URL from config.
        Example: "https://your-tunnel.trycloudflare.com"
    user : str
        ACP username (default: from DEFAULT_ACP_USER)
    password : str
        ACP password (default: from DEFAULT_ACP_PASS)
    enabled : bool
        Whether plugin is active (default: True)
    on_stop : Callable[[str], None] | None
        Callback when STOP is detected (receives reason)
    on_hint : Callable[[dict], None] | None
        Callback when hints are received (receives hints dict)
    on_nudge : Callable[[dict], None] | None
        Callback when nudge is received (receives nudge dict)
    on_orphan : Callable[[list], None] | None
        Callback when orphans are detected (receives orphan list)
    on_a2a_message : Callable[[dict], None] | None
        Callback when A2A messages are pending (v1.0.4)
        Receives dict with pending_count, senders, and preview
    debug : bool
        Print debug info (default: False)
    agent_name : str
        Name to use for activity attribution (default: "AgentKthx")
        v1.0.3: Helps identify which agent performed each action in multi-agent scenarios
    model_name : str | None
        Model identifier to display (e.g., "qwen2.5-coder:0.5b")
        v1.0.3: Shows agent_name · model_name format in ACP UI
    capabilities : list[str]
        List of agent capabilities for A2A registry (v1.0.4)
        Examples: ["code", "research"], ["testing"], ["a2a"]
    endpoint : str | None
        Optional HTTP endpoint for A2A messaging (v1.0.4)
        Other agents can use this to send direct messages
    """

    def __init__(
        self,
        base_url: str | None = None,
        user: str = None,
        password: str = None,
        enabled: bool = True,
        on_stop: Callable[[str], None] | None = None,
        on_hint: Callable[[dict], None] | None = None,
        on_nudge: Callable[[dict], None] | None = None,
        on_orphan: Callable[[list], None] | None = None,
        on_a2a_message: Callable[[dict], None] | None = None,
        debug: bool = False,
        agent_name: str = "AgentKthx",
        model_name: str | None = None,
        capabilities: list[str] | None = None,
        endpoint: str | None = None,
    ):
        self.base_url = base_url if base_url else DEFAULT_ACP_URL
        self.auth = base64.b64encode(f"{user or DEFAULT_ACP_USER}:{password or DEFAULT_ACP_PASS}".encode()).decode()
        self.enabled = enabled
        self.on_stop = on_stop
        self.on_hint = on_hint          # v1.0.3: Hints callback
        self.on_nudge = on_nudge        # v1.0.3: Nudge callback
        self.on_orphan = on_orphan      # v1.0.3: Orphan callback
        self.on_a2a_message = on_a2a_message  # v1.0.4: A2A message notification callback
        self.debug = debug
        self.agent_name = agent_name
        self.model_name = model_name
        self._capabilities_override = capabilities  # Store for later, derive from tools if not provided
        self.endpoint = endpoint  # v1.0.4: A2A endpoint

        # 1.0.6: CSRF disabled by default per spec §4.2
        self._csrf_enabled: bool | None = None  # Unknown until first request

        self._csrf_token: str | None = None
        self._csrf_expiry: float = 0
        self._current_activity_id: str | None = None
        self._activity_stack: list[str] = []  # Track nested activities
        self._step_count: int = 0
        self._last_stop_check: float = 0
        self._stop_flag: bool = False
        self._stop_reason: str | None = None

        # Track pending nudge for acknowledgment
        self._pending_nudge: dict | None = None

        # 1.0.4: A2A compliance - context tracking
        self._context_id: str | None = None
        self._jsonrpc_id: int = 0  # Incrementing ID for JSON-RPC requests

        # 1.0.4: AgentSkills for A2A Agent Card
        self._skills: list[dict] | None = None

        # Tool name to ACP action type mapping
        self._action_map = {
            "read_file": "READ",
            "write_file": "WRITE",
            "edit_file": "EDIT",
            "shell": "BASH",
            "web-search": "SEARCH",
            "http_get": "API",
            "http_post": "API",
            "calculator": "SKILL",
            "python_repl": "SKILL",
            "python_repl_reset": "SKILL",
            "save_note": "TODO",
            "get_note": "READ",
            "list_notes": "READ",
            "list_directory": "READ",
            # ACP v1.0.4: A2A agent-to-agent communication
            "a2a_send": "A2A",
            "a2a_request": "A2A",
            "a2a_response": "A2A",
        }

        # v1.0.4: Auto-derive capabilities from tool action map if not explicitly provided
        # This ensures agents announce their actual tool capabilities to ACP/A2A
        if self._capabilities_override:
            self.capabilities = self._capabilities_override
        else:
            # Derive from _action_map unique values (ACP action types)
            self.capabilities = list(set(self._action_map.values()))
            # Add CHAT capability for conversational ability
            if "CHAT" not in self.capabilities:
                self.capabilities.append("CHAT")

        # 1.0.4: Auto-generate AgentSkills from tools for A2A discovery
        # This allows other agents to know what actions this agent supports
        self._auto_skills = self._generate_skills_from_tools()

        # v1.0.5: Health and cost tracking (merged from acp_streaming.py)
        self.health = SessionHealth()
        self.costs = CostTracker(model=model_name or "local")
        self.token_budget = 0  # Set via set_token_budget() if needed
        self.on_budget_exceeded: Callable[[int, int], None] | None = None

    # ------------------------------------------------------------------ #
    #  Internal HTTP Methods                                              #
    # ------------------------------------------------------------------ #

    def _log(self, message: str):
        """Debug logging."""
        if self.debug:
            print(f"  [ACP] {message}")

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | None = None,
        timeout: float = 5.0,
    ) -> dict:
        """Make HTTP request to ACP server with health tracking."""
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Track request for health monitoring
        self.health.total_requests += 1

        # Refresh CSRF token for POST requests
        if method == "POST":
            self._ensure_csrf_token()

        headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json",
        }
        # 1.0.6: Only send CSRF token if enabled (disabled by default per spec §4.2)
        if method == "POST" and self._csrf_token and self._csrf_enabled:
            headers["X-CSRF-Token"] = self._csrf_token

        url = f"{self.base_url}{endpoint}"
        body = json.dumps(data).encode() if data else None

        req = urllib.request.Request(
            url,
            headers=headers,
            method=method,
            data=body,
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode())
                self.health.last_successful_heartbeat = time.time()
                self.health.acp_connected = True
                return result
        except urllib.error.HTTPError as e:
            self.health.failed_requests += 1
            self.health.last_error = f"HTTP {e.code}"
            error_body = e.read().decode() if e.fp else ""
            return {"success": False, "error": f"HTTP {e.code}: {error_body}"}
        except urllib.error.URLError as e:
            self.health.failed_requests += 1
            self.health.acp_connected = False
            self.health.last_error = str(e.reason)
            return {"success": False, "error": f"Connection error: {e.reason}"}
        except Exception as e:
            self.health.failed_requests += 1
            self.health.last_error = str(e)
            return {"success": False, "error": str(e)}

    def _ensure_csrf_token(self):
        now = time.time()
        if self._csrf_token is None or (now - self._csrf_expiry) > 3000:
            resp = self._request("/api/csrf-token")
            # 1.0.6: CSRF is optional and disabled by default per spec §4.2
            # Track the enabled state so we can skip header when not needed
            self._csrf_enabled = resp.get("csrf_enabled", False)
            token = resp.get("csrf_token")
            # If CSRF is disabled, use empty string sentinel so we don't re-fetch
            self._csrf_token = token if token else ""
            self._csrf_expiry = now

    # ------------------------------------------------------------------ #
    #  JSON-RPC 2.0 Support (1.0.4 - A2A Compliance)                          #
    # ------------------------------------------------------------------ #

    def _jsonrpc_request(
        self,
        method: str,
        params: dict | None = None,
        timeout: float = 5.0,
    ) -> dict:
        """
        Make a JSON-RPC 2.0 request to ACP server.

        This is the A2A-compliant way to communicate with ACP.

        Parameters
        ----------
        method : str
            JSON-RPC method name (e.g., "SendMessage", "GetTask", "RegisterAgent")
        params : dict | None
            Method parameters
        timeout : float
            Request timeout in seconds

        Returns
        -------
        dict
            JSON-RPC response with 'result' or 'error'
        """
        if not self.enabled:
            return {"error": {"code": -32603, "message": "Plugin disabled"}}

        # Build JSON-RPC 2.0 request
        self._jsonrpc_id += 1
        request_body = {
            "jsonrpc": "2.0",
            "id": self._jsonrpc_id,
            "method": method,
            "params": params or {}
        }

        headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json",
            "X-Agent-Name": self.agent_name,  # 1.0.4: Agent identification header
        }

        url = f"{self.base_url}/jsonrpc"
        body = json.dumps(request_body).encode()

        req = urllib.request.Request(
            url,
            headers=headers,
            method="POST",
            data=body,
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                response = json.loads(resp.read().decode())

                # Check for JSON-RPC error
                if "error" in response:
                    return {"error": response["error"]}

                # Return result
                return response.get("result", {})

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            return {"error": {"code": e.code, "message": error_body}}
        except urllib.error.URLError as e:
            return {"error": {"code": -32300, "message": f"Connection error: {e.reason}"}}
        except json.JSONDecodeError as e:
            return {"error": {"code": -32700, "message": f"Parse error: {e}"}}
        except Exception as e:
            return {"error": {"code": -32603, "message": str(e)}}

    def _generate_skills_from_tools(self) -> list[dict]:
        """
        Auto-generate AgentSkill objects from AgentKthx's tool mapping.

        Per ACP-Spec 1.0.4 §3.10, AgentSkill objects describe specific capabilities
        an agent can perform. This method creates them automatically from the
        _action_map so other agents can discover what actions this agent supports.

        Returns
        -------
        list[dict]
            List of AgentSkill objects for A2A registration
        """
        # Define skill templates for each tool type
        skill_templates = {
            "read_file": {
                "id": "read_file",
                "name": "Read File",
                "description": "Read the contents of a file from the filesystem",
                "tags": ["file", "read", "filesystem"],
                "examples": ["Read /etc/passwd", "Read the contents of config.py"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain"],
            },
            "write_file": {
                "id": "write_file",
                "name": "Write File",
                "description": "Create or overwrite a file with new content",
                "tags": ["file", "write", "create", "filesystem"],
                "examples": ["Create a new Python script", "Write config to file"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain"],
            },
            "edit_file": {
                "id": "edit_file",
                "name": "Edit File",
                "description": "Modify an existing file with targeted edits",
                "tags": ["file", "edit", "modify", "filesystem"],
                "examples": ["Edit the config file", "Fix a bug in the code"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain"],
            },
            "shell": {
                "id": "shell",
                "name": "Execute Shell Command",
                "description": "Run a bash/shell command and return the output",
                "tags": ["bash", "shell", "command", "execute"],
                "examples": ["Run ls -la", "Execute npm install", "Run pytest"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain"],
            },
            "web-search": {
                "id": "web-search",
                "name": "Web Search",
                "description": "Search the web for information",
                "tags": ["web", "search", "internet", "query"],
                "examples": ["Search for Python tutorials", "Find documentation for FastAPI"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            },
            "http_get": {
                "id": "http_get",
                "name": "HTTP GET Request",
                "description": "Make an HTTP GET request to a URL",
                "tags": ["http", "api", "request", "get"],
                "examples": ["GET https://api.example.com/data", "Fetch data from API"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            },
            "http_post": {
                "id": "http_post",
                "name": "HTTP POST Request",
                "description": "Make an HTTP POST request to a URL",
                "tags": ["http", "api", "request", "post"],
                "examples": ["POST data to API", "Submit form data"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            },
            "calculator": {
                "id": "calculator",
                "name": "Calculator",
                "description": "Evaluate mathematical expressions",
                "tags": ["math", "calculate", "expression"],
                "examples": ["Calculate 2^20", "Evaluate sqrt(144)"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
            },
            "python_repl": {
                "id": "python_repl",
                "name": "Python REPL",
                "description": "Execute Python code and return the result",
                "tags": ["python", "code", "execute", "repl"],
                "examples": ["Run Python script", "Execute Python expression"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain"],
            },
            "list_directory": {
                "id": "list_directory",
                "name": "List Directory",
                "description": "List contents of a directory",
                "tags": ["directory", "list", "files", "filesystem"],
                "examples": ["List files in /home", "Show directory contents"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain", "application/json"],
            },
        }

        # Generate skills for tools in our action map
        skills = []
        for tool_name in self._action_map.keys():
            if tool_name in skill_templates:
                skills.append(skill_templates[tool_name])

        return skills

    def set_skills(self, skills: list[dict]) -> None:
        """
        Set agent skills for A2A Agent Card (overrides auto-generated skills).

        Skills define what this agent can do for other agents.

        Parameters
        ----------
        skills : list[dict]
            List of skill objects, each with:
            - id: Unique skill identifier
            - name: Human-readable name
            - description: What this skill does
            - tags: Keywords for discovery
            - examples: Example prompts
            - inputModes: Supported input MIME types (default: ["text/plain"])
            - outputModes: Supported output MIME types (default: ["text/plain"])

        Example
        -------
        >>> acp.set_skills([
        ...     {
        ...         "id": "code_generation",
        ...         "name": "Code Generation",
        ...         "description": "Generate code from specifications",
        ...         "tags": ["code", "generation", "programming"],
        ...         "examples": ["Create a REST API", "Write a Python function"]
        ...     }
        ... ])
        """
        self._skills = skills

    def get_skills(self) -> list[dict]:
        """
        Get the agent's skills for A2A discovery.

        Returns custom skills if set via set_skills(), otherwise returns
        auto-generated skills from AgentKthx's tool mapping.

        Returns
        -------
        list[dict]
            List of AgentSkill objects
        """
        return self._skills if self._skills else self._auto_skills

    def _check_stop_flag(self) -> bool:
        """Check if STOP flag is set on ACP server."""
        # Rate limit: only check every 2 seconds
        now = time.time()
        if (now - self._last_stop_check) < 2.0:
            return self._stop_flag

        self._last_stop_check = now
        status = self._request("/api/status")

        if status.get("stop_flag"):
            self._stop_flag = True
            self._stop_reason = status.get("stop_reason", "No reason given")
            self._log(f"STOP flag detected: {self._stop_reason}")
            return True

        return False

    def _build_metadata(self, tool_name: str = None) -> dict:
        """Build standard metadata dict for activities."""
        metadata = {
            "agent_name": self.agent_name,
            "source": "agentnova",
        }
        if self.model_name:
            metadata["model_name"] = self.model_name
        if tool_name:
            metadata["tool"] = tool_name
        return metadata

    def _process_response_fields(self, resp: dict) -> None:
        """
        Process ACP response fields: hints, orphan_warning, nudge, a2a.
        
        This is called after every /api/action request to handle
        all the important response fields per ACP spec.
        """
        # Process hints (v1.0.1 - contextual information)
        hints = resp.get("hints")
        if hints:
            self._log(f"Hints received: {list(hints.keys())}")
            if self.on_hint:
                self.on_hint(hints)
            # Check for loop detection
            if hints.get("loop_detected"):
                self._log(f"⚠️ Loop detected: {hints.get('loop_count')} repetitions")
                if hints.get("suggestion"):
                    self._log(f"Suggestion: {hints['suggestion']}")
            
            # v1.0.4: Process A2A hints (pending messages notification)
            a2a_hints = hints.get("a2a")
            if a2a_hints:
                pending = a2a_hints.get("pending_count", 0)
                if pending > 0:
                    senders = a2a_hints.get("senders", [])
                    preview = a2a_hints.get("preview", {})
                    self._log(f"📨 A2A: {pending} pending message(s) from {senders}")
                    if preview:
                        self._log(f"   Preview: {preview.get('from')} - {preview.get('action')}")
                    # Call A2A callback if registered
                    if self.on_a2a_message:
                        self.on_a2a_message(a2a_hints)

        # Process orphan_warning (v1.0.2 - incomplete activities)
        orphan_warning = resp.get("orphan_warning")
        if orphan_warning:
            orphans = orphan_warning.get("tasks", [])
            self._log(f"⚠️ Orphan warning: {len(orphans)} orphaned activities")
            if self.on_orphan:
                self.on_orphan(orphans)
            # Complete orphaned activities
            self._complete_orphans(orphans)

        # Process nudge (v1.0.2 - human guidance)
        nudge = resp.get("nudge")
        if nudge:
            self._log(f"Nudge received: {nudge.get('message', '')[:50]}...")
            self._pending_nudge = nudge
            if self.on_nudge:
                self.on_nudge(nudge)
            # Auto-acknowledge if callback didn't handle it
            if nudge.get("requires_ack"):
                self.ack_nudge()

    def _complete_orphans(self, orphans: list) -> None:
        """Complete orphaned activities from previous session."""
        for orphan in orphans:
            orphan_id = orphan.get("id")
            if orphan_id:
                self._request("/api/complete", "POST", {
                    "activity_id": orphan_id,
                    "result": "[Completed by orphan handler]",
                })
                self._log(f"Completed orphan: {orphan_id}")

    # ------------------------------------------------------------------ #
    #  Public API - Shell Logging (MANDATORY per spec §5.0)              #
    # ------------------------------------------------------------------ #

    def log_shell(
        self,
        command: str,
        status: str = "completed",
        output_preview: str = "",
        error: bool = False,
    ) -> dict:
        """
        Log a shell command to ACP's Terminal history.

        This is MANDATORY per ACP spec §5.0:
        "Log every shell command via /api/shell/add"

        Parameters
        ----------
        command : str
            The shell command executed (max 500 chars)
        status : str
            "running", "completed", or "error" (default: "completed")
        output_preview : str
            First ~200 chars of output
        error : bool
            Whether the command resulted in an error

        Returns
        -------
        dict
            API response from ACP server
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Build metadata with agent attribution
        metadata = self._build_metadata()

        # Truncate per spec limits
        cmd_truncated = command[:500] if command else ""
        preview_truncated = output_preview[:200] if output_preview else ""

        return self._request("/api/shell/add", "POST", {
            "command": cmd_truncated,
            "status": "error" if error else status,
            "output_preview": preview_truncated,
            "metadata": metadata,
        })

    def check_nudge(self) -> dict:
        """
        Check if a nudge is pending without logging a new activity.

        1.0.6: GET /api/nudge returns {nudge, has_pending} for polling.
        Useful for checking nudge status between steps without
        triggering a full /api/action call.

        Returns
        -------
        dict
            Response with 'nudge' and 'has_pending' fields

        Example
        -------
        >>> result = acp.check_nudge()
        >>> if result.get("has_pending"):
        ...     print(f"Pending nudge: {result['nudge'].get('message')}")
        >>>     acp.ack_nudge()
        """
        return self._request("/api/nudge")

    def ack_nudge(self) -> dict:
        """
        Acknowledge a pending nudge.

        Call this after processing a nudge that had requires_ack=true.

        1.0.6: Also clears the nudge by setting it to None server-side.
        """
        resp = self._request("/api/nudge/ack", "POST", {})
        self._pending_nudge = None
        self._log("Nudge acknowledged")
        return resp

    # ------------------------------------------------------------------ #
    #  Public API - Main Callback                                         #
    # ------------------------------------------------------------------ #

    def on_step(self, step: StepResult) -> None:
        """
        Callback for AgentKthx Agent - called on every step.

        This is the main integration point. Call this method after
        each step in the agent's run loop:

            for step in agent.run_stream(prompt):
                acp.on_step(step)
                # ... process step

        Parameters
        ----------
        step : StepResult
            Step result from AgentKthx containing type, content, tool info.
        """
        if not self.enabled:
            return

        self._step_count += 1

        # Check STOP flag (raises to halt execution)
        if self._check_stop_flag():
            if self.on_stop:
                self.on_stop(self._stop_reason or "STOP requested")
            raise StopIteration(f"ACP STOP: {self._stop_reason}")

        step_type = getattr(step, "type", None)
        # Handle both enum and string types
        step_type_value = step_type.value if hasattr(step_type, 'value') else str(step_type)
        self._log(f"Step {self._step_count}: type={step_type_value}")

        # Handle different step types
        if step_type_value == "tool_call":
            self._handle_tool_call(step)
        elif step_type_value == "tool_result":
            self._handle_tool_result(step)
        elif step_type_value == "thought":
            self._handle_thought(step)
        elif step_type_value in ("final_answer", "final"):
            self._handle_final(step)

    def _handle_tool_call(self, step: StepResult) -> None:
        """
        Log a tool call to ACP using combined /api/action endpoint.
        
        Uses the recommended combined endpoint that can complete previous
        activity AND start new one in a single request (more efficient).
        """
        tool_call = getattr(step, "tool_call", None)
        tool_result = getattr(step, "tool_result", None)
        
        # Extract tool name and args from tool_call if available
        if tool_call:
            tool_name = tool_call.name
            tool_args = tool_call.arguments
        else:
            # Fallback: try to get from step attributes
            tool_name = getattr(step, "tool_name", "unknown")
            tool_args = getattr(step, "tool_args", {}) or {}

        # Map tool name to ACP action type
        action = self._action_map.get(tool_name.lower(), tool_name.upper())

        # Create target string from args (truncate if too long)
        target = self._format_target(tool_name, tool_args)

        # Build metadata
        metadata = self._build_metadata(tool_name)

        # Use combined /api/action endpoint (recommended per spec)
        # This checks stop_flag, starts new activity, returns hints/nudge/orphans
        resp = self._request("/api/action", "POST", {
            "action": action,
            "target": target,
            "details": f"AgentKthx tool: {tool_name}",
            "priority": "medium",
            "metadata": metadata,
        })

        # Process response fields (hints, orphan_warning, nudge)
        self._process_response_fields(resp)

        activity_id = resp.get("activity_id")
        if activity_id:
            self._activity_stack.append(activity_id)
            self._current_activity_id = activity_id
            self._log(f"Started activity: {activity_id} ({action})")

        # Special handling: Log shell commands to /api/shell/add
        if tool_name.lower() == "shell":
            cmd = tool_args.get("command", "")
            self.log_shell(cmd, status="running")

        # If we have a tool result, complete the activity
        if tool_result is not None:
            self._complete_activity(str(tool_result))

    def _handle_tool_result(self, step: StepResult) -> None:
        """
        Complete the current activity in ACP.
        
        Uses combined /api/action endpoint for efficiency when possible,
        falls back to /api/complete for final completion.
        """
        content = getattr(step, "content", "")
        tool_result = getattr(step, "tool_result", None)
        
        # Use tool_result if available, otherwise content
        result = str(tool_result) if tool_result is not None else content

        self._complete_activity(result)

    def _complete_activity(self, result: str) -> None:
        """Complete the current activity."""
        # Pop from stack
        if self._activity_stack:
            activity_id = self._activity_stack.pop()
        else:
            activity_id = self._current_activity_id

        if not activity_id:
            self._log("No activity to complete")
            return

        # Check for error indicators
        error = None
        result_lower = result.lower() if result else ""
        if any(x in result_lower for x in ["[error]", "[failed]", "exception:", "error:"]):
            error = result[:200]  # Truncate error

        # Complete using dedicated complete endpoint
        resp = self._request("/api/complete", "POST", {
            "activity_id": activity_id,
            "result": result[:500] if result else None,
            "error": error,
            "content_size": len(result) if result else 0,
        })

        self._log(f"Completed activity: {activity_id}")

        # Update current
        if self._activity_stack:
            self._current_activity_id = self._activity_stack[-1]
        else:
            self._current_activity_id = None

    def _handle_thought(self, step: StepResult) -> None:
        """
        Log agent thought process.
        
        v1.0.3: Could optionally log as CHAT action for token tracking,
        but this may be noisy. Currently just debug logs.
        """
        content = getattr(step, "content", "")
        self._log(f"Thought: {content[:100]}...")

    def _handle_final(self, step: StepResult) -> None:
        """Log final answer as an AI note and clean up."""
        content = getattr(step, "content", "")

        # Log full final answer (truncate only for debug display if very long)
        if len(content) > 500:
            self._log(f"Final answer: {content[:500]}...")
        else:
            self._log(f"Final answer: {content}")

        # Log final answer as CHAT activity for visibility in ACP
        self.log_assistant_message(content)

        # Also log as note for persistence
        self._request("/api/notes/add", "POST", {
            "category": "context",
            "content": f"AgentKthx final: {content[:2000]}",
            "importance": "high",
        })

        # Complete any orphaned activities
        while self._activity_stack:
            orphan_id = self._activity_stack.pop()
            self._request("/api/complete", "POST", {
                "activity_id": orphan_id,
                "result": "[Completed by final handler]",
            })

    def _format_target(self, tool_name: str, args: dict) -> str:
        """Format tool arguments as a target string for ACP."""
        # Tool-specific formatting
        if tool_name == "read_file":
            return args.get("file_path", args.get("path", "unknown"))
        elif tool_name == "write_file":
            return args.get("file_path", args.get("path", "unknown"))
        elif tool_name == "edit_file":
            return args.get("file_path", args.get("path", "unknown"))
        elif tool_name == "shell":
            return args.get("command", "")[:100]
        elif tool_name == "calculator":
            return args.get("expression", "")[:100]
        elif tool_name == "web-search":
            return args.get("query", "")[:100]
        elif tool_name == "http_get":
            return args.get("url", "")[:100]
        elif tool_name == "http_post":
            return args.get("url", "")[:100]
        elif tool_name == "python_repl":
            code = args.get("code", "")
            # First line only
            first_line = code.split("\n")[0] if code else ""
            return first_line[:100]
        elif tool_name == "save_note":
            return args.get("key", "unknown")
        elif tool_name == "get_note":
            return args.get("key", "unknown")
        else:
            # Generic: show first arg value
            if args:
                first_val = list(args.values())[0]
                return str(first_val)[:100]
            return tool_name

    # ------------------------------------------------------------------ #
    #  Chat Logging Methods                                                #
    # ------------------------------------------------------------------ #

    def log_chat(
        self,
        role: str,
        content: str,
        complete: bool = True,
    ) -> dict:
        """
        Log a chat message to ACP.

        This makes AgentKthx conversations visible in the ACP activity feed.

        Parameters
        ----------
        role : str
            "user" or "assistant"
        content : str
            The message content
        complete : bool
            Whether to immediately complete the activity (default: True)
            Set False for streaming responses, then call complete_chat()

        Returns
        -------
        dict
            API response with activity_id
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Build metadata
        metadata = self._build_metadata()
        metadata["chat_role"] = role

        # Truncate for display target
        preview = content[:200] if content else ""

        # Create activity - include full content in details (up to 4000 chars)
        resp = self._request("/api/action", "POST", {
            "action": "CHAT",
            "target": f"{role.title()}: {preview[:50]}...",
            "details": content[:4000] if content else "",
            "priority": "normal",
            "metadata": metadata,
        })

        # Process response fields
        self._process_response_fields(resp)

        activity_id = resp.get("activity_id")
        if activity_id and complete:
            # Complete immediately for non-streaming
            self._request("/api/complete", "POST", {
                "activity_id": activity_id,
                "result": content[:2000] if content else "",
            })
            self._log(f"Logged {role} message")

        return resp

    def log_assistant_message(self, content: str) -> dict:
        """Convenience method to log an assistant message."""
        return self.log_chat("assistant", content)

    # ------------------------------------------------------------------ #
    #  Utility Methods                                                    #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        """Get current ACP status."""
        return self._request("/api/status")

    def get_session_tokens(self) -> int:
        """Get current session token count from ACP."""
        status = self.get_status()
        return status.get("session_tokens", 0)

    # ------------------------------------------------------------------ #
    #  Budget & Cost Tracking (v1.0.5 - merged from acp_streaming.py)     #
    # ------------------------------------------------------------------ #

    def set_token_budget(self, budget: int, on_exceeded: Callable[[int, int], None] | None = None):
        """
        Set token budget limit for this session.
        
        Parameters
        ----------
        budget : int
            Maximum tokens allowed (0 = unlimited)
        on_exceeded : Callable[[current, budget], None] | None
            Callback when budget is exceeded
        """
        self.token_budget = budget
        self.on_budget_exceeded = on_exceeded

    def get_remaining_budget(self) -> int:
        """Get remaining tokens in budget. Returns -1 if unlimited."""
        if self.token_budget <= 0:
            return -1
        current = self.get_session_tokens()
        return max(0, self.token_budget - current)

    @contextmanager
    def track_operation(self, action: str, target: str) -> Generator[str | None, None, None]:
        """
        Context manager for tracking an operation.
        
        Usage:
            with acp.track_operation("READ", "/path/to/file") as activity_id:
                # do work
                result = read_file("/path/to/file")
            # Activity automatically completed on exit
        
        Parameters
        ----------
        action : str
            ACP action type (READ, WRITE, BASH, etc.)
        target : str
            Target string (file path, command, etc.)
        
        Yields
        ------
        str | None
            Activity ID or None if tracking disabled
        """
        activity_id = None
        start_time = time.time()
        
        # Start activity
        resp = self._request("/api/action", "POST", {
            "action": action,
            "target": target[:200],
            "details": f"Tracked operation: {action}",
            "priority": "medium",
            "metadata": {"agent_name": self.agent_name}
        })
        activity_id = resp.get("activity_id")
        if activity_id:
            self._activity_stack.append(activity_id)
        
        try:
            yield activity_id
        except Exception as e:
            if activity_id:
                self._request("/api/complete", "POST", {
                    "activity_id": activity_id,
                    "result": f"Error: {e}"
                })
                if activity_id in self._activity_stack:
                    self._activity_stack.remove(activity_id)
            raise
        else:
            # Completed successfully
            if activity_id:
                elapsed = time.time() - start_time
                self._request("/api/complete", "POST", {
                    "activity_id": activity_id,
                    "result": f"Completed in {elapsed:.1f}s"
                })
                if activity_id in self._activity_stack:
                    self._activity_stack.remove(activity_id)

    @property
    def summary(self) -> dict:
        """
        Get plugin session summary.
        
        Returns
        -------
        dict
            Summary with health, costs, and activity stats
        """
        return {
            "agent_name": self.agent_name,
            "model_name": self.model_name,
            "enabled": self.enabled,
            "health_score": self.health.health_score,
            "is_healthy": self.health.is_healthy,
            "acp_connected": self.health.acp_connected,
            "total_requests": self.health.total_requests,
            "failed_requests": self.health.failed_requests,
            "total_tokens": self.costs.total_tokens,
            "estimated_cost_usd": self.costs.estimated_cost,
            "steps_logged": self._step_count,
            "pending_activities": len(self._activity_stack),
            "token_budget": self.token_budget,
            "remaining_budget": self.get_remaining_budget(),
        }

    def sync_todos(self, todos: list[dict]) -> dict:
        """Sync TODO list to ACP.
        
        v1.0.3: Each TODO can include metadata with agent_name, tool, skill.
        """
        # Ensure each todo has metadata with agent attribution
        for todo in todos:
            if "metadata" not in todo:
                todo["metadata"] = {}
            if "agent_name" not in todo["metadata"]:
                todo["metadata"]["agent_name"] = self.agent_name
        return self._request("/api/todos/update", "POST", {"todos": todos})

    # ------------------------------------------------------------------ #
    #  Batch Operations (v1.0.3)                                          #
    # ------------------------------------------------------------------ #

    def batch_start(
        self,
        activities: list[dict],
    ) -> dict:
        """
        Start multiple activities in a single atomic request.

        This is more efficient than individual /api/action calls when
        reading multiple files or starting several operations at once.

        Parameters
        ----------
        activities : list[dict]
            List of activity dicts, each with:
            - action: Action type (READ, WRITE, etc.)
            - target: Target string (file path, command, etc.)
            - details: Optional description
            - content_size: Optional character count for token tracking
            - priority: Optional priority (high, medium, low)

        Returns
        -------
        dict
            Response with 'results' array containing activity_id for each

        Example
        -------
        >>> result = acp.batch_start([
        ...     {"action": "READ", "target": "/file1.py", "content_size": 5000},
        ...     {"action": "READ", "target": "/file2.py", "content_size": 3000},
        ... ])
        >>> activity_ids = [r["activity_id"] for r in result["results"]]
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Build operations array for /api/activity/batch
        operations = []
        for act in activities:
            op = {
                "type": "start",
                "action": act.get("action", "READ"),
                "target": act.get("target", ""),
                "details": act.get("details", ""),
                "content_size": act.get("content_size", 0),
                "priority": act.get("priority", "medium"),
                "metadata": self._build_metadata(act.get("tool")),
            }
            if act.get("details"):
                op["details"] = act["details"]
            operations.append(op)

        resp = self._request("/api/activity/batch", "POST", {
            "operations": operations,
        })

        # Process response fields
        self._process_response_fields(resp)

        # Track activity IDs
        if resp.get("success"):
            for result in resp.get("results", []):
                if result.get("success") and result.get("activity_id"):
                    self._activity_stack.append(result["activity_id"])

        return resp

    def batch_complete(
        self,
        completions: list[dict],
    ) -> dict:
        """
        Complete multiple activities in a single atomic request.

        Parameters
        ----------
        completions : list[dict]
            List of completion dicts, each with:
            - activity_id: ID of activity to complete
            - result: Optional result summary
            - error: Optional error message
            - content_size: Optional character count for token tracking

        Returns
        -------
        dict
            Response with 'results' array for each completion

        Example
        -------
        >>> result = acp.batch_complete([
        ...     {"activity_id": "id1", "result": "File read successfully"},
        ...     {"activity_id": "id2", "result": "File read successfully"},
        ... ])
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Build operations array
        operations = []
        for comp in completions:
            op = {
                "type": "complete",
                "activity_id": comp.get("activity_id"),
                "result": comp.get("result", ""),
                "content_size": comp.get("content_size", 0),
            }
            if comp.get("error"):
                op["error"] = comp["error"]
            operations.append(op)

        resp = self._request("/api/activity/batch", "POST", {
            "operations": operations,
        })

        # Remove from activity stack
        if resp.get("success"):
            completed_ids = {c.get("activity_id") for c in completions}
            self._activity_stack = [
                aid for aid in self._activity_stack if aid not in completed_ids
            ]

        return resp

    def batch_action(
        self,
        operations: list[dict],
    ) -> dict:
        """
        Execute mixed batch of start and complete operations.

        This is the most flexible batch method - combine starts and
        completions in a single atomic request.

        Parameters
        ----------
        operations : list[dict]
            List of operations, each with:
            - type: "start" or "complete"
            - For start: action, target, details, content_size, priority
            - For complete: activity_id, result, error, content_size

        Returns
        -------
        dict
            Response with 'results' array for each operation

        Example
        -------
        >>> # Complete previous reads and start new ones
        >>> result = acp.batch_action([
        ...     {"type": "complete", "activity_id": "prev1", "result": "Done"},
        ...     {"type": "complete", "activity_id": "prev2", "result": "Done"},
        ...     {"type": "start", "action": "READ", "target": "/newfile.py"},
        ... ])
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Add metadata to start operations
        for op in operations:
            if op.get("type") == "start":
                op["metadata"] = self._build_metadata(op.get("tool"))

        resp = self._request("/api/activity/batch", "POST", {
            "operations": operations,
        })

        self._process_response_fields(resp)

        # Track/cleanup activity IDs
        if resp.get("success"):
            for result in resp.get("results", []):
                if result.get("operation") == "start" and result.get("activity_id"):
                    self._activity_stack.append(result["activity_id"])
                elif result.get("operation") == "complete":
                    # Remove from stack if present
                    completed_id = result.get("activity_id")
                    if completed_id in self._activity_stack:
                        self._activity_stack.remove(completed_id)

        return resp

    # ------------------------------------------------------------------ #
    #  Shutdown Support (v1.0.2)                                          #
    # ------------------------------------------------------------------ #

    def shutdown(
        self,
        reason: str = "Session ended by AgentKthx",
        export_summary: bool = True,
    ) -> dict:
        """
        Gracefully end the ACP session.

        This triggers:
        1. Session summary export for context recovery
        2. Cancellation of all running activities
        3. Shutdown nudge sent to any connected agents
        4. Server stops after brief delay

        Parameters
        ----------
        reason : str
            Human-readable reason for shutdown
        export_summary : bool
            Whether to export session summary (default: True)

        Returns
        -------
        dict
            Response with shutdown status and summary path

        Example
        -------
        >>> # End session gracefully
        >>> result = acp.shutdown("Work complete, ending session")
        >>> print(f"Summary saved to: {result.get('summary_path')}")
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Unregister from A2A first (v1.0.4)
        self.a2a_unregister()

        resp = self._request("/api/shutdown", "POST", {
            "reason": reason,
            "export_summary": export_summary,
        })

        self._log(f"Shutdown requested: {reason}")

        # Clear local state
        if resp.get("success"):
            self._activity_stack.clear()
            self._current_activity_id = None

        return resp

    def add_todo(self, content: str, priority: str = "medium", status: str = "pending") -> dict:
        """
        Add a single TODO item (1.0.6).

        Unlike sync_todos() which replaces the entire list, this adds one item.

        Parameters
        ----------
        content : str
            Task description
        priority : str
            "high", "medium" (default), or "low"
        status : str
            "pending" (default), "in_progress", or "completed"

        Returns
        -------
        dict
            API response

        Example
        -------
        >>> acp.add_todo("Fix the login bug", priority="high")
        >>> acp.add_todo("Run tests", priority="low", status="in_progress")
        """
        metadata = self._build_metadata()
        return self._request("/api/todos/add", "POST", {
            "todo": {"content": content, "priority": priority, "status": status},
            "agent_name": self.agent_name,
        })

    def bootstrap(self, claim_primary: bool = True) -> dict:
        """
        Bootstrap ACP session - check status, establish identity, claim primary.

        Call this at the start of a session to:
        1. Check if STOP flag is set
        2. Establish agent identity via /api/whoami
        3. Optionally claim primary agent status

        Per ACP spec §5, this should be the first ACP call in a session.

        Parameters
        ----------
        claim_primary : bool
            Whether to claim primary agent status (default: True)
            If False, will only check status and establish identity

        Returns
        -------
        dict
            Bootstrap result with status, identity, and primary_claimed fields
        """
        result = {
            "status": None,
            "identity": None,
            "primary_claimed": False,
            "stop_flag": False,
            "warnings": [],
        }

        # 1. Check status
        status = self.get_status()
        result["status"] = status

        if status.get("stop_flag"):
            self._stop_flag = True
            self._stop_reason = status.get("stop_reason", "No reason given")
            result["stop_flag"] = True
            result["warnings"].append(f"STOP flag is set: {self._stop_reason}")
            self._log(f"Bootstrap: STOP flag detected - {self._stop_reason}")

        # 2. Establish identity
        whoami = self._request("/api/whoami")
        result["identity"] = whoami.get("identity", {})
        self._log(f"Bootstrap: Identity established as {self.agent_name}")

        # 3. Log bootstrap activity (always, regardless of primary claim)
        if not result["stop_flag"]:
            resp = self._request("/api/action", "POST", {
                "action": "CHAT",
                "target": f"{self.agent_name}: Session bootstrap",
                "details": f"Connecting to ACP session" + ("(claiming primary)" if claim_primary else "(secondary agent)"),
                "metadata": self._build_metadata(),
            })
            self._process_response_fields(resp)
            if resp.get("success") and resp.get("activity_id"):
                # Complete immediately
                self._request("/api/complete", "POST", {
                    "activity_id": resp["activity_id"],
                    "result": "Bootstrap complete",
                })
                self._log(f"Bootstrap: Logged bootstrap activity")

        # 4. Handle primary agent claim
        if claim_primary and not result["stop_flag"]:
            current_primary = status.get("primary_agent")
            if current_primary is None:
                # Try to claim primary via bootstrap activity
                result["primary_claimed"] = True  # We were first to log
                self._log(f"Bootstrap: Claimed primary agent status")
            elif current_primary == self.agent_name:
                result["primary_claimed"] = True
                self._log(f"Bootstrap: Already primary agent")
            else:
                result["warnings"].append(f"Primary agent is {current_primary}")
                self._log(f"Bootstrap: Primary agent is {current_primary}")

        # 5. Register with A2A Agent Registry (v1.0.4)
        if not result["stop_flag"]:
            a2a_result = self.a2a_register()
            result["a2a_registered"] = a2a_result.get("success", False)
            if a2a_result.get("success"):
                self._log(f"Bootstrap: Registered with A2A")

        return result

    def reset(self):
        """Reset plugin state (for new session)."""
        self._activity_stack.clear()
        self._current_activity_id = None
        self._step_count = 0
        self._stop_flag = False
        self._stop_reason = None
        self._pending_nudge = None

    # ------------------------------------------------------------------ #
    #  A2A Methods (v1.0.4 - JSON-RPC 2.0)                                #
    # ------------------------------------------------------------------ #

    def a2a_register(self, use_jsonrpc: bool = True) -> dict:
        """
        Register this agent with the A2A Agent Registry.

        Call this after bootstrap to make the agent discoverable by other agents.
        Registration includes capabilities, model name, skills, and optional endpoint.

        1.0.4: Supports JSON-RPC 2.0 for A2A compliance.

        Parameters
        ----------
        use_jsonrpc : bool
            Use JSON-RPC 2.0 if True (default), REST API otherwise

        Returns
        -------
        dict
            Registration response with success status and agent_card
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # Get skills (custom or auto-generated)
        skills = self.get_skills()
        
        params = {
            "agent_name": self.agent_name,
            "capabilities": self.capabilities,
            "model_name": self.model_name,
            "endpoint": self.endpoint,
            "skills": skills,
        }

        if use_jsonrpc:
            resp = self._jsonrpc_request("RegisterAgent", params)
            if "error" in resp:
                # Fallback to REST
                self._log(f"JSON-RPC failed, falling back to REST: {resp.get('error')}")
                resp = self._request("/api/agents/register", "POST", params)
            elif "agent_card" in resp:
                self._log(f"A2A: Registered via JSON-RPC as {self.agent_name}")
                return {"success": True, "agent_card": resp["agent_card"]}
        else:
            resp = self._request("/api/agents/register", "POST", params)

        if resp.get("success"):
            self._log(f"A2A: Registered as {self.agent_name}")

        return resp

    def a2a_unregister(self) -> dict:
        """
        Unregister this agent from the A2A Agent Registry.

        Call this before shutdown to cleanly remove the agent from the registry.

        Returns
        -------
        dict
            Unregistration response
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        resp = self._request("/api/agents/unregister", "POST", {
            "agent_name": self.agent_name,
        })

        if resp.get("success"):
            self._log(f"A2A: Unregistered {self.agent_name}")

        return resp

    def a2a_send(
        self,
        to_agent: str,
        action: str,
        payload: dict | None = None,
        message_type: str = "notification",
        subject: str | None = None,
        priority: str = "normal",
        ttl: int = 3600,
        reply_to: str | None = None,
        use_jsonrpc: bool = True,
    ) -> dict:
        """
        Send an A2A message to another agent.

        1.0.4: Supports JSON-RPC 2.0 SendMessage for A2A compliance.

        Parameters
        ----------
        to_agent : str
            Name of the target agent
        action : str
            Action type (e.g., "run_tests", "status_check", "collaborate")
        payload : dict | None
            Message payload/data
        message_type : str
            "request" (expects response), "response" (reply), or "notification"
        subject : str | None
            Human-readable subject line
        priority : str
            Message priority per spec §3.12: "normal" | "high" | "urgent" (default: "normal")
        ttl : int
            Time-to-live in seconds (default: 3600 = 1 hour)
        reply_to : str | None
            Message ID this is a reply to
        use_jsonrpc : bool
            Use JSON-RPC 2.0 SendMessage if True (default)

        Returns
        -------
        dict
            Response with success status and message_id or task
        """
        if not self.enabled:
            return {"success": False, "error": "Plugin disabled"}

        # 1.0.4: Use JSON-RPC SendMessage for A2A compliance
        if use_jsonrpc:
            # Build A2A-format message
            parts = []
            if payload:
                if isinstance(payload, dict):
                    parts.append({"data": payload})
                else:
                    parts.append({"text": str(payload)})
            else:
                parts.append({"text": action})

            message = {
                "contextId": self._context_id,
                "messageId": f"msg-{time.time_ns()}",
                "parts": parts,
                "metadata": {
                    "action": action,
                    "target_agent": to_agent,
                    "priority": priority,  # str: "normal"|"high"|"urgent" per spec §3.12
                }
            }

            resp = self._jsonrpc_request("SendMessage", {"message": message})
            if "error" not in resp:
                task = resp.get("task", {})
                self._log(f"A2A: Sent via JSON-RPC to {to_agent}: {action}")
                return {
                    "success": True,
                    "message_id": task.get("id"),
                    "task": task,
                    "context_id": task.get("contextId"),
                }
            else:
                self._log(f"JSON-RPC failed, falling back to REST: {resp.get('error')}")

        # Fallback to REST API
        data = {
            "from_agent": self.agent_name,
            "to_agent": to_agent,
            "action": action,
            "payload": payload or {},
            "type": message_type,
            "priority": priority,
            "ttl": ttl,
        }
        if subject:
            data["subject"] = subject
        if reply_to:
            data["reply_to"] = reply_to

        resp = self._request("/api/a2a/send", "POST", data)

        if resp.get("success"):
            self._log(f"A2A: Sent message to {to_agent} ({action})")

        return resp

    # ------------------------------------------------------------------ #
    #  Batch Context Manager (v1.0.6)                                     #
    # ------------------------------------------------------------------ #

    def batch_context(
        self,
        description: str = "Batch operation",
        auto_complete: bool = True,
    ):
        """
        Context manager for batch operations.

        This provides a convenient way to group multiple activities
        into a single batch with automatic completion.

        Parameters
        ----------
        description : str
            Description for the batch operation
        auto_complete : bool
            Whether to automatically complete all activities on exit (default: True)

        Yields
        ------
        BatchHelper
            A helper object for adding activities to the batch

        Example
        -------
        >>> with acp.batch_context("Read multiple config files") as batch:
        ...     batch.add_read("/etc/config1.yaml")
        ...     batch.add_read("/etc/config2.yaml")
        ...     batch.add_read("/etc/config3.yaml")
        >>> # All activities are automatically started and completed
        """
        return _BatchContext(self, description, auto_complete)


class _BatchContext:
    """
    Internal batch context manager for grouping activities.

    Collects activities and submits them as a batch when the context exits.
    """

    def __init__(self, acp: "ACPPlugin", description: str, auto_complete: bool):
        self._acp = acp
        self._description = description
        self._auto_complete = auto_complete
        self._activities: list[dict] = []
        self._results: list[dict] = []
        self._activity_ids: list[str] = []

    def add(self, action: str, target: str, details: str = "", **kwargs) -> None:
        """
        Add an activity to the batch.

        Parameters
        ----------
        action : str
            Action type (READ, WRITE, EDIT, BASH, etc.)
        target : str
            Target for the action (file path, command, etc.)
        details : str
            Optional details about the activity
        **kwargs
            Additional fields (content_size, priority, etc.)
        """
        self._activities.append({
            "action": action,
            "target": target,
            "details": details,
            **kwargs
        })

    def add_read(self, path: str, content_size: int = 0) -> None:
        """Add a READ activity for a file."""
        self.add("READ", path, f"Read file: {path}", content_size=content_size)

    def add_write(self, path: str, content_size: int = 0) -> None:
        """Add a WRITE activity for a file."""
        self.add("WRITE", path, f"Write file: {path}", content_size=content_size)

    def add_edit(self, path: str, content_size: int = 0) -> None:
        """Add an EDIT activity for a file."""
        self.add("EDIT", path, f"Edit file: {path}", content_size=content_size)

    def add_bash(self, command: str) -> None:
        """Add a BASH activity for a command."""
        self.add("BASH", command[:100], f"Execute: {command[:200]}")

    def add_search(self, query: str) -> None:
        """Add a SEARCH activity for a web search."""
        self.add("SEARCH", query[:100], f"Search: {query[:200]}")

    def add_api(self, url: str, method: str = "GET") -> None:
        """Add an API activity for an HTTP request."""
        self.add("API", url[:100], f"{method} {url[:200]}")

    @property
    def activity_ids(self) -> list[str]:
        """Get the list of activity IDs (available after entering context)."""
        return self._activity_ids

    @property
    def results(self) -> list[dict]:
        """Get the batch results (available after exiting context)."""
        return self._results

    def __enter__(self):
        """Start all activities in the batch."""
        if not self._acp.enabled or not self._activities:
            return self

        # Start all activities in batch
        result = self._acp.batch_start(self._activities)
        self._results = result.get("results", [])
        self._activity_ids = [
            r.get("activity_id") for r in self._results if r.get("activity_id")
        ]

        # Track activities
        for aid in self._activity_ids:
            if aid:
                self._acp._activity_stack.append(aid)

        self._acp._log(f"Batch started: {len(self._activity_ids)} activities for '{self._description}'")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Complete all activities in the batch."""
        if not self._acp.enabled or not self._activity_ids:
            return False

        if self._auto_complete:
            # Complete all activities
            completions = []
            for aid in self._activity_ids:
                result = "Completed" if exc_type is None else f"Error: {exc_val}"
                completions.append({
                    "activity_id": aid,
                    "result": result,
                })

            self._acp.batch_complete(completions)
            self._acp._log(f"Batch completed: {len(self._activity_ids)} activities")

        return False  # Don't suppress exceptions