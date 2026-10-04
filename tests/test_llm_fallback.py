import os, types as _t
os.environ["GEMINI_API_KEY"] = "x"
from google.genai import errors
from worker import llm as L


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model == "bad-model":
            raise errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}, None)
        part = _t.SimpleNamespace(function_call=_t.SimpleNamespace(name="finish", args={"success": True}), text=None, thought=False)
        return _t.SimpleNamespace(candidates=[_t.SimpleNamespace(content=_t.SimpleNamespace(parts=[part]))])


def test_falls_back_when_main_model_overloaded(monkeypatch):
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    g = L.GeminiLLM(model="bad-model")
    g.fallbacks = ["good-model"]
    g.client = _t.SimpleNamespace(models=FakeModels())
    out = g.create("sys", [{"role": "user", "content": "hi"}], [])
    assert out[0]["name"] == "finish" and g.model == "good-model"
    assert g.client.models.calls.count("bad-model") == 5   # retried with backoff before giving up
