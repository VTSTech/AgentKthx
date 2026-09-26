#!/usr/bin/env python3
"""
⚛️ AgentKthx — Agent Mode

A goal-driven execution mode where the agent autonomously works through tasks.
Unlike chat mode (which is user-driven), agent mode:
- Plans and executes multi-step tasks autonomously
- Queues messages while working, processes after completion
- Supports rollback of current step on /stop
- Provides progress tracking via slash commands

State Machine:
  IDLE → WORKING (task given) → IDLE (task done)
              ↓
         STOPPING (/stop) → IDLE (with optional rollback)

Written by VTSTech — https://www.vts-tech.org — https://github.com/VTSTech/AgentKthx
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from .colors import dim


class AgentState(Enum):
    """Agent execution states."""
    IDLE = "idle"           # Ready for new tasks
    WORKING = "working"     # Executing a task plan
    PAUSED = "paused"       # Paused mid-execution
    STOPPING = "stopping"   # User requested stop


@dataclass
class Action:
    """
    A single atomic action within a step.
    Supports rollback via undo_fn.
    """
    type: str                           # file_write, shell, http_post, agent_execution, etc.
    description: str = ""               # Human-readable description
    params: dict = field(default_factory=dict)
    result: Any = None                  # Result after execution
    error: Optional[str] = None         # Error if failed
    undo_fn: Optional[Callable] = None  # Rollback function
    undo_params: dict = field(default_factory=dict)  # Params for undo
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def undo(self) -> tuple[bool, str]:
        """Execute rollback for this action. Returns (success, message)."""
        if self.undo_fn is None:
            return False, "No rollback function defined"
        try:
            result = self.undo_fn(**self.undo_params)
            return True, result or f"Rolled back {self.type}"
        except Exception as e:
            return False, f"Rollback failed: {e}"


@dataclass
class Step:
    """
    A step in the task plan, containing multiple actions.
    Steps are the unit of rollback - stopping rolls back the current step.
    """
    description: str
    actions: list[Action] = field(default_factory=list)
    status: str = "pending"  # pending, in_progress, done, rolled_back
    checkpoint: Optional[dict] = None  # State snapshot before step started
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    def add_action(self, action: Action):
        """Add an action to this step."""
        self.actions.append(action)
    
    def rollback(self) -> list[tuple[bool, str]]:
        """Rollback all actions in reverse order. Returns list of (success, msg)."""
        results = []
        for action in reversed(self.actions):
            if action.undo_fn:
                success, msg = action.undo()
                results.append((success, msg))
        self.status = "rolled_back"
        self.completed_at = datetime.now().isoformat()
        return results


@dataclass
class TaskPlan:
    """
    A complete task plan with multiple steps.
    Created by the agent when given a goal.
    """
    goal: str
    steps: list[Step] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    current_step_index: int = 0
    
    @property
    def current_step(self) -> Optional[Step]:
        """Get the current step being executed."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None
    
    @property
    def completed_steps(self) -> int:
        """Count of completed steps."""
        return sum(1 for s in self.steps if s.status == "done")
    
    @property
    def total_steps(self) -> int:
        """Total number of steps."""
        return len(self.steps)
    
    @property
    def progress_percent(self) -> float:
        """Progress as percentage."""
        if not self.steps:
            return 0.0
        return (self.completed_steps / self.total_steps) * 100
    
    def add_step(self, description: str) -> Step:
        """Add a new step to the plan."""
        step = Step(description=description)
        self.steps.append(step)
        return step


# ------------------------------------------------------------------ #
#  Rollback helpers for common actions                                #
# ------------------------------------------------------------------ #

def create_file_write_action(path: str, content: str, base_dir: str = ".") -> Action:
    """
    Create a file write action with rollback support.
    Stores original content for restoration on undo.
    """
    full_path = Path(base_dir) / path if not Path(path).is_absolute() else Path(path)
    original_content = None
    file_existed = full_path.exists()
    
    if file_existed:
        try:
            original_content = full_path.read_text(encoding='utf-8')
        except Exception:
            original_content = None
    
    def undo_file_write(original: Optional[str], existed: bool, p: Path) -> str:
        if existed and original is not None:
            p.write_text(original, encoding='utf-8')
            return f"Restored {p}"
        elif p.exists():
            p.unlink()
            return f"Deleted {p}"
        return f"No action needed for {p}"
    
    return Action(
        type="file_write",
        description=f"Write to {path}",
        params={"path": str(full_path), "content": content},
        undo_fn=undo_file_write,
        undo_params={"original": original_content, "existed": file_existed, "p": full_path}
    )


