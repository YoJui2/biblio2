from pathlib import Path

import pytest

from app.inventory import parse_inventory_csv, parse_inventory_range


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_inventory_range_expands_inclusively() -> None:
    assert parse_inventory_range("62-71") == list(range(62, 72))
    assert parse_inventory_range("INV-000062–INV-000071") == list(range(62, 72))
    assert parse_inventory_range("62") == [62]


@pytest.mark.parametrize("value", ["0", "71-62", "a-2", "1-10001"])
def test_invalid_inventory_range_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_inventory_range(value)


def test_source_csv_import_has_expected_record_and_copy_counts() -> None:
    source_csv = PROJECT_ROOT / "Inventario Consolidado de Libros - Sheet1.csv"
    imported = parse_inventory_csv(source_csv.read_text(encoding="utf-8-sig"))

    assert len(imported) == 195
    assert sum(len(record["inventory_numbers"]) for record in imported) == 751
    assert len({record["section"] for record in imported}) == 5
    grouped_record = next(
        record
        for record in imported
        if record["title"] == "Serie de Módulos de Facilitación Didáctica"
    )
    assert grouped_record["inventory_numbers"] == list(range(62, 72))


def test_duplicate_inventory_ranges_reject_entire_import() -> None:
    csv_text = (
        'Sección,N° Inv.,Autor,Título,Pie de Imprenta,Encuadernación / Observaciones\n'
        'General,62-64,Autor,Título A,Editorial,Cartoné\n'
        'General,64-66,Autor,Título B,Editorial,Cartoné\n'
    )

    with pytest.raises(ValueError, match="INV-000064 se repite"):
        parse_inventory_csv(csv_text)
