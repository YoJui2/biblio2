import json

from fastapi.testclient import TestClient
import pytest

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'test-backup.db'}"
    application = create_app(database_url, create_schema=True)
    with TestClient(application) as test_client:
        yield test_client


def _payload(**overrides) -> dict:
    base = {
        "title": "Registro de prueba",
        "author": "Autora de prueba",
        "inventory_numbers": [80, 81],
        "inventory_source": "80-81",
    }
    base.update(overrides)
    return base


def _create(client: TestClient, **overrides) -> dict:
    response = client.post("/api/books", json=_payload(**overrides))
    assert response.status_code == 201
    return response.json()


def _backup_document(records: list[dict]) -> dict:
    return {
        "format": "biblioteca-inventario",
        "version": 1,
        "created_at": "2026-10-21T10:00:00+00:00",
        "records": records,
    }


def _restore(
    client: TestClient,
    payload,
    *,
    mode: str = "replace",
    confirm: bool = True,
    filename: str = "respaldo.json",
):
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return client.post(
        f"/api/backup/restore?mode={mode}",
        data={"confirm_replace": "true" if confirm else "false"},
        files={"file": (filename, content, "application/json")},
    )


def _record(title: str, numbers: list[int], deleted_at=None) -> dict:
    return {
        "title": title,
        "author": "Autora",
        "imprint": "",
        "binding": "",
        "inventory_source": "",
        "inventory_numbers": numbers,
        "deleted_at": deleted_at,
    }


def test_backup_downloads_catalog_and_trash_as_json(client: TestClient) -> None:
    active = _create(client, title="Activo")
    trashed = _create(client, title="En papelera", inventory_numbers=[90])
    assert client.delete(f"/api/books/{trashed['id']}").status_code == 204

    response = client.get("/api/backup")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    assert "respaldo-biblioteca-" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"

    backup = response.json()
    assert backup["format"] == "biblioteca-inventario"
    assert backup["version"] == 1
    assert backup["summary"] == {
        "records": 1,
        "copies": 2,
        "trashed_records": 1,
        "trashed_copies": 1,
    }
    assert [item["title"] for item in backup["records"]] == [
        active["title"],
        trashed["title"],
    ]
    assert backup["records"][0]["deleted_at"] is None
    assert backup["records"][1]["deleted_at"] is not None


def test_restore_replace_returns_system_to_backup_state(client: TestClient) -> None:
    kept = _create(client, title="Se conserva")
    trashed = _create(client, title="Estaba en papelera", inventory_numbers=[90])
    assert client.delete(f"/api/books/{trashed['id']}").status_code == 204
    backup = client.get("/api/backup").json()

    newer = _create(client, title="Creado después", inventory_numbers=[100])
    assert len(client.get("/api/books").json()) == 2

    response = _restore(client, backup)

    assert response.status_code == 200
    assert response.json() == {
        "mode": "replace",
        "records": 2,
        "copies": 3,
        "skipped": 0,
    }
    catalog = client.get("/api/books").json()
    assert [item["title"] for item in catalog] == [kept["title"]]
    assert catalog[0]["inventory_numbers"] == [80, 81]
    assert [item["title"] for item in client.get("/api/trash").json()] == [
        trashed["title"]
    ]
    assert all(item["id"] != newer["id"] for item in catalog)


def test_restore_replace_requires_confirmation_with_existing_data(
    client: TestClient,
) -> None:
    _create(client, title="Existente")
    backup = _backup_document([_record("Del respaldo", [80])])

    blocked = _restore(client, backup, confirm=False)

    assert blocked.status_code == 409
    assert "Confirmá el reemplazo" in blocked.json()["detail"]
    assert [item["title"] for item in client.get("/api/books").json()] == [
        "Existente"
    ]


def test_restore_replace_works_on_empty_system_without_confirmation(
    client: TestClient,
) -> None:
    backup = _backup_document([_record("Del respaldo", [80, 81])])

    response = _restore(client, backup, confirm=False)

    assert response.status_code == 200
    assert response.json() == {
        "mode": "replace",
        "records": 1,
        "copies": 2,
        "skipped": 0,
    }
    assert [item["title"] for item in client.get("/api/books").json()] == [
        "Del respaldo"
    ]


def test_restore_merge_skips_conflicts_and_adds_new_records(
    client: TestClient,
) -> None:
    existing = _create(client, title="Existente", inventory_numbers=[80])
    backup = _backup_document(
        [
            _record("Repetido", [80, 81]),
            _record("Nuevo", [90, 91]),
            _record("Papelera", [95], deleted_at="2026-10-20T12:00:00"),
        ]
    )

    response = _restore(client, backup, mode="merge")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "merge",
        "records": 2,
        "copies": 3,
        "skipped": 1,
    }
    assert [item["title"] for item in client.get("/api/books").json()] == [
        existing["title"],
        "Nuevo",
    ]
    assert [item["title"] for item in client.get("/api/trash").json()] == [
        "Papelera"
    ]


def test_restore_accepts_bare_record_list(client: TestClient) -> None:
    response = _restore(client, [_record("Lista simple", [70])])

    assert response.status_code == 200
    assert [item["title"] for item in client.get("/api/books").json()] == [
        "Lista simple"
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"format": "otro-sistema", "version": 1, "records": []},
        {"format": "biblioteca-inventario", "version": 99, "records": []},
        {"format": "biblioteca-inventario", "version": 1, "records": []},
        _backup_document([_record("Repetido", [80, 80])]),
        _backup_document([_record("Uno", [80]), _record("Dos", [80])]),
        _backup_document([_record("Negativo", [-5])]),
        _backup_document([{**_record("Sin lista", []), "inventory_numbers": "80"}]),
        _backup_document([{**_record("Fecha mala", [80]), "deleted_at": "ayer"}]),
    ],
)
def test_restore_rejects_invalid_backups(client: TestClient, payload) -> None:
    response = _restore(client, payload)

    assert response.status_code == 422
    assert client.get("/api/books").json() == []


def test_restore_rejects_non_json_content_and_wrong_extension(
    client: TestClient,
) -> None:
    not_json = client.post(
        "/api/backup/restore",
        data={"confirm_replace": "true"},
        files={"file": ("respaldo.json", b"no es json", "application/json")},
    )
    assert not_json.status_code == 422

    wrong_name = client.post(
        "/api/backup/restore",
        data={"confirm_replace": "true"},
        files={"file": ("respaldo.txt", b"{}", "text/plain")},
    )
    assert wrong_name.status_code == 400
