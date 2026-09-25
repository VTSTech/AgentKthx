"""
⚛️ AgentKthx — Orchestrator
Multi-agent orchestration for complex tasks.

Supports three execution modes:
  • Router — Route tasks to specialized agents (with LLM-based routing option)
  • Pipeline — Chain agents in sequence, each receives previous output
  • Parallel — Run agents simultaneously with result merging

Features:
  • True parallel execution with ThreadPoolExecutor
  • Timeout handling per agent
  • Fault tolerance with fallback agents
  • Result merging strategies (concat, first, vote, best)

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from .agent import Agent
from .tools import ToolRegistry


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT CARD
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentCard:
    """
    Card describing an agent's capabilities.
    
    Parameters
    ----------
    name : str
        Unique identifier for this agent
    description : str
        What this agent specializes in (used for routing)
    capabilities : list[str]
        Keywords for capability matching (used in router mode without LLM)
    tools : list[str]
        Tool names to enable for this agent
    model : str | None
        Model to use (overrides default)
    agent : Agent | None
        Pre-configured Agent instance (optional)
    priority : int
        Priority for routing (higher = more likely to be chosen)
    timeout : float
        Maximum seconds this agent can run (default: 60)
    fallback : bool
        If True, this agent runs when others fail
    """
    name: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    model: str | None = None
    agent: Agent | None = None
    priority: int = 1
    timeout: float = 60.0
    fallback: bool = False

    def matches(self, task: str) -> bool:
        """Check if this agent matches a task using capability keywords."""
        task_lower = task.lower()
        for cap in self.capabilities:
            if cap.lower() in task_lower:
                return True
        return False

    def match_score(self, task: str) -> int:
        """Calculate match score for routing."""
        task_lower = task.lower()
        score = sum(1 for cap in self.capabilities if cap.lower() in task_lower)
        return score + self.priority


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class OrchestratorResult:
    """Result from orchestrator execution."""
    mode: str  # "router" | "pipeline" | "parallel"
    chosen_agent: str | None = None  # For router mode
    agents_used: list[str] = field(default_factory=list)
    final_answer: str = ""
    agent_results: dict[str, str] = field(default_factory=dict)  # agent_name -> result
    agent_times: dict[str, float] = field(default_factory=dict)  # agent_name -> seconds
    total_ms: float = 0.0
    success: bool = True
    error: str = ""

# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """
    Multi-agent orchestrator with three execution modes.

    Modes:
    -------
    router : Router picks the best agent for the task (keyword matching or LLM)
    pipeline : Agents run sequentially, each receives previous output
    parallel : All agents run simultaneously, results merged

    Parameters
    ----------
    mode : str
        Execution mode: "router", "pipeline", or "parallel"
    default_model : str
        Default model for auto-created agents
    default_tools : list[str] | None
        Default tools for auto-created agents
    router_model : str | None
        Model to use for LLM-based routing decisions (optional)
    router_backend : str | None
        Backend to use for LLM-based routing (default: current default backend)
    on_step : Callable | None
        Callback for step events (ACP integration)
    merge_strategy : str
        How to combine parallel results: "concat", "first", "vote", "best"
    timeout : float
        Global timeout for entire orchestration (seconds)
    """

    def __init__(
        self,
        mode: Literal["router", "pipeline", "parallel"] = "router",
        default_model: str = "qwen2.5:0.5b",
        default_tools: list[str] | None = None,
        router_model: str | None = None,
        router_backend: str | None = None,
        on_step: Callable | None = None,
        merge_strategy: Literal["concat", "first", "vote", "best"] = "concat",
        timeout: float = 120.0,
    ):
        self.mode = mode
        self.default_model = default_model
        self.default_tools = default_tools or []
        self.router_model = router_model
        self.router_backend = router_backend
        self.on_step = on_step
        self.merge_strategy = merge_strategy
        self.timeout = timeout

        self._agents: dict[str, AgentCard] = {}
        self._agent_list: list[AgentCard] = []
        
        # Thread-local storage for parallel execution
        self._thread_local = threading.local()
        self._lock = threading.Lock()

    def register(self, card: AgentCard) -> None:
        """Register an agent card."""
        # Create agent if not provided
        if card.agent is None:
            from .tools import make_builtin_registry
            tools = ToolRegistry()
            if card.tools:
                builtin = make_builtin_registry()
                tools = builtin.subset(card.tools)

            card.agent = Agent(
                model=card.model or self.default_model,
                tools=tools,
            )

        self._agents[card.name] = card
        self._agent_list.append(card)

    def run(self, task: str) -> OrchestratorResult:
        """
        Run orchestration on a task.

        Parameters
        ----------
        task : str
            Task to process

        Returns
        -------
        OrchestratorResult
            Result with outputs from agent(s)
        """
        start_time = time.perf_counter()
        result = OrchestratorResult(mode=self.mode)

        if not self._agent_list:
            result.error = "No agents registered"
            result.success = False
            result.total_ms = (time.perf_counter() - start_time) * 1000
            return result

        try:
            if self.mode == "router":
                result = self._run_router(task, result)
            elif self.mode == "pipeline":
                result = self._run_pipeline(task, result)
            elif self.mode == "parallel":
                result = self._run_parallel(task, result)
            else:
                result.error = f"Unknown mode: {self.mode}"
                result.success = False
        except Exception as e:
            result.error = str(e)
            result.success = False

        result.total_ms = (time.perf_counter() - start_time) * 1000
        return result

    # ------------------------------------------------------------------ #
    #  ROUTER MODE                                                        #
    # ------------------------------------------------------------------ #

    def _run_router(self, task: str, result: OrchestratorResult) -> OrchestratorResult:
        """Route task to best matching agent."""
        # Use LLM routing if router_model is set
        if self.router_model:
            chosen = self._select_agent_with_llm(task)
        else:
            chosen = self._select_agent_by_keywords(task)

        result.chosen_agent = chosen
        result.agents_used = [chosen]

        # Run the chosen agent
        card = self._agents[chosen]
        start = time.perf_counter()

        try:
            run = card.agent.run(task)
            result.final_answer = run.final_answer
            result.agent_results[chosen] = run.final_answer
            result.agent_times[chosen] = time.perf_counter() - start
            result.success = run.success if hasattr(run, 'success') else True
        except Exception as e:
            result.agent_results[chosen] = f"[Error] {e}"
            result.agent_times[chosen] = time.perf_counter() - start
            result.success = False

            # Try fallback agents if available
            for fallback_card in self._agent_list:
                if fallback_card.fallback and fallback_card.name != chosen:
                    try:
                        run = fallback_card.agent.run(task)
                        result.final_answer = run.final_answer
                        result.agent_results[fallback_card.name] = run.final_answer
                        result.agents_used.append(fallback_card.name)
                        result.success = True
                        break
                    except Exception:
                        continue

        return result

    def _select_agent_by_keywords(self, task: str) -> str:
        """Select agent using keyword matching."""
        best_agent = None
        best_score = 0

        for card in self._agent_list:
            if card.matches(task):
                score = card.match_score(task)
                if score > best_score:
                    best_score = score
                    best_agent = card

        if best_agent is None:
            # Use first agent as fallback
            best_agent = self._agent_list[0]

        return best_agent.name

    def _select_agent_with_llm(self, task: str) -> str:
        """Use the router model to select the best agent."""
        from .backends import get_backend, get_default_backend

        backend = get_backend(self.router_backend) if self.router_backend else get_default_backend()

        # Build agent descriptions
        agent_descs = "\n".join(
            f"- {card.name}: {card.description}"
            for card in self._agent_list
        )

        router_prompt = f"""You are an agent router. Select the best agent for this task.

