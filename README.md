# Autonomous AI Task Worker

**CentrAlign AI – AI Engineering Intern assignment ("Autonomous AI Task Worker")**

An agent that takes a plain-English back-office task, operates a real browser against a simulated company
(vendor portal + internal ERP), recovers from failures, asks a human when it should, and only reports success
after **independently verifying** the result.

> Example task: *"Find the latest invoice from Northwind Traders, extract the amount and due date, enter it into the ERP, and tell me when it's done."*

- **Demo video:** (https://drive.google.com/file/d/1gOHABQjALmI99AhcjRgamYd3U6qDTdJl/view?usp=sharing)
- **Sample run (audit trail of a real successful run):** [`examples/sample_run/`](examples/sample_run/)

## What the demo shows
| Demo | Task | Result |
|---|---|---|
| 1 | Latest Northwind invoice → ERP | Agent logs in, handles a 404 and a transient 503, picks the latest invoice by *issue date*, converts date/amount to ERP format, asks for approval, saves, verifies through a separate API → **SUCCESS** |
| 2 | "Latest Acme invoice" (ambiguous: Acme Corp vs Acme Industries) | Agent does not guess, asks me, then completes and verifies → **SUCCESS** |
| 3 | Contoso invoice, I **deny** the approval | Nothing is written; agent stops → **NOT COMPLETED** (intended) |

## Setup
```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # only for the real-browser backend

# Free Gemini key: https://aistudio.google.com/apikey
# Windows PowerShell:  $env:GEMINI_API_KEY="your_key"      Mac/Linux: export GEMINI_API_KEY=your_key
```
The key is read from the environment only; it is never written to disk or committed.

## Run
```bash
# Fast backend (HTTP + DOM snapshots). The mock company app starts automatically.
python -m worker.cli "Find the latest invoice from Northwind Traders, extract the amount and due date, and enter it into the ERP. Tell me when it's done."

# Real Chromium window
python -m worker.cli --backend playwright "<task>"

# Other tasks, same code (nothing task-specific in the agent)
python -m worker.cli "Enter the latest Acme invoice into the ERP"        # ambiguous -> agent asks you
python -m worker.cli "Find the latest invoice from Contoso Ltd and enter it into the ERP."   # try denying the approval

pytest -q    # offline tests, no API key needed
```
Each run creates `runs/<timestamp>/` with `trace.jsonl` (full audit log), `memory.json`, `report.json` and per-step evidence.

Optional env vars: `WORKER_MODEL` (default `gemini-3.8-flash`), `WORKER_FALLBACKS` (comma-separated fallback models).

## Architecture
```
 task ──► agent.py   LLM loop: plan → ONE tool call → observe → adapt
              │      system prompt = rules + company_context.md + working memory (re-injected every turn)
              ▼
          tools.py   HARNESS (plain code; the model cannot bypass it)
            • retry + backoff on transient read failures (5xx / network)
            • human approval gate on every state-changing POST (login is exempt)
            • finish(success) is REJECTED until an independent read follows a write
            • loop detection, step budget, same-host allowlist for api_get
              ▼
          browser.py   HttpBrowser | PlaywrightBrowser   (same snapshot interface)
              ▼
          mock_company/app.py   vendor portal + ERP with deliberate traps:
            transient 503, unsorted list, "18 Sep 2026" dates vs DD/MM/YYYY, strict ERP validation,
            duplicate check, ambiguous vendor names

          llm.py       Gemini backend with retry/backoff and model fallback chain (429/5xx)
          runtime.py   audit trace, working memory, human-in-the-loop interface
```

## Important design decisions
1. **The model decides, the harness guarantees.** Safety and verification are enforced in code, not left to prompt-following. Approval triggers on "this click submits a POST form", not on task-specific rules, so it generalises to other tasks.
2. **Generic element-ref snapshots** (page text + numbered links/inputs/buttons) instead of hard-coded selectors, so the same agent works on any page.
3. **Independent verification.** After a write the agent must re-read via a different channel (JSON API / list page). `finish(success=true)` is refused otherwise.
4. **Errors are observations.** Validation messages and HTTP codes go back to the model so it can adapt (e.g. `₹51,780.50` → `51780.50`, ISO date → `DD/MM/YYYY`).
5. **Working memory + context compaction.** Old large observations are truncated; durable facts live in `remember` and are re-injected each turn.
6. **Custom loop, no LangChain/LangGraph.** The loop is ~100 lines, so every decision is traceable and I can explain/debug/modify it live. For a larger system (many agents, branching workflows, checkpointing/resume) I would consider LangGraph.
7. **Two browser backends behind one interface**: deterministic HTTP for fast tests, Playwright for the real thing.
8. **LLM resilience.** Free-tier Gemini returns 429/503 under load, so `llm.py` retries with backoff and falls back to other models.
9. One tool call per turn so the observe → act cadence stays strict (element refs go stale after each action).

## Models, APIs and libraries
Google Gemini API (free tier) with native function calling via `google-genai`. Default `gemini-3.8-flash`; fallbacks `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite` (some of my runs were served by these when the main model was rate-limited). Flask (mock company app), Requests + BeautifulSoup (HTTP backend), Playwright (Chromium backend), pytest. No other external services.

## Assumptions
- A sandboxed company with one vendor portal and one ERP; mock credentials are in `company_context.md`. No real company data or credentials are used.
- "Latest invoice" = most recent issue date.
- Every ERP write requires human approval.

## Testing
`pytest -q` runs 4 offline tests. Three use a scripted stand-in for the LLM that drives the real harness, browser and mock apps (happy path with transient-failure retry, validation-error recovery and early-finish rejection; denied write never happens; human answer reaches the agent). The fourth checks LLM model fallback. I also ran the three demos above manually with real Gemini and the Playwright backend on Windows.

## Known limitations
- Verification is "independent read-back + the model comparing values"; the harness does not yet compare fields programmatically against an expected-outcome contract.
- The approval gate treats any non-login POST as a write; there are no per-action risk tiers or policy engine.
- Browser only (no desktop/computer-use over screenshots). Mock environment only; real sites with CAPTCHAs, MFA or heavy JS were not tested.
- Free-tier rate limits: under heavy use the fallback models are weaker, which can reduce reliability. The agent loop is sequential (no parallel tasks).
- Working memory is per run; the agent does not learn across runs. No automated LLM-quality eval suite yet.
- Developed and tested on Windows only.

## What I would build next
Declarative permission policy (per tool / per amount), structured "expected outcome" contracts verified in code, cross-run procedural memory (reuse successful plans), a task queue with resumable runs, a replayable eval suite with failure injection, a desktop/computer-use backend using screenshots, and secrets handling with scoped credentials.

## AI tools used
I used an AI assistant (Claude) while building this project, along with Gemini as the runtime model. I have reviewed the code of each module.