from fastapi.testclient import TestClient

from app.main import create_app
from app.ocr import interpret_ocr_records_text, interpret_ocr_text


def make_client(tmp_path) -> TestClient:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'test-ocr.db'}"
    application = create_app(database_url, create_schema=True)
    return TestClient(application)


def test_interpret_labeled_scan_returns_catalog_fields() -> None:
    text = (
        "INV-000062\n"
        "Serie de Módulos\n"
        "Autor: Juan Pérez\n"
        "Buenos Aires: Editorial, 2020\n"
        "Cartoné\n"
    )

    result = interpret_ocr_text(text)

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


def test_interpret_reads_several_numbers_from_one_line() -> None:
    result = interpret_ocr_text("hola mundo\n67 67 78 78\nCartoné")

    assert result["inventory_numbers"] == [67, 78]
    assert result["title"] == "hola mundo"
    assert result["binding"] == "Cartoné"


def test_interpret_reads_labeled_list_of_numbers() -> None:
    text = (
        "Ninv: 62, 65, 78, 79\n"
        "Título: Obra en once tomos\n"
        "Autor: Autora\n"
    )

    result = interpret_ocr_text(text)

    assert result["inventory_numbers"] == [62, 65, 78, 79]
    assert result["inventory_source"] == "62, 65, 78-79"


def test_interpret_reads_labeled_range_without_losing_numbers() -> None:
    result = interpret_ocr_text("Ninv: 62-72\nTítulo: Obra\nAutor: Autora")

    assert result["inventory_numbers"] == list(range(62, 73))
    assert result["inventory_source"] == "62-72"


def test_interpret_detects_title_and_author_from_lines() -> None:
    text = "Matemática 6\nMaría López\n"

    result = interpret_ocr_text(text)

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
    assert isinstance(body["available"], bool)
    assert body["engine"] in ("trocr", "deepseek")
    assert body["model"]
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


def test_interpret_records_splits_pipe_rows() -> None:
    text = (
        "166, 182 | Vasallo, L. | Nociones de química | Cartón | Bs.As.: Kapelusz, 1997\n"
        "200 | María López | Matemática 6 | Cartoné | Buenos Aires: Estrada, 2010\n"
    )

    records = interpret_ocr_records_text(text)

    assert len(records) == 2
    assert records[0]["inventory_numbers"] == [166, 182]
    assert records[0]["author"] == "Vasallo, L."
    assert records[0]["title"] == "Nociones de química"
    assert records[0]["binding"] == "Cartón"
    assert records[0]["imprint"] == "Bs.As.: Kapelusz, 1997"
    assert records[1]["inventory_numbers"] == [200]
    assert records[1]["title"] == "Matemática 6"


def test_interpret_records_strips_list_markers_and_pads_fields() -> None:
    records = interpret_ocr_records_text("1. 62, 65 | Autora | Obra en tomos\n")

    assert len(records) == 1
    assert records[0]["inventory_numbers"] == [62, 65]
    assert records[0]["title"] == "Obra en tomos"
    assert records[0]["author"] == "Autora"
    assert records[0]["imprint"] == ""
    assert records[0]["inventory_source"] == "62, 65"


def test_interpret_records_expands_ranges_per_row() -> None:
    records = interpret_ocr_records_text("62-64 | Autora | Obra\n")

    assert records[0]["inventory_numbers"] == [62, 63, 64]
    assert records[0]["inventory_source"] == "62-64"


def test_interpret_records_falls_back_to_single_record() -> None:
    records = interpret_ocr_records_text("Ninv: 62\nTítulo: Obra\nAutor: Autora")

    assert len(records) == 1
    assert records[0]["inventory_numbers"] == [62]
    assert records[0]["title"] == "Obra"
    assert records[0]["author"] == "Autora"


def test_interpret_records_warns_on_missing_fields() -> None:
    records = interpret_ocr_records_text(" |  | Solo título |  | \n")

    assert len(records) == 1
    assert records[0]["title"] == "Solo título"
    assert records[0]["inventory_numbers"] == []
    assert "Sin números de inventario." in records[0]["warnings"]
    assert "Sin autor." in records[0]["warnings"]
