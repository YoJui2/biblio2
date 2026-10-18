import re
import unicodedata
from fastapi import HTTPException


INVENTORY_PATTERN = re.compile(
    r"^(?:INV\s*[-:]?\s*)?(\d+)"
    r"(?:\s*[-–—]\s*(?:INV\s*[-:]?\s*)?(\d+))?$",
    re.IGNORECASE,
)


def parse_inventory_range(value: str) -> list[int]:
    match = INVENTORY_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("Usá un número o rango, por ejemplo 62 o 62-71.")

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    if start < 1 or end < start:
        raise ValueError("El rango debe ser ascendente y usar números mayores que cero.")
    if end - start >= 10000:
        raise ValueError("El rango no puede superar los 10.000 ejemplares.")
    return list(range(start, end + 1))


def normalize_header(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    without_accents = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]", "", without_accents.lower())


def parse_inventory_csv(
    text: str, *, ignore_duplicates: bool = False
) -> list[dict[str, object]]:
    import csv
    import io

    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), strict=True))
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if {"autor", "titulo", "piedeimprenta"}.issubset(
                {normalize_header(cell) for cell in row}
            )
            and any("inv" in normalize_header(cell) for cell in row)
        ),
        None,
    )
    if header_index is None:
        raise ValueError(
            "No encontré los encabezados N° Inv., Autor, Título y Pie de Imprenta."
        )

    headers = [normalize_header(value) for value in rows[header_index]]

    def find_column(predicate) -> int:
        return next((index for index, name in enumerate(headers) if predicate(name)), -1)

    columns = {
        "inventory": find_column(lambda name: "inv" in name),
        "author": find_column(lambda name: name == "autor"),
        "title": find_column(lambda name: name == "titulo"),
        "imprint": find_column(lambda name: "imprenta" in name),
        "binding": find_column(
            lambda name: "encuadernacion" in name or "observaciones" in name
        ),
    }
    imported: list[dict[str, object]] = []
    seen: dict[int, int] = {}
    errors: list[str] = []

    for row_index, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(column: int) -> str:
            return row[column].strip() if 0 <= column < len(row) else ""

        record = {key: cell(column) for key, column in columns.items()}
        if not record["inventory"] and not record["title"] and not record["author"]:
            continue
        required = ("inventory", "author", "title")
        missing = [name for name in required if not record[name]]
        if missing:
            errors.append(f"fila {row_index}: faltan campos obligatorios")
            continue
        try:
            numbers = parse_inventory_range(str(record["inventory"]))
        except ValueError as error:
            errors.append(f"fila {row_index} ({record['inventory']}): {error}")
            continue

        duplicate = next((number for number in numbers if number in seen), None)
        if duplicate is not None:
            if ignore_duplicates:
                continue
            errors.append(
                f"fila {row_index}: INV-{duplicate:06d} se repite "
                f"(también en fila {seen[duplicate]})"
            )
            continue
        for number in numbers:
            seen[number] = row_index

        imported.append(
            {
                **record,
                "inventory_numbers": numbers,
                "inventory_source": str(record["inventory"]),
            }
        )

    if errors:
        preview = "; ".join(errors[:5])
        remaining = len(errors) - 5
        suffix = f"; y {remaining} errores más" if remaining > 0 else ""
        raise ValueError(f"No se importó el archivo: {preview}{suffix}.")
    if not imported:
        raise ValueError("El archivo no contiene registros para importar.")
    return imported


def record_to_dict(record) -> dict[str, object]:
    return {
        "id": record.id,
        "title": record.title,
        "author": record.author,
        "imprint": record.imprint or "",
        "binding": record.binding or "",
        "inventory_source": record.inventory_source or "",
        "inventory_numbers": [copy.inventory_number for copy in record.copies],
        "deleted_at": record.deleted_at,
    }


def validate_numbers_not_in_use(
    numbers: list[int], in_use: set[int]
) -> None:
    duplicate = next((number for number in numbers if number in in_use), None)
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail=f"INV-{duplicate:06d} ya está asignado a otro registro.",
        )
