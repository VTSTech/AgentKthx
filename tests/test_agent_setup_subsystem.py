"""R07.00 Phase 9 — AgentSetupMixin extraction tests.

Verifies the structural move of the constructor + prompt building from
``agent.py`` to ``agentkthx/core/agent_setup.py``:

1. The mixin exists at the new import path and provides __init__,
   ``_is_comp_mode``, and ``_build_default_prompt``.
2. ``Agent`` inherits from ``AgentSetupMixin`` (MRO wiring).
3. ``Agent`` no longer *defines* the moved methods — proves move, not copy.
4. Constructing an ``Agent`` still initializes the full attribute set
   (constructor reachable through the mixin, verbatim behavior).
5. ``_build_default_prompt`` keeps all 4 prompt variants (no-tools,
   BitNet lean, comp-mode, ReAct default).
"""

from agentkthx.core.agent_setup import AgentSetupMixin


def test_mixin_provides_constructor_and_prompt_builder():
    assert hasattr(AgentSetupMixin, "__init__")
    assert hasattr(AgentSetupMixin, "_is_comp_mode")
    assert hasattr(AgentSetupMixin, "_build_default_prompt")
    # __init__ signature still accepts the full public surface
    import inspect
    params = inspect.signature(AgentSetupMixin.__init__).parameters
    for expected in ("model", "tools", "backend", "max_steps", "memory_config",
                     "debug", "system_prompt", "soul", "soul_level", "num_ctx",
                     "temperature", "top_p", "num_predict", "tool_choice",
                     "allowed_tools", "skills_prompt", "retry_on_error",
                     "max_tool_retries", "max_api_retries", "truncation",
                     "thinking_level", "think", "reasoning_effort",
                     "show_reasoning", "kwargs"):
        assert expected in params, f"constructor lost parameter: {expected}"


def test_agent_inherits_from_setup_mixin():
    from agentkthx.agent import Agent
    assert issubclass(Agent, AgentSetupMixin)
    # MRO: Agent -> AgentSetupMixin -> CompactionMixin -> object
    mro = [c.__name__ for c in Agent.__mro__]
    assert mro.index("AgentSetupMixin") < mro.index("CompactionMixin")


def test_agent_does_not_redefine_moved_methods():
    """Constructor + prompt builder must be MOVED, not copied.

    A shadowing copy in Agent.__dict__ would silently diverge from the
    mixin on future edits — reintroducing the duplication this phase
    eliminates.
    """
    from agentkthx.agent import Agent
    assert "__init__" not in vars(Agent), "Agent still defines its own __init__"
    for name in ("_is_comp_mode", "_build_default_prompt"):
        assert name not in vars(Agent), f"Agent.__dict__ still contains {name}"


def test_agent_construction_via_mixin_initializes_attributes():
    from agentkthx.agent import Agent
    from agentkthx.core.openresponses import ToolChoiceType
    agent = Agent(model="qwen2.5:0.5b", tools=["calculator"],
                  system_prompt="You are a test agent.", debug=False)
    # Constructor ran through the mixin and set the full attribute set
    assert agent.model == "qwen2.5:0.5b"
    assert isinstance(agent.session_id, str) and len(agent.session_id) == 12
    assert agent.num_ctx == 8192
    assert agent.tool_choice.type == ToolChoiceType.AUTO
    assert agent._custom_system_prompt == "You are a test agent."
    assert "calculator" in agent.tools.names()
    # system prompt landed in memory
    assert agent.memory and "test agent" in agent.memory.get_messages()[0]["content"]


class _PromptHost(AgentSetupMixin):
    """Minimal host exposing only what _build_default_prompt reads."""

    def __init__(self, is_bitnet=False, is_comp_mode=False):
        self._is_bitnet = is_bitnet
        self._comp = is_comp_mode

    @property
    def _is_comp_mode(self):
        return self._comp


def test_build_default_prompt_all_four_variants():
    host = _PromptHost()
    # 1. No tools → direct-answer prompt
    no_tools = host._build_default_prompt(False)
    assert no_tools == ("You are AI AgentKthx. "
                        "Answer questions directly and accurately.")
    # 2. BitNet → ultra-lean prompt, < 500 chars, no markdown
    bitnet = _PromptHost(is_bitnet=True)._build_default_prompt(True)
    assert len(bitnet) < 500
    assert "ReAct" in bitnet and "```" not in bitnet and "**" not in bitnet
    # 3. Comp mode → native function calling, no ReAct format block
    comp = _PromptHost(is_comp_mode=True)._build_default_prompt(True)
    assert "function calls" in comp and "Action Input" not in comp
    # 4. Default → full ReAct format
    react = host._build_default_prompt(True)
    assert "Action:" in react and "Action Input:" in react
    assert "Final Answer:" in react and "**CRITICAL RULES:**" in react
