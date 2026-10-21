from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import csv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import chatbot, ocr
from app.config import PROJECT_ROOT, Settings
from app.export import build_catalog_workbook
from app.db import Base, make_session_factory, session_dependency
from app.inventory import (
    parse_inventory_csv,
    record_to_dict,
    validate_numbers_not_in_use,
)
from app.models import BibliographicRecord, InventoryCopy
from app.schemas import (
    BulkCreateResult,
    ChatReply,
    ChatRequest,
    ChatbotStatus,
    DeleteAllResult,
    ImportResult,
    OCRScanResult,
    OCRStatus,
    RecordCreate,
    RecordRead,
    RecordUpdate,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _active_inventory_numbers(
    session: Session, exclude_record_id: int | None = None
) -> set[int]:
    query = (
        select(InventoryCopy.inventory_number)
        .join(BibliographicRecord, InventoryCopy.record_id == BibliographicRecord.id)
        .where(BibliographicRecord.deleted_at.is_(None))
    )
    if exclude_record_id is not None:
        query = query.where(InventoryCopy.record_id != exclude_record_id)
    return set(session.scalars(query).all())


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
            .where(BibliographicRecord.deleted_at.is_(None))
            .options(selectinload(BibliographicRecord.copies))
            .order_by(BibliographicRecord.id)
        ).all()
        return [record_to_dict(record) for record in records]

    @application.get("/api/export.xlsx", tags=["catálogo"])
    def export_catalog_excel(
        session: Annotated[Session, Depends(dependency)],
        q: Annotated[str, Query()] = "",
    ) -> Response:
        records = session.scalars(
            select(BibliographicRecord)
            .where(BibliographicRecord.deleted_at.is_(None))
            .options(selectinload(BibliographicRecord.copies))
            .order_by(BibliographicRecord.id)
        ).all()
        content = build_catalog_workbook(
            [record_to_dict(record) for record in records],
            query=q,
        )
        suffix = "-filtrado" if q.strip() else ""
        filename = f"inventario-biblioteca{suffix}-{datetime.now():%Y-%m-%d}.xlsx"
        return Response(
            content=content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

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
            _active_inventory_numbers(session),
        )
        record = BibliographicRecord(
            section="",
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

    @application.post(
        "/api/books/bulk",
        response_model=BulkCreateResult,
        status_code=201,
        tags=["catálogo"],
    )
    def create_records_bulk(
        payload: list[RecordCreate],
        session: Annotated[Session, Depends(dependency)],
        on_conflict: Annotated[str, Query(pattern="^(error|skip)$")] = "error",
    ):
        if not payload:
            raise HTTPException(
                status_code=400, detail="No hay registros para guardar."
            )
        skipped = 0
        if on_conflict == "skip":
            existing = _active_inventory_numbers(session)
            pending: list[RecordCreate] = []
            for item in payload:
                if any(number in existing for number in item.inventory_numbers):
                    skipped += 1
                    continue
                existing.update(item.inventory_numbers)
                pending.append(item)
            payload = pending
        else:
            numbers = [
                number for item in payload for number in item.inventory_numbers
            ]
            if len(numbers) != len(set(numbers)):
                raise HTTPException(
                    status_code=409,
                    detail="Hay números de inventario repetidos entre los registros.",
                )
            validate_numbers_not_in_use(numbers, _active_inventory_numbers(session))
        numbers = [
            number for item in payload for number in item.inventory_numbers
        ]
        records = [
            BibliographicRecord(
                section="",
                title=item.title,
                author=item.author,
                imprint=item.imprint,
                binding=item.binding,
                inventory_source=item.inventory_source,
                copies=[
                    InventoryCopy(inventory_number=number)
                    for number in item.inventory_numbers
                ],
            )
            for item in payload
        ]
        session.add_all(records)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Uno o más números de inventario ya están asignados.",
            ) from error
        return BulkCreateResult(
            records=len(records), copies=len(numbers), skipped=skipped
        )

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
        in_use = _active_inventory_numbers(session, exclude_record_id=record_id)
        validate_numbers_not_in_use(payload.inventory_numbers, in_use)
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
        "/api/books",
        response_model=DeleteAllResult,
        tags=["catálogo"],
    )
    def delete_all_records(
        session: Annotated[Session, Depends(dependency)],
    ):
        active = BibliographicRecord.deleted_at.is_(None)
        records = (
            session.scalar(
                select(func.count())
                .select_from(BibliographicRecord)
                .where(active)
            )
            or 0
        )
        copies = (
            session.scalar(
                select(func.count())
                .select_from(InventoryCopy)
                .join(
                    BibliographicRecord,
                    InventoryCopy.record_id == BibliographicRecord.id,
                )
                .where(active)
            )
            or 0
        )
        if records:
            session.execute(
                update(BibliographicRecord)
                .where(active)
                .values(deleted_at=_utcnow())
            )
            session.commit()
        return DeleteAllResult(deleted_records=records, deleted_copies=copies)

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
        if record is None or record.deleted_at is not None:
            raise HTTPException(status_code=404, detail="No se encontró el registro.")
        record.deleted_at = _utcnow()
        session.commit()

    @application.get(
        "/api/trash",
        response_model=list[RecordRead],
        tags=["papelera"],
    )
    def list_trash(session: Annotated[Session, Depends(dependency)]):
        records = session.scalars(
            select(BibliographicRecord)
            .where(BibliographicRecord.deleted_at.is_not(None))
            .options(selectinload(BibliographicRecord.copies))
            .order_by(
                BibliographicRecord.deleted_at.desc(),
                BibliographicRecord.id.desc(),
            )
        ).all()
        return [record_to_dict(record) for record in records]

    @application.post(
        "/api/trash/{record_id}/restore",
        response_model=RecordRead,
        tags=["papelera"],
    )
    def restore_record(
        record_id: int,
        session: Annotated[Session, Depends(dependency)],
    ):
        record = session.scalar(
            select(BibliographicRecord)
            .where(BibliographicRecord.id == record_id)
            .options(selectinload(BibliographicRecord.copies))
        )
        if record is None or record.deleted_at is None:
            raise HTTPException(
                status_code=404,
                detail="No se encontró el registro en la papelera.",
            )
        validate_numbers_not_in_use(
            [copy.inventory_number for copy in record.copies],
            _active_inventory_numbers(session),
        )
        record.deleted_at = None
        session.commit()
        session.refresh(record)
        return record_to_dict(record)

    @application.delete(
        "/api/trash/{record_id}",
        status_code=204,
        tags=["papelera"],
    )
    def purge_record(
        record_id: int,
        session: Annotated[Session, Depends(dependency)],
    ) -> None:
        record = session.get(BibliographicRecord, record_id)
        if record is None or record.deleted_at is None:
            raise HTTPException(
                status_code=404,
                detail="No se encontró el registro en la papelera.",
            )
        session.delete(record)
        session.commit()

    @application.delete(
        "/api/trash",
        response_model=DeleteAllResult,
        tags=["papelera"],
    )
    def empty_trash(
        session: Annotated[Session, Depends(dependency)],
    ):
        trashed = BibliographicRecord.deleted_at.is_not(None)
        trash_ids = select(BibliographicRecord.id).where(trashed)
        records = (
            session.scalar(
                select(func.count()).select_from(BibliographicRecord).where(trashed)
            )
            or 0
        )
        copies = (
            session.scalar(
                select(func.count())
                .select_from(InventoryCopy)
                .where(InventoryCopy.record_id.in_(trash_ids))
            )
            or 0
        )
        if records:
            session.execute(
                delete(InventoryCopy).where(InventoryCopy.record_id.in_(trash_ids))
            )
            session.execute(delete(BibliographicRecord).where(trashed))
            session.commit()
        return DeleteAllResult(deleted_records=records, deleted_copies=copies)

    @application.post(
        "/api/import",
        response_model=ImportResult,
        tags=["importación"],
    )
    async def replace_catalog(
        session: Annotated[Session, Depends(dependency)],
        file: Annotated[UploadFile, File()],
        confirm_replace: Annotated[bool, Form()] = False,
        mode: Annotated[str, Query(pattern="^(replace|merge)$")] = "replace",
    ):
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
            records = parse_inventory_csv(text, ignore_duplicates=(mode == "merge"))
        except (ValueError, csv.Error) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        pending: list[dict[str, object]] = []
        skipped = 0
        try:
            with session.begin():
                if mode == "replace":
                    current_count = session.scalar(
                        select(func.count())
                        .select_from(BibliographicRecord)
                        .where(BibliographicRecord.deleted_at.is_(None))
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
                    pending = records
                else:
                    existing = _active_inventory_numbers(session)
                    for item in records:
                        numbers = item["inventory_numbers"]
                        assert isinstance(numbers, list)
                        if any(number in existing for number in numbers):
                            skipped += 1
                            continue
                        existing.update(numbers)
                        pending.append(item)
                for item in pending:
                    numbers = item["inventory_numbers"]
                    assert isinstance(numbers, list)
                    record = BibliographicRecord(
                        section="",
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
                detail="No se pudo importar el catálogo por un conflicto de inventario.",
            ) from error
        copies = sum(len(item["inventory_numbers"]) for item in pending)
        return ImportResult(records=len(pending), copies=copies, skipped=skipped)

    @application.get("/api/ocr/status", response_model=OCRStatus, tags=["ocr"])
    def ocr_status() -> dict[str, object]:
        return ocr.ocr_status()

    @application.post(
        "/api/ocr/scan",
        response_model=OCRScanResult,
        tags=["ocr"],
    )
    async def scan_record_image(
        file: Annotated[UploadFile, File()],
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
        try:
            return ocr.scan_image_bytes(content)
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

    @application.get(
        "/api/chat/status",
        response_model=ChatbotStatus,
        tags=["chatbot"],
    )
    def chatbot_status() -> dict[str, object]:
        return chatbot.chatbot_status()

    @application.post(
        "/api/chat",
        response_model=ChatReply,
        tags=["chatbot"],
    )
    def ask_chatbot(
        payload: ChatRequest,
        session: Annotated[Session, Depends(dependency)],
    ):
        records = session.scalars(
            select(BibliographicRecord)
            .where(BibliographicRecord.deleted_at.is_(None))
            .options(selectinload(BibliographicRecord.copies))
            .order_by(BibliographicRecord.id)
        ).all()
        catalog = [record_to_dict(record) for record in records]
        history = [message.model_dump() for message in payload.history]
        try:
            result = chatbot.answer_question(catalog, payload.message, history)
        except chatbot.ChatbotUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except chatbot.ChatbotError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return ChatReply(reply=result["reply"], model=result["model"])

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(
            ui_directory / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/styles.css", include_in_schema=False)
    def styles():
        return FileResponse(
            ui_directory / "styles.css",
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/app.js", include_in_schema=False)
    def script():
        return FileResponse(
            ui_directory / "app.js",
            media_type="text/javascript; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    return application


app = create_app()
