"""Tool layer + the safety/reliability *harness* that wraps every tool call.
The harness (not the model) enforces: retries on transient failures, human approval for writes,
and 'no success without an independent read-back after a write'."""
import json, time
from urllib.parse import urljoin, urlparse
import requests
from .browser import TransientError

SCHEMAS = [
    {"name": "browser_goto", "description": "Open a URL (absolute or relative to current site). Returns a page snapshot with numbered elements.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "browser_click", "description": "Click a link/button by ref from the LATEST snapshot. Submitting a POST form is a write and needs human approval (handled automatically).",
     "input_schema": {"type": "object", "properties": {"ref": {"type": "integer"}}, "required": ["ref"]}},
    {"name": "browser_fill", "description": "Type into a text/password field by ref.",
     "input_schema": {"type": "object", "properties": {"ref": {"type": "integer"}, "value": {"type": "string"}}, "required": ["ref", "value"]}},
    {"name": "browser_select", "description": "Choose an option in a select by ref.",
     "input_schema": {"type": "object", "properties": {"ref": {"type": "integer"}, "value": {"type": "string"}}, "required": ["ref", "value"]}},
    {"name": "api_get", "description": "Read-only GET to an internal JSON/HTTP endpoint (same host only). Use it to VERIFY outcomes independently of the UI you just used.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "remember", "description": "Store a fact (e.g. extracted amount, chosen invoice) in working memory; it is shown to you every turn.",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}},
    {"name": "ask_human", "description": "Ask the user a question when the task is ambiguous, a choice is risky, or you are stuck. Returns their answer.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "finish", "description": "End the task. success=true only if the outcome was VERIFIED by a read after any write. evidence = list of concrete observations.",
     "input_schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "summary": {"type": "string"},
                                                       "evidence": {"type": "array", "items": {"type": "string"}}}, "required": ["success", "summary", "evidence"]}},
]


class Toolset:
    def __init__(self, browser, memory, human, trace, base_url, retries=(0, 1, 2)):
        self.b, self.mem, self.human, self.trace, self.base, self.retries = browser, memory, human, trace, base_url, retries
        self.needs_verification, self.result = False, None

    def dispatch(self, name, args):
        try:
            out = getattr(self, f"t_{name}")(**args)
        except TransientError as e:
            out = f"ERROR (transient): {e}"
        except Exception as e:
            out = f"ERROR: {type(e).__name__}: {e}"
        self.trace.log("tool", name=name, args=args, out=out[:1500], evidence=getattr(self.b, "evidence", lambda: None)())
        return out

    # -- reads (with retry/backoff for transient failures)
    def t_browser_goto(self, url):
        last = None
        for i, delay in enumerate(self.retries):
            time.sleep(delay)
            try:
                snap = self.b.goto(url)
                if self.needs_verification:
                    self.needs_verification = False  # an independent re-read happened
                note = f"[harness: recovered after {i} retries]\n" if i else ""
                return note + snap
            except TransientError as e:
                last = e
                self.trace.log("retry", url=url, attempt=i + 1, error=str(e))
        raise TransientError(f"{last} (gave up after {len(self.retries)} attempts; try another route or ask_human)")

    def t_api_get(self, path):
        url = urljoin(self.base + "/", path)
        if urlparse(url).netloc != urlparse(self.base).netloc:
            return "ERROR: api_get only allows the company host"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            self.needs_verification = False
        return f"HTTP {r.status_code}\n{r.text[:3000]}"

    # -- actions
    def t_browser_click(self, ref):
        d = self.b.describe_click(ref)
        if d["is_write"]:
            self.trace.log("approval_requested", description=d["description"])
            ok = self.human.approve(d["description"])
            self.trace.log("approval_result", approved=ok)
            if not ok:
                return "DENIED: the human did not approve this write. Do not retry it; ask_human what to do or finish(success=false)."
            snap = self.b.click(ref)
            self.needs_verification = True
            return snap + "\n[harness: a write was performed. Verify it with an independent read (api_get or re-open the list page) before finish(success=true).]"
        return self.b.click(ref)

    def t_browser_fill(self, ref, value):
        return self.b.fill(ref, value)

    def t_browser_select(self, ref, value):
        return self.b.select(ref, value)

    def t_remember(self, key, value):
        self.mem.set(key, value)
        return f"stored {key}"

    def t_ask_human(self, question):
        return f"Human answered: {self.human.ask(question)}"

    def t_finish(self, success, summary, evidence):
        if success and self.needs_verification:
            return "REJECTED: you performed a write but have not verified it with an independent read. Do that first."
        if success and not evidence:
            return "REJECTED: success=true requires concrete evidence."
        self.result = {"success": success, "summary": summary, "evidence": evidence}
        return "ok"