def create_file_delete_action(path: str, base_dir: str = ".") -> Action:
    """
    Create a file delete action with rollback support.
    Moves file to temp for potential restoration.
    """
    full_path = Path(base_dir) / path if not Path(path).is_absolute() else Path(path)
    
    if not full_path.exists():
        return Action(
            type="file_delete",
            description=f"Delete {path}",
            params={"path": str(full_path)},
            error="File does not exist"
        )
    
    # Store content for potential restoration
    try:
        original_content = full_path.read_bytes()
    except Exception as e:
        return Action(
            type="file_delete",
            description=f"Delete {path}",
            params={"path": str(full_path)},
            error=f"Cannot read file: {e}"
        )
    
    def undo_file_delete(content: bytes, p: Path) -> str:
        p.write_bytes(content)
        return f"Restored {p}"
    
    return Action(
        type="file_delete",
        description=f"Delete {path}",
        params={"path": str(full_path)},
        undo_fn=undo_file_delete,
        undo_params={"content": original_content, "p": full_path}
    )


def create_mkdir_action(path: str, base_dir: str = ".") -> Action:
    """Create a directory action with rollback support."""
    full_path = Path(base_dir) / path if not Path(path).is_absolute() else Path(path)
    
    def undo_mkdir(p: Path) -> str:
        if p.exists() and p.is_dir():
            # Only remove if empty
            try:
                p.rmdir()
                return f"Removed directory {p}"
            except OSError:
                return f"Cannot remove non-empty directory {p}"
        return f"Directory {p} does not exist"
    
    return Action(
        type="mkdir",
        description=f"Create directory {path}",
        params={"path": str(full_path)},
        undo_fn=undo_mkdir,
        undo_params={"p": full_path}
    )


def create_shell_action(command: str, undo_command: Optional[str] = None) -> Action:
    """
    Create a shell command action with optional rollback command.
    Note: Shell rollback is best-effort and depends on the command.
    """
    import subprocess
    
    def run_shell(cmd: str) -> str:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            return result.stdout or result.stderr or "(no output)"
        except Exception as e:
            return f"Error: {e}"
    
    return Action(
        type="shell",
        description=f"Run: {command[:50]}{'...' if len(command) > 50 else ''}",
        params={"command": command},
        undo_fn=run_shell if undo_command else None,
        undo_params={"cmd": undo_command} if undo_command else {}
    )


# ------------------------------------------------------------------ #
#  Agent Mode Session                                                 #
# ------------------------------------------------------------------ #

