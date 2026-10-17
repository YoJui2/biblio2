from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import csv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import ocr
from app.config import PROJECT_ROOT, Settings
from app.db import Base, make_session_factory, session_dependency
from app.inventory import (
    parse_inventory_csv,
    record_to_dict,
    validate_numbers_not_in_use,
)
from app.models import BibliographicRecord, InventoryCopy
from app.schemas import (
    ImportResult,
    OCRResult,
    OCRStatus,
    RecordCreate,
    RecordRead,
    RecordUpdate,
)


def create_app(
    database_url: str | None = None,
    *,
    frontend_dir: Path | None = None,
    create_schema: bool = False,
) -> FastAPI:
    settings = Settings() if database_url is None else None
    configured_url = database_url if database_url is not None else settings.database_url
    engine, session_factory = make_session_factory(configured_url)
    dependency = session_dependency(session_factory)
    ui_directory = frontend_dir or (settings.frontend_dir if settings else PROJECT_ROOT)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if create_schema:
            Base.metadata.create_all(engine)
        yield
        engine.dispose()

    application = FastAPI(
        title="Inventario de Biblioteca",
        description="API local para gestionar el catálogo y los ejemplares.",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.engine = engine

    @application.get("/api/health", tags=["sistema"])
    def health(session: Annotated[Session, Depends(dependency)]) -> dict[str, str]:
        session.execute(select(1))
        return {"status": "ok", "database": "connected"}

    @application.get("/api/books", response_model=list[RecordRead], tags=["catálogo"])
    def list_records(session: Annotated[Session, Depends(dependency)]):
        records = session.scalars(
            select(BibliographicRecord)
            .options(selectinload(BibliographicRecord.copies))
            .order_by(BibliographicRecord.id)
        ).all()
        return [record_to_dict(record) for record in records]

    @application.post(
        "/api/books",
        response_model=RecordRead,
        status_code=201,
        tags=["catálogo"],
    )
    def create_record(
        payload: RecordCreate,
        session: Annotated[Session, Depends(dependency)],
    ):
        validate_numbers_not_in_use(
            payload.inventory_numbers,
            set(session.scalars(select(InventoryCopy.inventory_number)).all()),
        )
        record = BibliographicRecord(
            section=payload.section,
            title=payload.title,
            author=payload.author,
            imprint=payload.imprint,
            binding=payload.binding,
            inventory_source=payload.inventory_source,
            copies=[
                InventoryCopy(inventory_number=number)
                for number in payload.inventory_numbers
            ],
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Uno o más números de inventario ya están asignados.",
            ) from error
        session.refresh(record)
        return record_to_dict(record)

    @application.put(
        "/api/books/{record_id}",
        response_model=RecordRead,
        tags=["catálogo"],
    )
    def update_record(
        record_id: int,
        payload: RecordUpdate,
        session: Annotated[Session, Depends(dependency)],
    ):
        record = session.scalar(
            select(BibliographicRecord)
            .where(BibliographicRecord.id == record_id)
            .options(selectinload(BibliographicRecord.copies))
        )
        if record is None:
            raise HTTPException(status_code=404, detail="No se encontró el registro.")
        in_use = set(
            session.scalars(
                select(InventoryCopy.inventory_number).where(
                    InventoryCopy.record_id != record_id
                )
            ).all()
        )
        validate_numbers_not_in_use(payload.inventory_numbers, in_use)
        record.section = payload.section
        record.title = payload.title
        record.author = payload.author
        record.imprint = payload.imprint
        record.binding = payload.binding
        record.inventory_source = payload.inventory_source
        existing_copies = {copy.inventory_number: copy for copy in record.copies}
        record.copies = [
            existing_copies.get(number) or InventoryCopy(inventory_number=number)
            for number in payload.inventory_numbers
        ]
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Uno o más números de inventario ya están asignados.",
            ) from error
        session.refresh(record)
        return record_to_dict(record)

    @application.delete(
        "/api/books/{record_id}",
        status_code=204,
        tags=["catálogo"],
    )
    def delete_record(
        record_id: int,
        session: Annotated[Session, Depends(dependency)],
    ) -> None:
        record = session.get(BibliographicRecord, record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No se encontró el registro.")
        session.delete(record)
        session.commit()

    @application.post(
        "/api/import",
        response_model=ImportResult,
        tags=["importación"],
    )
    async def replace_catalog(
        session: Annotated[Session, Depends(dependency)],
        file: Annotated[UploadFile, File()],
        confirm_replace: Annotated[bool, Form()] = False,
        mode: Annotated[str, Query(pattern="^replace$")] = "replace",
    ):
        del mode
        if not file.filename or Path(file.filename).suffix.lower() != ".csv":
            raise HTTPException(status_code=400, detail="Seleccioná un archivo .csv.")
        content = await file.read(10_000_001)
        if len(content) > 10_000_000:
            raise HTTPException(status_code=413, detail="El CSV supera el límite de 10 MB.")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise HTTPException(
                status_code=400,
                detail="El CSV debe estar guardado con codificación UTF-8.",
            ) from error
        try:
            records = parse_inventory_csv(text)
        except (ValueError, csv.Error) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        total_numbers = sum(len(record["inventory_numbers"]) for record in records)
        sections = {str(record["section"]) for record in records}
        try:
            with session.begin():
                current_count = session.scalar(
                    select(func.count()).select_from(BibliographicRecord)
                ) or 0
                if current_count and not confirm_replace:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"MySQL ya contiene {current_count} registros. "
                            "Confirmá el reemplazo antes de volver a importar."
                        ),
                    )
                session.execute(delete(InventoryCopy))
                session.execute(delete(BibliographicRecord))
                for item in records:
                    numbers = item["inventory_numbers"]
                    assert isinstance(numbers, list)
                    record = BibliographicRecord(
                        section=str(item["section"]),
                        title=str(item["title"]),
                        author=str(item["author"]),
                        imprint=str(item["imprint"]),
                        binding=str(item["binding"]),
                        inventory_source=str(item["inventory_source"]),
                        copies=[
                            InventoryCopy(inventory_number=number)
                            for number in numbers
                        ],
                    )
                    session.add(record)
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="No se pudo reemplazar el catálogo por un conflicto de inventario.",
            ) from error
        return ImportResult(
            records=len(records),
            copies=total_numbers,
            sections=len(sections),
        )

    @application.get("/api/ocr/status", response_model=OCRStatus, tags=["ocr"])
    def ocr_status() -> dict[str, object]:
        return ocr.ocr_status()

    @application.post(
        "/api/ocr/scan",
        response_model=OCRResult,
        tags=["ocr"],
    )
    async def scan_record_image(
        file: Annotated[UploadFile, File()],
        sections: Annotated[str | None, Form()] = None,
    ):
        suffix = Path(file.filename or "").suffix.lower()
        content_type = file.content_type or ""
        allowed_suffixes = {
            ".png",
            ".jpg",
            ".jpeg",
            ".tif",
            ".tiff",
            ".bmp",
            ".webp",
            ".gif",
            ".heic",
            ".heif",
            ".avif",
        }
        if suffix not in allowed_suffixes and not content_type.startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail=f"Seleccioná una imagen escaneada ({ocr.SUPPORTED_FORMATS}).",
            )
        content = await file.read(ocr.MAX_IMAGE_BYTES + 1)
        if len(content) > ocr.MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="La imagen supera el límite de 20 MB. Reducila o escaneá a menor resolución.",
            )
        if not content:
            raise HTTPException(status_code=400, detail="El archivo llegó vacío.")
        known_sections = [
            value.strip() for value in (sections or "").split(",") if value.strip()
        ]
        try:
            return ocr.scan_image_bytes(content, known_sections)
        except ocr.OcrUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ocr.OcrError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except HTTPException:
            raise
        except Exception as error:  # noqa: BLE001 - siempre devolvemos JSON legible
            raise HTTPException(
                status_code=500,
                detail=f"Error inesperado al leer la imagen: {error}",
            ) from error

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(ui_directory / "index.html")

    @application.get("/styles.css", include_in_schema=False)
    def styles():
        return FileResponse(ui_directory / "styles.css", media_type="text/css")

    @application.get("/app.js", include_in_schema=False)
    def script():
        return FileResponse(
            ui_directory / "app.js",
            media_type="text/javascript; charset=utf-8",
        )

    return application


app = create_app()
