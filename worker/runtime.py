"""Run-scoped state: audit trace, working memory, human-in-the-loop interface."""
import json, time
from pathlib import Path


class Trace:
    def __init__(self, run_dir):
        self.path = Path(run_dir) / "trace.jsonl"

    def log(self, event, **data):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 2), "event": event, **data}, default=str) + "\n")


class WorkingMemory:
    """Facts discovered during the run. Re-injected into the system prompt every turn,
    so they survive context compaction."""
    def __init__(self, run_dir):
        self.facts, self.path = {}, Path(run_dir) / "memory.json"

    def set(self, k, v):
        self.facts[k] = v
        self.path.write_text(json.dumps(self.facts, indent=2, ensure_ascii=False), encoding="utf-8")

    def render(self):
        return "\n".join(f"- {k}: {v}" for k, v in self.facts.items()) or "(empty)"


class ConsoleHuman:
    def __init__(self, auto_approve=False):
        self.auto = auto_approve

    def ask(self, question):
        return input(f"\n🙋 Agent asks: {question}\n> ")

    def approve(self, description):
        if self.auto:
            print(f"\n✅ (auto-approved) {description}")
            return True
        return input(f"\n⚠️  Approval needed for a state-changing action:\n   {description}\n   Approve? [y/N] ").strip().lower() == "y"