class AgentMode:
    """
    Agent Mode session manager.
    
    Handles state transitions, message queuing, and task execution.
    """
    
    def __init__(self, agent, verbose: bool = False,
                 reset_memory_between_steps: bool = False,
                 stream: bool = False):
        """
        Initialize Agent Mode session.
        
        Parameters
        ----------
        agent : Agent
            The AgentKthx Agent instance to use for task execution.
        verbose : bool
            Whether to print verbose output.
        reset_memory_between_steps : bool
            If True, clear agent memory between steps so each step
            starts with a clean context.  Default is False — the agent
            reuses its memory across steps, giving it awareness of
            previous step results.
        stream : bool
            If True, stream LLM output tokens to stdout as they arrive
            (typewriter effect, matching ``chat --stream``). Also
            enables inline tool-call printing (``[N] tool name {args}
            → result``) and surfaces ``[Compaction]`` / ``[Context]``
            events from the underlying agent loop. Default False —
            caller (cmd_agent) decides based on --stream flag + backend
            cloud-ness, mirroring cmd_chat's resolution logic.
        """
        self.agent = agent
        self.verbose = verbose
        self.reset_memory_between_steps = reset_memory_between_steps
        self.stream = stream
        
        # State
        self.state = AgentState.IDLE
        self.plan: Optional[TaskPlan] = None
        
        # Message queue (for messages received while WORKING)
        self.message_queue: list[str] = []
        
        # Execution history
        self.execution_log: list[dict] = []
        
        # Track final response from last task
        self.final_response: Optional[str] = None
        
        # Callbacks
        self.on_state_change: Optional[Callable[[AgentState, AgentState], None]] = None
        self.on_step_complete: Optional[Callable[[Step], None]] = None
        self.on_task_complete: Optional[Callable[[TaskPlan], None]] = None
    
    def _set_state(self, new_state: AgentState):
        """Transition to new state, calling callback if set."""
        old_state = self.state
        self.state = new_state
        if self.on_state_change:
            self.on_state_change(old_state, new_state)
    
    # ------------------------------------------------------------------ #
    #  Slash Command Handlers                                            #
    # ------------------------------------------------------------------ #
    
    def get_status(self) -> dict:
        """Get current agent status."""
        status = {
            "state": self.state.value,
            "queue_length": len(self.message_queue),
            "final_response": self.final_response,
        }
        
        if self.plan:
            status.update({
                "goal": self.plan.goal,
                "current_step": self.plan.current_step_index + 1,
                "total_steps": self.plan.total_steps,
                "completed_steps": self.plan.completed_steps,
                "progress_percent": round(self.plan.progress_percent, 1),
                "current_step_description": self.plan.current_step.description if self.plan.current_step else None,
            })
        
        return status
    
    def pause(self) -> tuple[bool, str]:
        """
        Pause execution.
        Returns (success, message).
        """
        if self.state != AgentState.WORKING:
            return False, f"Cannot pause: agent is {self.state.value}"
        
        self._set_state(AgentState.PAUSED)
        return True, "Agent paused. Use /resume to continue or /stop to abort."
    
    def resume(self) -> tuple[bool, str]:
        """
        Resume from paused state.
        Returns (success, message).
        """
        if self.state != AgentState.PAUSED:
            return False, f"Cannot resume: agent is {self.state.value}"
        
        self._set_state(AgentState.WORKING)
        return True, "Agent resumed."
    
    def stop(self, rollback: bool = False) -> tuple[bool, str]:
        """
        Stop the current task.
        
        Parameters
        ----------
        rollback : bool
            Whether to rollback the current step.
        
        Returns
        -------
        tuple[bool, str]
            (success, message) tuple.
        """
        if self.state not in (AgentState.WORKING, AgentState.PAUSED):
            return False, f"Cannot stop: agent is {self.state.value}"
        
        rollback_results = []
        
        if rollback and self.plan and self.plan.current_step:
            step = self.plan.current_step
            rollback_results = step.rollback()
        
        # Log the stop
        self.execution_log.append({
            "type": "stop",
            "timestamp": datetime.now().isoformat(),
            "rollback": rollback,
            "rollback_results": rollback_results,
            "step": self.plan.current_step_index + 1 if self.plan else 0,
        })
        
        self._set_state(AgentState.IDLE)
        
        msg = f"Task stopped at step {self.plan.current_step_index + 1}/{self.plan.total_steps if self.plan else 0}."
        if rollback:
            msg += f" Rolled back {len(rollback_results)} action(s)."
        
        self.plan = None
        return True, msg
    
    # ------------------------------------------------------------------ #
    #  Task Execution                                                    #
    # ------------------------------------------------------------------ #
    
    # Simple planning prompt - works better with small models
    PLANNING_PROMPT = """Break down this task into 3-5 steps. Output ONLY a JSON array:
Task: {goal}

Example: [{{"description": "Step 1"}}, {{"description": "Step 2"}}]"""

    def plan_task(self, goal: str) -> TaskPlan:
        """
        Generate a plan for the given goal using LLM-based planning.
        
        Falls back to heuristic planning if LLM planning fails.
        """
        plan = TaskPlan(goal=goal)
        
        # For simple/short tasks, skip LLM planning and use heuristics
        # Small models often struggle with planning prompts
        if len(goal) < 50 or not self.verbose:
            return self._heuristic_plan(goal)
        
        # Try LLM-based planning for complex tasks
        try:
            plan = self._llm_plan(goal)
            if plan and plan.steps:
                return plan
        except Exception as e:
            if self.verbose:
                print(f"  ⚠ LLM planning failed: {e}, using heuristics")
        
        return self._heuristic_plan(goal)
    
    def _heuristic_plan(self, goal: str) -> TaskPlan:
        """Generate plan using keyword heuristics."""
        plan = TaskPlan(goal=goal)
        goal_lower = goal.lower()
        
        if "analyze" in goal_lower and "log" in goal_lower:
            plan.add_step("Locate and read log files")
            plan.add_step("Parse and categorize log entries")
            plan.add_step("Identify patterns and anomalies")
            plan.add_step("Generate analysis report")
        elif "refactor" in goal_lower:
            plan.add_step("Analyze current code structure")
            plan.add_step("Identify refactoring targets")
            plan.add_step("Apply refactoring changes")
            plan.add_step("Verify changes work correctly")
        elif "create" in goal_lower or "build" in goal_lower:
            plan.add_step("Gather requirements and context")
            plan.add_step("Create initial structure")
            plan.add_step("Implement core functionality")
            plan.add_step("Test and verify")
        elif "fix" in goal_lower or "debug" in goal_lower:
            plan.add_step("Identify the problem")
            plan.add_step("Analyze root cause")
            plan.add_step("Implement fix")
            plan.add_step("Verify fix works")
        else:
            # Simple single-step for most tasks
            plan.add_step(f"Complete: {goal[:50]}{'...' if len(goal) > 50 else ''}")
        
        return plan
    
    def _llm_plan(self, goal: str) -> Optional[TaskPlan]:
        """Use the agent's LLM to generate a plan."""
        import re
        
        plan = TaskPlan(goal=goal)
        
        # Use the agent's client for a simple completion
        try:
            if hasattr(self.agent, 'client') and hasattr(self.agent.client, 'chat'):
                # Use a simple system prompt for planning
                response = self.agent.client.chat(
                    model=self.agent.model,
                    messages=[
                        {"role": "user", "content": self.PLANNING_PROMPT.format(goal=goal)}
                    ],
                    options={"temperature": 0.1, "num_predict": 300}
                )
                content = response.get("message", {}).get("content", "")
            else:
                return None
            
            if not content:
                return None
            
            # Extract JSON array from response
            content = re.sub(r"```(?:json)?", "", content).strip().rstrip("`").strip()
            
            # Find JSON array
            start = content.find("[")
            end = content.rfind("]")
            if start == -1 or end == -1:
                return None
            
            json_str = content[start:end + 1]
            
            # Parse JSON
            import json
            steps_data = json.loads(json_str)
            
            if isinstance(steps_data, list):
                for step_data in steps_data:
                    if isinstance(step_data, dict) and "description" in step_data:
                        plan.add_step(step_data["description"])
                    elif isinstance(step_data, str):
                        plan.add_step(step_data)
            
            return plan if plan.steps else None
            
        except Exception as e:
            if self.verbose:
                print(f"  Planning error: {e}")
            return None
    
    def execute_step(self, step: Step) -> tuple[bool, str]:
        """
        Execute a single step using the Agent.
        
        Uses a simple, direct prompt that works with the agent's
        existing system prompt (including Modelfile prompts).
        """
        step.status = "in_progress"
        step.started_at = datetime.now().isoformat()

        # R06.58: sync plan.current_step_index to the step we're about to
        # execute. run_task iterates with enumerate() and (R07.01) the
        # Plan.advance() method no longer exists — this direct sync is the
        # single mechanism, so the [N/M] in the ⟳ line stays accurate.
        # Syncing here also fixes get_status() which relies on
        # current_step_index being accurate mid-run.
        if self.plan and step in self.plan.steps:
            self.plan.current_step_index = self.plan.steps.index(step)

        # Optionally clear memory between steps for isolated execution.
        # By default (False), the agent reuses its memory so it retains
        # context from previous steps — useful for multi-step workflows
        # where later steps depend on earlier results.
        if self.reset_memory_between_steps:
            self.agent.memory.clear()
        
        # Log step start
        self.execution_log.append({
            "type": "step_start",
            "timestamp": step.started_at,
            "step_description": step.description,
        })
        
        try:
            # Build step prompt with plan context for multi-step awareness.
            # The agent reuses the same memory instance across steps, so it
            # already has access to previous step results. We add a brief
            # context header so it knows where it is in the overall plan.
            if self.plan and self.plan.total_steps > 1:
                step_idx = self.plan.current_step_index + 1
                step_count = self.plan.total_steps
                step_prompt = (
                    f"[Step {step_idx} of {step_count}] "
                    f"Goal: {self.plan.goal}\n\n"
                    f"{step.description}"
                )
            else:
                step_prompt = step.description
            
            # Run the agent with the step prompt.
            # R06.58: pass stream=self.stream so --stream actually takes
            # effect in agent mode. Previously this called run() with the
            # default stream=False, so the --stream flag was silently
            # ignored — no typewriter effect, no inline tool-call printing,
            # no [Compaction]/[Context] events surfaced. All three come
            # for free once we delegate to _run_core_streaming.
            if self.verbose:
                # Include step number/total for context, e.g. "[1/4]"
                if self.plan and self.plan.total_steps > 1:
                    step_idx = self.plan.current_step_index + 1
                    step_count = self.plan.total_steps
                    print(f"  ⟳ [{step_idx}/{step_count}] Executing: {step.description}")
                else:
                    print(f"  ⟳ Executing: {step.description}")

            run = self.agent.run(step_prompt, stream=self.stream)
            
            # Track the result
            result_msg = run.final_answer
            
            # Debug output: show step completion info
            if self.verbose and run:
                tool_calls = [s for s in run.steps if hasattr(s, 'type') and s.type == "tool_call"]
                tool_names = list(set(getattr(s, 'tool_name', '') for s in tool_calls if hasattr(s, 'tool_name')))
                print(dim(f"    ⏱️ {len(run.steps)} steps, {len(tool_calls)} tool calls, {run.total_ms:.0f}ms"))
                if tool_names:
                    print(dim(f"    🔧 Tools used: {', '.join(tool_names)}"))
            
            # Log step completion
            step.status = "done"
            step.completed_at = datetime.now().isoformat()
            
            # Add an action record for the step
            action = Action(
                type="agent_execution",
                description=step.description,
                params={"prompt": step_prompt},
                result=result_msg
            )
            step.add_action(action)
            
            return True, result_msg
            
        except Exception as e:
            step.status = "rolled_back"
            step.completed_at = datetime.now().isoformat()
            return False, f"Step failed: {str(e)}"
    
    def run_task(self, goal: str) -> tuple[bool, str]:
        """
        Run a task from start to finish.
        
        This is the main entry point for autonomous task execution.
        The method will:
        1. Generate a plan
        2. Execute each step
        3. Handle completion/failure
        
        Returns
        -------
        tuple[bool, str]
            (success, final_message)
        """
        if self.state != AgentState.IDLE:
            return False, f"Agent is busy: {self.state.value}"
        
        self._set_state(AgentState.WORKING)
        
        # Generate plan
        self.plan = self.plan_task(goal)

        # R06.58: print the full plan upfront so the user can see what
        # the agent is about to do, step by step, before execution starts.
        # Matches the verbosity users expect from autonomous agent CLIs
        # (e.g., "Plan: 4 steps → 1. Analyze 2. Identify 3. Apply 4. Verify").
        # Only prints when verbose=True (which cmd_agent always sets).
        if self.verbose and self.plan and self.plan.total_steps > 0:
            print(f"  📋 Plan: {self.plan.total_steps} step(s)")
            for i, s in enumerate(self.plan.steps, 1):
                print(f"    {i}. {s.description}")
            print()

        # Log task start
        self.execution_log.append({
            "type": "task_start",
            "timestamp": datetime.now().isoformat(),
            "goal": goal,
            "steps": self.plan.total_steps,
        })
        
        # Execute steps
        final_response = None
        for i, step in enumerate(self.plan.steps):
            # Check for pause/stop between steps
            while self.state == AgentState.PAUSED:
                time.sleep(0.5)  # Wait for resume
            
            if self.state == AgentState.STOPPING:
                return False, "Task was stopped"
            
            # Execute the step
            try:
                success, msg = self.execute_step(step)
            except KeyboardInterrupt:
                step.status = "cancelled"
                step.completed_at = datetime.now().isoformat()
                self._set_state(AgentState.IDLE)
                return False, "Task cancelled by user"
            
            # Track the final response (from last successful step)
            if success and msg:
                final_response = msg
            
            if not success:
                # Step failed - ask agent to handle or abort
                self.execution_log.append({
                    "type": "step_failed",
                    "timestamp": datetime.now().isoformat(),
                    "step": i + 1,
                    "error": msg,
                })
                
                # For now, abort on failure
                self._set_state(AgentState.IDLE)
                return False, f"Task failed at step {i + 1}: {msg}"
            
            # Callback
            if self.on_step_complete:
                self.on_step_complete(step)
            
            # Inject awareness for long plans
            remaining = self.plan.total_steps - (i + 1)
            if self.plan.total_steps > 5:
                if remaining == 3:
                    self._inject_context("→ 3 steps remaining. Stay focused on the goal.")
                elif remaining == 1:
                    self._inject_context("→ Final step. Wrap up and report completion.")
        
        # Store final response
        self.final_response = final_response
        
        # Task complete
        self.execution_log.append({
            "type": "task_complete",
            "timestamp": datetime.now().isoformat(),
            "goal": goal,
            "total_steps": self.plan.total_steps,
            "final_response": final_response,
        })
        
        if self.on_task_complete:
            self.on_task_complete(self.plan)
        
        self._set_state(AgentState.IDLE)
        return True, final_response or f"Task completed: {goal}"
    
    def _inject_context(self, message: str):
        """
        Inject a context message into the agent's memory.

        Used to provide awareness prompts during long executions so the
        agent stays focused on the goal and knows where it is in the plan.
        The message is added as a 'user' role message so the model
        processes it on the next step.
        """
        try:
            self.agent.memory.add("user", message)
        except Exception:
            pass  # Non-fatal — context injection is best-effort


