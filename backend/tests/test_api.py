from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'test-catalog.db'}"
    application = create_app(database_url, create_schema=True)
    with TestClient(application) as test_client:
        yield test_client


def test_serves_admin_frontend_and_styles(client: TestClient) -> None:
    assert "Biblioteca — Inventario" in client.get("/").text
    assert client.get("/styles.css").status_code == 200
    assert client.get("/app.js").status_code == 200


def test_health_and_crud_preserve_unique_inventory_numbers(client: TestClient) -> None:
    assert client.get("/api/health").json() == {
        "status": "ok",
        "database": "connected",
    }
    payload = {
        "title": "Libro de prueba",
        "author": "Autora de prueba",
        "imprint": "Buenos Aires: Editorial, 2020",
        "binding": "Cartoné",
        "inventory_numbers": [62, 63],
        "inventory_source": "62-63",
    }

    created_response = client.post("/api/books", json=payload)
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["inventory_numbers"] == [62, 63]

    conflicting = {**payload, "title": "Conflicto", "inventory_numbers": [63, 64]}
    assert client.post("/api/books", json=conflicting).status_code == 409

    updated = {**payload, "title": "Libro editado", "inventory_numbers": [62, 64]}
    update_response = client.put(f"/api/books/{created['id']}", json=updated)
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Libro editado"
    assert update_response.json()["inventory_numbers"] == [62, 64]

    delete_response = client.delete(f"/api/books/{created['id']}")
    assert delete_response.status_code == 204
    assert client.get("/api/books").json() == []


def test_create_record_allows_missing_fields(client: TestClient) -> None:
    response = client.post("/api/books", json={})

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == ""
    assert body["author"] == ""
    assert body["inventory_numbers"] == []
    assert len(client.get("/api/books").json()) == 1


def test_bulk_create_allows_incomplete_records(client: TestClient) -> None:
    payload = [
        {"title": "", "author": "", "inventory_numbers": []},
        {"title": "Solo título"},
    ]

    response = client.post("/api/books/bulk", json=payload)

    assert response.status_code == 201
    assert response.json() == {"records": 2, "copies": 0, "skipped": 0}


def test_delete_all_records_clears_catalog(client: TestClient) -> None:
    payload = {
        "title": "Libro de prueba",
        "author": "Autora de prueba",
        "inventory_numbers": [62, 63],
        "inventory_source": "62-63",
    }
    assert client.post("/api/books", json=payload).status_code == 201
    assert client.post(
        "/api/books",
        json={**payload, "title": "Otro", "inventory_numbers": [70]},
    ).status_code == 201

    response = client.delete("/api/books")
    assert response.status_code == 200
    assert response.json() == {"deleted_records": 2, "deleted_copies": 3}
    assert client.get("/api/books").json() == []

    empty = client.delete("/api/books")
    assert empty.status_code == 200
    assert empty.json() == {"deleted_records": 0, "deleted_copies": 0}


def _payload(**overrides) -> dict:
    base = {
        "title": "Registro de prueba",
        "author": "Autora de prueba",
        "inventory_numbers": [80, 81],
        "inventory_source": "80-81",
    }
    base.update(overrides)
    return base


