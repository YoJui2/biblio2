from fastapi.testclient import TestClient

from app.main import create_app
from app.ocr import interpret_ocr_text


def make_client(tmp_path) -> TestClient:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'test-ocr.db'}"
    application = create_app(database_url, create_schema=True)
    return TestClient(application)


def test_interpret_labeled_scan_returns_catalog_fields() -> None:
    text = (
        "INV-000062\n"
        "Serie de Módulos\n"
        "Autor: Juan Pérez\n"
        "Sección: Educación\n"
        "Buenos Aires: Editorial, 2020\n"
        "Cartoné\n"
    )

    result = interpret_ocr_text(text)

    assert result["section"] == "Educación"
    assert result["title"] == "Serie de Módulos"
    assert result["author"] == "Juan Pérez"
    assert result["imprint"] == "Buenos Aires: Editorial, 2020"
    assert result["binding"] == "Cartoné"
    assert result["inventory_numbers"] == [62]
    assert result["warnings"] == []


def test_interpret_expands_inventory_range_token() -> None:
    result = interpret_ocr_text("62–71")

    assert result["inventory_numbers"] == list(range(62, 72))
    assert result["inventory_source"] == "62–71"


def test_interpret_detects_known_section_and_title_author() -> None:
    text = "Educación\nMatemática 6\nMaría López\n"

    result = interpret_ocr_text(text, known_sections=["Educación", "Lengua"])

    assert result["section"] == "Educación"
    assert result["title"] == "Matemática 6"
    assert result["author"] == "María López"


def test_interpret_splits_title_and_author_separator() -> None:
    result = interpret_ocr_text("El Principito / Antoine de Saint-Exupéry")

    assert result["title"] == "El Principito"
    assert result["author"] == "Antoine de Saint-Exupéry"


def test_interpret_warns_when_nothing_is_recognized() -> None:
    result = interpret_ocr_text("   \n  ")

    assert result["inventory_numbers"] == []
    assert len(result["warnings"]) == 3


def test_ocr_status_reports_availability(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/ocr/status")

    assert response.status_code == 200
    body = response.json()
    assert "available" in body
    assert body["default_languages"] == "spa+eng"
    assert "PNG" in (body["formats"] or "")


def test_ocr_scan_rejects_non_image_files(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/ocr/scan",
            files={"file": ("notas.txt", b"no es una imagen", "text/plain")},
        )

    assert response.status_code == 400


def test_ocr_scan_returns_readable_json_error_for_corrupt_image(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/ocr/scan",
            files={"file": ("foto.png", b"esto no es una imagen", "image/png")},
        )

    assert response.status_code in (422, 503)
    assert "detail" in response.json()


def test_ocr_scan_rejects_empty_file(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/ocr/scan",
            files={"file": ("foto.png", b"", "image/png")},
        )

    assert response.status_code == 400
