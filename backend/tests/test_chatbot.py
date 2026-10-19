import httpx

from fastapi.testclient import TestClient

from app import chatbot
from app.main import create_app


RECORDS = [
    {
        "inventory_numbers": [62, 63],
        "title": "Matemática 6",
        "author": "María López",
        "imprint": "Buenos Aires: Estrada, 2010",
        "binding": "Cartoné",
        "inventory_source": "62-63",
    },
    {
        "inventory_numbers": [200],
        "title": "El Principito",
        "author": "Antoine de Saint-Exupéry",
        "imprint": "",
        "binding": "",
        "inventory_source": "200",
    },
]


def make_client(tmp_path) -> TestClient:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'test-chat.db'}"
    application = create_app(database_url, create_schema=True)
    return TestClient(application)


def test_summary_counts_records_and_copies() -> None:
    summary = chatbot.build_inventory_summary(RECORDS)

    assert "2 registros" in summary
    assert "3 ejemplares" in summary
    assert "INV-000062 a INV-000200" in summary


def test_format_record_compacts_inventory_numbers() -> None:
    line = chatbot.format_record(RECORDS[0])

    assert line.startswith("INV-000062–INV-000063 | «Matemática 6»")
    assert "María López" in line


def test_select_relevant_records_matches_title() -> None:
    selected = chatbot.select_relevant_records(RECORDS, "¿Tenés el principito?")

    assert selected[0]["title"] == "El Principito"


def test_select_relevant_records_matches_inventory_number() -> None:
    selected = chatbot.select_relevant_records(RECORDS, "¿De quién es INV-000062?")

    assert selected[0]["title"] == "Matemática 6"


def test_context_includes_summary_and_records() -> None:
    summary, catalog, included, truncated = chatbot.build_catalog_context(
        RECORDS, "¿Cuántos libros hay?"
    )

    assert "2 registros" in summary
    assert included == 2
    assert truncated is False
    assert "El Principito" in catalog


def test_load_library_context_reads_markdown_and_text(monkeypatch, tmp_path) -> None:
    context_dir = tmp_path / "ctx"
    context_dir.mkdir()
    (context_dir / "EntrevistaV1.md").write_text(
        "# Normas\nPréstamo a domicilio solo socios.", encoding="utf-8"
    )
    (context_dir / "notas.txt").write_text("Horario de 18 a 22.", encoding="utf-8")
    (context_dir / "dato.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(chatbot, "context_directory", lambda: context_dir)

    text, count = chatbot.load_library_context()

    assert count == 2
    assert "Préstamo a domicilio solo socios." in text
    assert "Horario de 18 a 22." in text
    assert "dato.json" not in text


def test_chat_status_reports_context_files(monkeypatch, tmp_path) -> None:
    context_dir = tmp_path / "ctx"
    context_dir.mkdir()
    (context_dir / "EntrevistaV1.md").write_text("Normas internas.", encoding="utf-8")
    monkeypatch.setattr(chatbot, "context_directory", lambda: context_dir)
    monkeypatch.setattr(chatbot, "_api_key", lambda: "test-key")

    with make_client(tmp_path) as client:
        body = client.get("/api/chat/status").json()

    assert body["context_files"] == 1


def test_chat_status_reports_unavailable_without_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chatbot, "_api_key", lambda: "")

    with make_client(tmp_path) as client:
        body = client.get("/api/chat/status").json()

    assert body["available"] is False
    assert body["model"]


def test_chat_requires_api_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chatbot, "_api_key", lambda: "")

    with make_client(tmp_path) as client:
        response = client.post("/api/chat", json={"message": "¿Cuántos libros hay?"})

    assert response.status_code == 503


def test_chat_rejects_blank_message(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chatbot, "_api_key", lambda: "test-key")

    with make_client(tmp_path) as client:
        response = client.post("/api/chat", json={"message": "   "})

    assert response.status_code == 422