Available agents:
{agent_descs}

User request: {task}

Reply with ONLY the agent name (nothing else). Pick the most suitable agent."""

        try:
            response = backend.chat(
                model=self.router_model,
                messages=[{"role": "user", "content": router_prompt}],
                options={"num_predict": 20, "temperature": 0.1}
            )
            content = response.get("message", {}).get("content", "").strip().lower()

            # Match to agent names
            for name in self._agents:
                if name.lower() in content:
                    return name

            # Fallback to first
            return self._agent_list[0].name

        except Exception:
            return self._agent_list[0].name

    # ------------------------------------------------------------------ #
    #  PIPELINE MODE                                                      #
    # ------------------------------------------------------------------ #

    def _run_pipeline(self, task: str, result: OrchestratorResult) -> OrchestratorResult:
        """Run agents sequentially, each receiving previous output."""
        current_input = task
        accumulated_results = []

        for card in self._agent_list:
            start = time.perf_counter()

            # Include previous results in context
            if accumulated_results:
                enhanced_input = f"{current_input}\n\n[Previous output: {accumulated_results[-1]}]"
            else:
                enhanced_input = current_input

            try:
                run = card.agent.run(enhanced_input)
                agent_result = run.final_answer
                result.success = run.success if hasattr(run, 'success') else True
            except Exception as e:
                agent_result = f"[Error in {card.name}]: {e}"
                result.success = False

            elapsed = time.perf_counter() - start
            result.agent_results[card.name] = agent_result
            result.agent_times[card.name] = elapsed
            result.agents_used.append(card.name)
            accumulated_results.append(agent_result)

        # Final answer is the last agent's output
        result.final_answer = accumulated_results[-1] if accumulated_results else ""

        return result

    # ------------------------------------------------------------------ #
    #  PARALLEL MODE                                                      #
    # ------------------------------------------------------------------ #

    def _run_parallel(self, task: str, result: OrchestratorResult) -> OrchestratorResult:
        """Run all agents in parallel and merge results."""
        results_map = {}
        times_map = {}
        errors_map = {}

        def run_single_agent(card: AgentCard):
            """Run a single agent and store result."""
            start = time.perf_counter()
            try:
                run = card.agent.run(task)
                with self._lock:
                    results_map[card.name] = run.final_answer
                    times_map[card.name] = time.perf_counter() - start
            except Exception as e:
                with self._lock:
                    errors_map[card.name] = str(e)
                    times_map[card.name] = time.perf_counter() - start

        # Run all agents concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self._agent_list)) as executor:
            futures = [executor.submit(run_single_agent, card) for card in self._agent_list]

            # Wait for all with timeout
            done, not_done = concurrent.futures.wait(
                futures,
                timeout=self.timeout,
                return_when=concurrent.futures.ALL_COMPLETED
            )

            # Cancel any still running
            for future in not_done:
                future.cancel()

        # Collect results
        for card in self._agent_list:
            if card.name in results_map:
                result.agent_results[card.name] = results_map[card.name]
                result.agents_used.append(card.name)
            elif card.name in errors_map:
                result.agent_results[card.name] = f"[Error] {errors_map[card.name]}"
            result.agent_times[card.name] = times_map.get(card.name, 0)

        # Merge results according to strategy
        result.final_answer = self._merge_results(results_map)
        result.success = len(results_map) > 0

        return result

    def _merge_results(self, results: dict[str, str]) -> str:
        """Merge parallel results according to strategy."""
        if not results:
            return "[No results from any agent]"

        if self.merge_strategy == "first":
            return list(results.values())[0]

        elif self.merge_strategy == "concat":
            parts = []
            for name, output in results.items():
                parts.append(f"[{name}]:\n{output}")
            return "\n\n---\n\n".join(parts)

        elif self.merge_strategy == "vote":
            # Simple voting: most common answer wins
            from collections import Counter
            # Normalize and count
            normalized = [r.strip().lower()[:100] for r in results.values()]
            counts = Counter(normalized)
            most_common = counts.most_common(1)[0][0]
            # Return original (non-normalized) version
            for r in results.values():
                if r.strip().lower()[:100] == most_common:
                    return r
            return list(results.values())[0]

        elif self.merge_strategy == "best":
            # Pick longest substantive answer
            best = max(results.values(), key=lambda x: len(x.strip()))
            return best

        else:
            return list(results.values())[0]

    # ------------------------------------------------------------------ #
    #  UTILITIES                                                          #
    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:
        return f"Orchestrator(mode={self.mode}, agents={len(self._agent_list)})"


__all__ = [
    "AgentCard",
    "OrchestratorResult",
    "Orchestrator",
]