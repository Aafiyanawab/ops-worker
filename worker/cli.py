import argparse, json, sys, threading, time
from pathlib import Path
from werkzeug.serving import make_server
from mock_company.app import create_app
from .agent import run
from .browser import HttpBrowser
from .llm import GeminiLLM
from .runtime import ConsoleHuman, Trace, WorkingMemory
from .tools import Toolset

ROOT = Path(__file__).resolve().parent.parent


def build(base_url, backend, human, llm=None, run_dir=None):
    run_dir = Path(run_dir or ROOT / "runs" / time.strftime("%Y%m%d-%H%M%S"))
    ev = run_dir / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    if backend == "playwright":
        from .browser import PlaywrightBrowser
        browser = PlaywrightBrowser(base_url, ev, headless=False)
    else:
        browser = HttpBrowser(base_url, ev)
    trace, memory = Trace(run_dir), WorkingMemory(run_dir)
    tools = Toolset(browser, memory, human, trace, base_url)
    return browser, tools, memory, trace, run_dir


def main():
    ap = argparse.ArgumentParser(description="Autonomous AI task worker")
    ap.add_argument("task")
    ap.add_argument("--backend", choices=["http", "playwright"], default="http")
    ap.add_argument("--auto-approve", action="store_true", help="skip the approval prompt (demo/CI only)")
    ap.add_argument("--port", type=int, default=5055)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles: emoji / ₹ safe
    base = f"http://127.0.0.1:{a.port}"
    srv = make_server("127.0.0.1", a.port, create_app())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    browser, tools, memory, trace, run_dir = build(base, a.backend, ConsoleHuman(a.auto_approve))
    llm = GeminiLLM()
    print(f"Using Gemini model: {llm.model}")
    res = run(a.task, llm, tools, memory, trace, ROOT / "company_context.md", base_url=base)
    (run_dir / "report.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 60, f"\n{'✅ SUCCESS' if res['success'] else '❌ NOT COMPLETED'}\n{res['summary']}\nEvidence:")
    for e in res["evidence"]:
        print(f"  - {e}")
    print(f"\nAudit trail + screenshots: {run_dir}")
    browser.close()


if __name__ == "__main__":
    main()
