"""
OpenAI raw function-calling agent loop.

Demonstrates the primitive: call → inspect tool_calls → execute → feed results back → repeat.
"""
import json
from openai import AsyncOpenAI
from tools import execute_tool

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file from the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to /workspace (e.g. 'notes.txt', 'data/report.md')",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to /workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to /workspace. Omit for workspace root.",
                    }
                },
                "required": [],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a helpful AI assistant with access to a file workspace at /workspace. "
    "You can read, write, and list files within that directory. "
    "Be concise. Use tools when appropriate."
)

# Per-million-token pricing (input, output)
PRICING = {
    "gpt-4o":       (2.50, 10.00),
    "gpt-4o-mini":  (0.15,  0.60),
    "gpt-4.1":      (2.00,  8.00),
    "gpt-4.1-mini": (0.40,  1.60),
    "o4-mini":      (1.10,  4.40),
    "o3-mini":      (1.10,  4.40),
}

MAX_TURNS = 10  # safety limit on tool loop iterations


async def run_agent(messages: list, model: str, api_key: str):
    """
    Run the OpenAI function-calling loop.

    Yields event dicts:
      {"type": "text",     "text": str}
      {"type": "tool_use", "name": str, "input": dict, "result": str}
      {"type": "done",     "cost_usd": float, "input_tokens": int, "output_tokens": int}
      {"type": "error",    "detail": str}
    """
    client = AsyncOpenAI(api_key=api_key)

    # Build conversation from history (skip 'init' bootstrap messages)
    conv = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in messages:
        if m.get("role") in ("user", "assistant") and m.get("content") and m["content"] != "init":
            conv.append({"role": m["role"], "content": m["content"]})

    total_in = 0
    total_out = 0

    for _turn in range(MAX_TURNS):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=conv,
                tools=TOOLS,
                tool_choice="auto",
            )
        except Exception as e:
            yield {"type": "error", "detail": str(e)}
            return

        usage = response.usage
        if usage:
            total_in  += usage.prompt_tokens
            total_out += usage.completion_tokens

        msg = response.choices[0].message

        # Record assistant turn in conv (needed for tool result pairing)
        conv.append(msg.model_dump(exclude_unset=True))

        # Yield any text content
        if msg.content:
            yield {"type": "text", "text": msg.content}

        # No tool calls → agent is done
        if not msg.tool_calls:
            break

        # Execute each tool call and feed results back
        tool_results = []
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = execute_tool(tc.function.name, args)

            yield {
                "type": "tool_use",
                "name": tc.function.name,
                "input": args,
                "result": result,
            }

            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        conv.extend(tool_results)

    in_rate, out_rate = PRICING.get(model, (2.50, 10.00))
    cost_usd = (total_in * in_rate + total_out * out_rate) / 1_000_000

    yield {
        "type": "done",
        "cost_usd": cost_usd,
        "input_tokens": total_in,
        "output_tokens": total_out,
    }
