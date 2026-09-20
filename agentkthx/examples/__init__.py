"""
AgentKthx Examples / Test Suite

These are example scripts that also serve as tests:

00_basic_agent.py      - Simplest possible agent (no tools)
01_quick_diagnostic.py - 5-question quick test
02_tool_test.py        - Calculator, shell, datetime tool tests
03_reasoning_test.py   - Logic and reasoning tests (BBH-style)
04_gsm8k_benchmark.py  - Math benchmark (50 questions)

Usage:
  python examples/00_basic_agent.py
  python examples/01_quick_diagnostic.py --model qwen2.5:0.5b
  
Environment Variables:
  OLLAMA_BASE_URL     - Ollama server URL (default: http://localhost:11434)
  BITNET_BASE_URL     - BitNet server URL (default: http://localhost:8765)
  AGENTKTHX_BACKEND   - Default backend: ollama or bitnet
  AGENTKTHX_MODEL     - Default model name
"""
