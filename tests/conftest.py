"""Test setup: a throwaway SQLite database and a fake LLM (no real API calls)."""
import json
import os
import re
import tempfile
from types import SimpleNamespace

# Must be set BEFORE the app is imported.
_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmpdir, 'test.db')}"
os.environ["SECRET_KEY"] = "test-secret-key-for-unit-tests-only-0123456789"
os.environ["OPENAI_API_KEY"] = ""
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import agent as agent_service  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(
        r'name="csrf-token" content="([^"]+)"', html
    )
    assert match, "CSRF token not found in page"
    return match.group(1)


def register(client: TestClient, email="owner@shop-example.com", name="Test Shop", password="password123", sample=False):
    token = csrf_from(client.get("/register").text)
    data = {"business_name": name, "email": email, "password": password, "csrf_token": token}
    if sample:
        data["sample_data"] = "true"
    response = client.post("/register", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return csrf_from(client.get("/dashboard").text)


# ------------------------------------------------------------------ fake LLM
class FakeLLM:
    """Mimics openai.OpenAI().chat.completions.create with a scripted list of steps.

    Each step is either:
      ("text", "reply")
      ("tool", "tool_name", args_dict_or_callable)  # callable receives the messages so far
    """

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.steps:
            raise AssertionError("FakeLLM ran out of scripted steps")
        step = self.steps.pop(0)
        if step[0] == "text":
            message = SimpleNamespace(content=step[1], tool_calls=None)
        else:
            _, name, args = step
            args = args(kwargs["messages"]) if callable(args) else args
            call = SimpleNamespace(
                id=f"call_{len(self.calls)}",
                type="function",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            message = SimpleNamespace(content=None, tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def fake_llm(monkeypatch):
    holder = {}

    def install(steps):
        llm = FakeLLM(steps)
        holder["llm"] = llm
        monkeypatch.setattr(agent_service, "llm_client_factory", lambda: llm)
        return llm

    return install


def last_tool_result(messages) -> dict:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "tool":
            return json.loads(m["content"])
    raise AssertionError("no tool result yet")
