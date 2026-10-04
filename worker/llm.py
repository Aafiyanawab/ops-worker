"""LLM backend: Google Gemini (free tier via Google AI Studio). Messages use a simple provider-neutral
format (text / tool_use / tool_result blocks), so another provider can be added by writing one class."""
import os, time


class GeminiLLM:
    """Gemini backend (free tier). Key from aistudio.google.com/apikey."""

    def __init__(self, model=None):
        from google import genai
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise SystemExit("Set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)")
        self.client = genai.Client(api_key=key)
        self.model = model or os.getenv("WORKER_MODEL", "gemini-3.8-flash")
        # if the main model is overloaded / unavailable, fall back to these in order
        self.fallbacks = [m for m in os.getenv("WORKER_FALLBACKS", "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash-lite").split(",") if m and m != self.model]
        self.raw, self.n = {}, 0   # keep original function-call parts (they carry thought signatures)

    def _tools(self, tools):
        from google.genai import types
        return [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=t["input_schema"])
            for t in tools])]

    def _contents(self, messages):
        from google.genai import types
        names, out = {}, []
        for m in messages:
            if isinstance(m["content"], str):
                out.append(types.Content(role="user", parts=[types.Part(text=m["content"])]))
            elif m["role"] == "assistant":
                parts = []
                for b in m["content"]:
                    if b["type"] == "text":
                        parts.append(types.Part(text=b["text"]))
                    else:
                        names[b["id"]] = b["name"]
                        parts.append(self.raw.get(b["id"]) or types.Part(function_call=types.FunctionCall(name=b["name"], args=b["input"])))
                out.append(types.Content(role="model", parts=parts))
            else:
                parts = [types.Part.from_function_response(name=names[b["tool_use_id"]], response={"result": b["content"]})
                         for b in m["content"] if b.get("type") == "tool_result"]
                out.append(types.Content(role="user", parts=parts))
        return out

    def _generate(self, model, contents, config):
        """Retry transient errors (429/5xx) with exponential backoff on one model."""
        from google.genai import errors
        for attempt in range(5):
            try:
                return self.client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    wait = 2 ** (attempt + 1)
                    print(f"   ⏳ Gemini {e.code} on {model}; retrying in {wait}s ({attempt + 1}/4)")
                    time.sleep(wait)
                    continue
                raise

    def create(self, system, messages, tools):
        from google.genai import types, errors
        contents = self._contents(messages)
        config = types.GenerateContentConfig(system_instruction=system, tools=self._tools(tools),
                                             automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        r, last = None, None
        for model in [self.model] + self.fallbacks:   # model chain: main first, then fallbacks
            try:
                r = self._generate(model, contents, config)
                if model != self.model:
                    print(f"   ↪ using fallback model {model}")
                    self.model = model                 # stick with the one that works
                    self.fallbacks = [m for m in self.fallbacks if m != model]
                break
            except errors.APIError as e:
                last = e
                if e.code not in (404, 429, 500, 502, 503, 504):
                    raise
                print(f"   ⚠ {model} failed ({e.code}); trying next model")
        if r is None:
            raise last
        blocks = []
        for p in (r.candidates[0].content.parts if r.candidates and r.candidates[0].content else []):
            if p.function_call:
                self.n += 1
                bid = f"g{self.n}"
                self.raw[bid] = p
                blocks.append({"type": "tool_use", "id": bid, "name": p.function_call.name, "input": dict(p.function_call.args or {})})
            elif p.text and not p.thought:
                blocks.append({"type": "text", "text": p.text})
        return blocks