def test_chat_answers_using_inventory_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chatbot, "_api_key", lambda: "test-key")
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {"message": {"content": "Hay 1 registro y 2 ejemplares."}}
                ]
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    with make_client(tmp_path) as client:
        client.post(
            "/api/books",
            json={
                "title": "Matemática 6",
                "author": "María López",
                "inventory_numbers": [62, 63],
                "inventory_source": "62-63",
            },
        )
        response = client.post("/api/chat", json={"message": "¿Cuántos libros hay?"})

    assert response.status_code == 200
    body = response.json()
    assert "2 ejemplares" in body["reply"]
    assert body["model"]

    payload = captured["payload"]
    messages = payload["messages"]
    user_content = messages[-1]["content"]
    assert "INV-000062" in user_content
    assert "Matemática 6" in user_content
    assert messages[0]["role"] == "system"


def test_chat_providers_add_deepseek_fallback(monkeypatch) -> None:
    monkeypatch.setattr(chatbot, "_api_key", lambda: "gemini-key")
    monkeypatch.setattr(chatbot, "_base_url", lambda: "https://gemini.test")
    monkeypatch.setattr(chatbot, "_chat_model", lambda: "gemini-2.5-flash")
    monkeypatch.setattr(
        chatbot,
        "_deepseek_provider",
        lambda: {
            "name": "DeepSeek",
            "api_key": "deepseek-key",
            "base_url": "https://deepseek.test",
            "model": "deepseek-chat",
        },
    )

    models = [provider["model"] for provider in chatbot.chat_providers()]

    assert models == ["gemini-2.5-flash", "deepseek-chat"]


def test_chat_providers_skip_duplicate_fallback(monkeypatch) -> None:
    monkeypatch.setattr(chatbot, "_api_key", lambda: "same-key")
    monkeypatch.setattr(chatbot, "_base_url", lambda: "https://same.test")
    monkeypatch.setattr(chatbot, "_chat_model", lambda: "same-model")
    monkeypatch.setattr(
        chatbot,
        "_deepseek_provider",
        lambda: {
            "name": "DeepSeek",
            "api_key": "same-key",
            "base_url": "https://same.test",
            "model": "deepseek-chat",
        },
    )

    assert len(chatbot.chat_providers()) == 1


def test_chat_falls_back_when_primary_runs_out_of_tokens(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chatbot, "_api_key", lambda: "gemini-key")
    providers = [
        {
            "name": "principal",
            "api_key": "gemini-key",
            "base_url": "https://gemini.test",
            "model": "gemini-2.5-flash",
        },
        {
            "name": "DeepSeek",
            "api_key": "deepseek-key",
            "base_url": "https://deepseek.test",
            "model": "deepseek-chat",
        },
    ]
    monkeypatch.setattr(chatbot, "chat_providers", lambda: providers)
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, status_code, content=""):
            self.status_code = status_code
            self.text = content

        def json(self):
            return {"choices": [{"message": {"content": "Hay 2 ejemplares."}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if "gemini" in url:
            return FakeResponse(429, "quota exceeded")
        return FakeResponse(200, "Hay 2 ejemplares.")

    monkeypatch.setattr(httpx, "post", fake_post)

    with make_client(tmp_path) as client:
        response = client.post("/api/chat", json={"message": "¿Cuántos libros hay?"})

    assert response.status_code == 200
    assert response.json()["model"] == "deepseek-chat"
    assert "deepseek" in calls[-1]


def test_chat_includes_institutional_context(monkeypatch, tmp_path) -> None:
    context_dir = tmp_path / "ctx"
    context_dir.mkdir()
    (context_dir / "EntrevistaV1.md").write_text(
        "Préstamo a domicilio solo para socios activos.", encoding="utf-8"
    )
    monkeypatch.setattr(chatbot, "context_directory", lambda: context_dir)
    monkeypatch.setattr(chatbot, "_api_key", lambda: "test-key")
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "Sí, siendo socio activo."}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    with make_client(tmp_path) as client:
        response = client.post(
            "/api/chat", json={"message": "¿Puedo llevarme un libro a casa?"}
        )

    assert response.status_code == 200
    system_content = captured["payload"]["messages"][0]["content"]
    assert "CONOCIMIENTO INSTITUCIONAL" in system_content
    assert "solo para socios activos" in system_content
