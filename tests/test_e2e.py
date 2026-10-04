"""Offline end-to-end tests: a scripted stand-in for the LLM drives the REAL harness, browser and mock apps.
They test the harness guarantees (retry, approval gate, verification gate, error recovery), not model intelligence."""
import re, threading, itertools
from pathlib import Path
import pytest
from werkzeug.serving import make_server
from mock_company.app import create_app
from worker.agent import run
from worker.cli import build

ROOT = Path(__file__).resolve().parent.parent
ports = itertools.count(5300)


def ref(obs, label):
    m = re.search(r'\[(\d+)\] \w+ "%s"' % re.escape(label), obs)
    assert m, f"{label!r} not in:\n{obs}"
    return int(m.group(1))


class Script:
    """Each step is fn(last_observation) -> (tool, args)."""
    def __init__(self, steps):
        self.steps, self.i, self.last = steps, 0, ""

    def create(self, system, messages, tools):
        if isinstance(messages[-1]["content"], list):
            self.last = messages[-1]["content"][0]["content"]
        name, args = self.steps[self.i](self.last)
        self.i += 1
        return [{"type": "tool_use", "id": f"t{self.i}", "name": name, "input": args}]


class FakeHuman:
    def __init__(self, approve=True, answer="Acme Corp"):
        self.ok, self.answer, self.approvals, self.questions = approve, answer, [], []

    def approve(self, d):
        self.approvals.append(d)
        return self.ok

    def ask(self, q):
        self.questions.append(q)
        return self.answer


@pytest.fixture
def env(tmp_path):
    port = next(ports)
    srv = make_server("127.0.0.1", port, create_app())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", tmp_path
    srv.shutdown()


def happy_steps():
    return [
        lambda o: ("browser_goto", {"url": "/vendor/login"}),
        lambda o: ("browser_fill", {"ref": ref(o, "Email"), "value": "ap@demo"}),
        lambda o: ("browser_fill", {"ref": ref(o, "Password"), "value": "demo123"}),
        lambda o: ("browser_click", {"ref": ref(o, "Sign in")}),            
        lambda o: ("browser_goto", {"url": "/vendor/invoices?vendor=Northwind"}),
        lambda o: ("browser_goto", {"url": "/vendor/invoice/INV-2087"}),  # first load 503 -> harness retries
        lambda o: ("remember", {"key": "invoice", "value": "INV-2087 51780.50 due 18 Oct 2026"}),
        lambda o: ("browser_goto", {"url": "/erp/bills/new"}),
        lambda o: ("browser_fill", {"ref": ref(o, "Vendor"), "value": "Northwind Traders"}),
        lambda o: ("browser_fill", {"ref": ref(o, "Invoice number"), "value": "INV-2087"}),
        lambda o: ("browser_fill", {"ref": ref(o, "Amount"), "value": "₹51,780.50"}),   # wrong on purpose
        lambda o: ("browser_fill", {"ref": ref(o, "Due date DD/MM/YYYY"), "value": "2026-10-18"}),  # wrong on purpose
        lambda o: ("browser_click", {"ref": ref(o, "Save bill")}),          # approval -> validation error
        lambda o: ("browser_fill", {"ref": ref(o, "Amount"), "value": "51780.50"}),
        lambda o: ("browser_fill", {"ref": ref(o, "Due date DD/MM/YYYY"), "value": "18/10/2026"}),
        lambda o: ("browser_click", {"ref": ref(o, "Save bill")}),          # success
        lambda o: ("finish", {"success": True, "summary": "x", "evidence": ["y"]}),  # must be REJECTED (no verification)
        lambda o: ("api_get", {"path": "/erp/api/bills?invoice_no=INV-2087"}),
        lambda o: ("finish", {"success": True, "summary": "Entered INV-2087", "evidence": [o]}),
    ]


def go(env, steps, human):
    base, tmp = env
    browser, tools, memory, trace, run_dir = build(base, "http", human, run_dir=tmp)
    res = run("task", Script(steps), tools, memory, trace, ROOT / "company_context.md", base_url=base)
    return res, tools, memory, run_dir


def test_happy_path_recovers_retries_and_verifies(env):
    h = FakeHuman()
    res, tools, memory, run_dir = go(env, happy_steps(), h)
    assert res["success"] and "51780.5" in res["evidence"][0] and "2026-10-18" in res["evidence"][0]
    assert len(h.approvals) == 2                      # both submit attempts were gated; login was not
    trace = (run_dir / "trace.jsonl").read_text()
    assert '"event": "retry"' in trace                # transient 503 was retried
    assert "REJECTED" in trace                        # early finish was blocked
    assert memory.facts["invoice"].startswith("INV-2087")


def test_denied_write_never_happens(env):
    steps = happy_steps()[:12] + [
        lambda o: ("browser_click", {"ref": ref(o, "Save bill")}),
        lambda o: ("finish", {"success": False, "summary": "Human denied the write", "evidence": ["approval denied"]}),
    ]
    res, tools, _, _ = go(env, steps, FakeHuman(approve=False))
    assert not res["success"]
    import requests
    assert requests.get(env[0] + "/erp/api/bills").json() == []


def test_ask_human_answer_reaches_agent(env):
    h = FakeHuman(answer="Acme Industries")
    steps = [lambda o: ("ask_human", {"question": "Acme Corp or Acme Industries?"}),
             lambda o: ("finish", {"success": False, "summary": o, "evidence": []})]
    res, *_ = go(env, steps, h)
    assert "Acme Industries" in res["summary"] and h.questions
