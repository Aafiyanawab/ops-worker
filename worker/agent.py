"""Core loop: Goal → Understand → Plan → Execute → Observe → Adapt → Verify → Complete.
The model does understanding/planning/adaptation; the harness (tools.py) enforces safety and verification."""
import copy
from pathlib import Path
from .tools import SCHEMAS

SYSTEM = """You are an autonomous operations worker for a company. You complete tasks end-to-end using a browser and read-only APIs.

Operating loop: (1) restate the goal and make a short plan in your first message; (2) act with exactly one tool call at a time;
(3) read each observation carefully before choosing the next action; (4) adapt when something fails.

Rules:
- Element refs change every page load. Only use refs from the most recent snapshot.
- Never guess data. Read it from the source, and store key facts with `remember`.
- Follow company rules (below), including date/number formats. If an action returns a validation error, fix the input and retry.
- If a request is ambiguous (e.g. a name matches multiple vendors) or you are blocked, use `ask_human`.
- State-changing submits need human approval; that is handled automatically. If denied, do not work around it.
- After any write, verify with an independent read (api_get or re-open a list page) and compare to the intended values.
- Finish with `finish`, giving a concise summary and concrete evidence (values you observed). success=true only if verified.

## Company context
{context}

## Working memory (your own notes from earlier in this run)
{memory}
"""


def compact(messages, keep_last=3):
    """Context management: keep recent tool results in full, shrink older ones (facts live in working memory)."""
    msgs, seen = copy.deepcopy(messages), 0
    for m in reversed(msgs):
        if m["role"] == "user" and isinstance(m["content"], list):
            seen += 1
            if seen > keep_last:
                for b in m["content"]:
                    if b.get("type") == "tool_result" and len(b["content"]) > 400:
                        b["content"] = b["content"][:400] + "…[older observation truncated]"
    return msgs


def run(task, llm, tools, memory, trace, context_path, max_steps=40, base_url=""):
    context = Path(context_path).read_text(encoding="utf-8").replace("{BASE}", base_url)
    messages, last_calls = [{"role": "user", "content": task}], []
    trace.log("task", task=task)
    for step in range(max_steps):
        system = SYSTEM.format(context=context, memory=memory.render())
        blocks = llm.create(system, compact(messages), SCHEMAS)
        messages.append({"role": "assistant", "content": blocks})
        for b in blocks:
            if b["type"] == "text" and b["text"].strip():
                print(f"\n💭 {b['text'].strip()}")
                trace.log("thought", text=b["text"])
        uses = [b for b in blocks if b["type"] == "tool_use"]
        if not uses:
            messages.append({"role": "user", "content": "Continue by calling a tool. Call `finish` if you are done."})
            continue
        results = []
        for u in uses:
            print(f"🔧 {u['name']} {u['input']}")
            out = tools.dispatch(u["name"], u["input"])
            key = (u["name"], str(u["input"]))
            last_calls.append(key)
            if last_calls[-3:] == [key] * 3:
                out += "\n[harness: you have repeated this exact action 3 times. Change approach or ask_human.]"
            print(f"   ↳ {out[:200].replace(chr(10), ' ')}")
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": out})
        messages.append({"role": "user", "content": results})
        if tools.result:
            trace.log("result", **tools.result)
            return tools.result
    res = {"success": False, "summary": f"Stopped: step budget ({max_steps}) exhausted.", "evidence": []}
    trace.log("result", **res)
    return res