# ------------------------------------------------------------------ #
#  Agent Mode CLI Integration                                         #
# ------------------------------------------------------------------ #

def format_status(status: dict) -> str:
    """Format status dict for display."""
    lines = [
        f"  State: {status['state'].upper()}",
        f"  Queue: {status['queue_length']} message(s)",
    ]
    
    if "goal" in status:
        lines.extend([
            "",
            f"  Goal: {status['goal']}",
            f"  Progress: {status['current_step']}/{status['total_steps']} ({status['progress_percent']}%)",
        ])
        if status.get("current_step_description"):
            lines.append(f"  Current: {status['current_step_description']}")
    
    # Show final response if available
    if status.get("final_response"):
        lines.extend([
            "",
            "  Final Response:",
        ])
        # Show full response, wrapping long lines
        response = status["final_response"]
        for line in response.split("\n")[:10]:  # Limit to 10 lines
            lines.append(f"    {line}")
        if len(response.split("\n")) > 10:
            more_lines = len(response.split("\n")) - 10
            lines.append(f"    ... ({more_lines} more lines)")
    
    return "\n".join(lines)


def format_progress(progress: dict) -> str:
    """Format progress dict for display."""
    lines = [
        f"  Goal: {progress['goal']}",
        f"  Progress: {progress['current_step']}/{progress['total_steps']} ({progress['progress_percent']}%)",
        "",
        "  Steps:",
    ]
    
    for step in progress.get("steps", []):
        icon = {
            "pending": "○",
            "in_progress": "◐",
            "done": "●",
            "rolled_back": "↺",
        }.get(step["status"], "?")
        
        lines.append(f"    {icon} Step {step['index']}: {step['description']} [{step['status']}]")
    
    return "\n".join(lines)


__all__ = [
    "AgentState",
    "Action",
    "Step",
    "TaskPlan",
    "AgentMode",
    "create_file_write_action",
    "create_file_delete_action",
    "create_mkdir_action",
    "create_shell_action",
    "format_status",
    "format_progress",
]