def test_trash_soft_deletes_restores_and_purges(client: TestClient) -> None:
    created = client.post("/api/books", json=_payload()).json()

    assert client.delete(f"/api/books/{created['id']}").status_code == 204
    assert client.get("/api/books").json() == []
    trash = client.get("/api/trash").json()
    assert [item["id"] for item in trash] == [created["id"]]
    assert trash[0]["deleted_at"] is not None

    reuse = client.post("/api/books", json=_payload(title="Reuso"))
    assert reuse.status_code == 201

    conflict = client.post(f"/api/trash/{created['id']}/restore")
    assert conflict.status_code == 409

    assert client.delete(f"/api/books/{reuse.json()['id']}").status_code == 204
    restored = client.post(f"/api/trash/{created['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == created["id"]
    assert restored.json()["deleted_at"] is None
    assert [item["id"] for item in client.get("/api/trash").json()] == [reuse.json()["id"]]

    client.delete(f"/api/books/{created['id']}")
    assert client.delete(f"/api/trash/{created['id']}").status_code == 204
    assert [item["id"] for item in client.get("/api/trash").json()] == [reuse.json()["id"]]


def test_empty_trash_removes_all_trashed_records(client: TestClient) -> None:
    created = client.post("/api/books", json=_payload()).json()
    assert client.delete(f"/api/books/{created['id']}").status_code == 204

    response = client.delete("/api/trash")
    assert response.status_code == 200
    assert response.json() == {"deleted_records": 1, "deleted_copies": 2}
    assert client.get("/api/trash").json() == []


def test_bulk_create_saves_several_records(client: TestClient) -> None:
    payload = [
        {**_payload(title="Primero", inventory_numbers=[80, 81], inventory_source="80-81")},
        {**_payload(title="Segundo", inventory_numbers=[90], inventory_source="90")},
        {**_payload(title="Tercero", inventory_numbers=[100, 101, 102], inventory_source="100-102")},
    ]

    response = client.post("/api/books/bulk", json=payload)

    assert response.status_code == 201
    assert response.json() == {"records": 3, "copies": 6, "skipped": 0}
    titles = [book["title"] for book in client.get("/api/books").json()]
    assert titles == ["Primero", "Segundo", "Tercero"]


def test_bulk_create_skip_ignores_conflicting_records(client: TestClient) -> None:
    assert client.post("/api/books", json=_payload(inventory_numbers=[80])).status_code == 201

    payload = [
        {**_payload(title="Choque", inventory_numbers=[80, 81], inventory_source="80-81")},
        {**_payload(title="Nuevo", inventory_numbers=[90, 91], inventory_source="90-91")},
    ]

    response = client.post("/api/books/bulk?on_conflict=skip", json=payload)

    assert response.status_code == 201
    assert response.json() == {"records": 1, "copies": 2, "skipped": 1}
    titles = [book["title"] for book in client.get("/api/books").json()]
    assert titles == ["Registro de prueba", "Nuevo"]


def test_bulk_create_rejects_duplicates_within_batch(client: TestClient) -> None:
    payload = [
        {**_payload(title="Primero", inventory_numbers=[80], inventory_source="80")},
        {**_payload(title="Segundo", inventory_numbers=[80, 81], inventory_source="80-81")},
    ]

    response = client.post("/api/books/bulk", json=payload)

    assert response.status_code == 409
    assert client.get("/api/books").json() == []


def test_bulk_create_rejects_numbers_already_in_use(client: TestClient) -> None:
    assert client.post("/api/books", json=_payload(inventory_numbers=[80])).status_code == 201

    response = client.post(
        "/api/books/bulk",
        json=[_payload(title="Choque", inventory_numbers=[80, 81])],
    )

    assert response.status_code == 409


def test_bulk_create_rejects_empty_batch(client: TestClient) -> None:
    assert client.post("/api/books/bulk", json=[]).status_code == 400


def test_csv_import_is_atomic_and_expands_inventory_ranges(client: TestClient) -> None:
    csv_text = (
        "Inventario de prueba\n"
        'N° Inv.,Autor,Título,Pie de Imprenta,Encuadernación / Observaciones\n'
        '62-71,Autor,"Título, con coma",Editorial,Cartoné\n'
    )
    response = client.post(
        "/api/import?mode=replace",
        files={"file": ("inventario.csv", csv_text.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    assert response.json() == {"records": 1, "copies": 10, "skipped": 0}
    imported = client.get("/api/books").json()
    assert imported[0]["inventory_numbers"] == list(range(62, 72))

    replacement = client.post(
        "/api/import?mode=replace",
        files={"file": ("nuevo.csv", csv_text.replace("62-71", "72").encode("utf-8"), "text/csv")},
    )
    assert replacement.status_code == 409
    assert "Confirmá el reemplazo" in replacement.json()["detail"]
    assert client.get("/api/books").json() == imported

    invalid_csv = (
        "N° Inv.,Autor,Título,Pie de Imprenta,Encuadernación / Observaciones\n"
        "1-3,Autor,Título A,Editorial,Cartoné\n"
        "3-5,Autor,Título B,Editorial,Cartoné\n"
    )
    rejected = client.post(
        "/api/import?mode=replace",
        files={"file": ("incorrecto.csv", invalid_csv.encode("utf-8"), "text/csv")},
    )
    assert rejected.status_code == 422
    assert client.get("/api/books").json() == imported


def test_csv_import_merge_ignores_existing_duplicates(client: TestClient) -> None:
    header = "N° Inv.,Autor,Título,Pie de Imprenta,Encuadernación / Observaciones\n"
    first = header + "62-63,Autor,Título A,Editorial,Cartoné\n"
    assert client.post(
        "/api/import?mode=replace",
        files={"file": ("base.csv", first.encode("utf-8"), "text/csv")},
    ).status_code == 200

    second = (
        header
        + "62,Autor,Duplicado,Editorial,Cartoné\n"
        + "90-91,Autor,Título Nuevo,Editorial,Cartoné\n"
    )
    response = client.post(
        "/api/import?mode=merge",
        files={"file": ("nuevo.csv", second.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    assert response.json() == {"records": 1, "copies": 2, "skipped": 1}
    titles = sorted(book["title"] for book in client.get("/api/books").json())
    assert titles == ["Título A", "Título Nuevo"]


def test_alembic_initial_migration_creates_catalog_tables(tmp_path, monkeypatch) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    database_path = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"

    command.upgrade(Config(str(config_path)), "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"bibliographic_records", "inventory_copies", "alembic_version"} <= tables
