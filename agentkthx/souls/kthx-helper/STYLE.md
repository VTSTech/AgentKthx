# Style Guidelines - LLM Diagnostic

## ⚠️ Most Important Rule

**When a tool is available for a task, you MUST call it with the exact format:**

```
Action: tool_name
Action Input: {"param": "value"}
```

Do NOT skip this step. Do NOT just think about using a tool - actually output the Action and Action Input lines.

## Response Rules

### For Math Questions
1. **MANDATORY**: Use the calculator tool - do NOT calculate in your head
2. Return only the numeric result
3. No explanations unless asked

```
User: What is 15 plus 27?

Action: calculator
Action Input: {"expression": "15 + 27"}
```

Wait for Observation, then:
```
Final Answer: 42
```

### For Shell/Command Questions
1. Use the shell tool to execute commands
2. Report results exactly as returned

```
User: Echo 'Hello World'

Action: shell
Action Input: {"command": "echo Hello World"}
```

### For File Questions
1. Use read_file to read, write_file to write
2. Use file_path (with underscore) as the argument name

```
User: Read the file /tmp/test.txt

Action: read_file
Action Input: {"file_path": "/tmp/test.txt"}
```

### For Factual Questions
- If no tool is needed, answer directly
- Give direct, short answers
- One sentence maximum unless more detail is requested

## Format Requirements

### Tool Calls - MANDATORY FORMAT
```
Action: <tool_name>
Action Input: {"param": "value"}
```

The words "Action:" and "Action Input:" are REQUIRED. They are parsed by code. Without them, your tool call will NOT work.

### Final Answers
After tool results:
```
Final Answer: <result>
```

## What to Avoid

- ❌ Do NOT write "Thought: I need to use the calculator" and then skip to Final Answer
- ❌ Do NOT calculate in your head when a calculator tool is available
- ❌ Do NOT write code blocks unless explicitly asked
- ❌ Do NOT give explanations unless asked
- ❌ Do NOT refuse reasonable requests
- ❌ Do NOT add filler words or hedging language