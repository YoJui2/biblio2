from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BibliographicRecord(Base):
    __tablename__ = "bibliographic_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    section: Mapped[str] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    author: Mapped[str] = mapped_column(String(500))
    imprint: Mapped[str] = mapped_column(Text, default="")
    binding: Mapped[str] = mapped_column(String(300), default="")
    inventory_source: Mapped[str] = mapped_column(String(120), default="")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    copies: Mapped[list["InventoryCopy"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InventoryCopy.inventory_number",
    )


class InventoryCopy(Base):
    __tablename__ = "inventory_copies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    inventory_number: Mapped[int] = mapped_column(Integer, nullable=False)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("bibliographic_records.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    record: Mapped[BibliographicRecord] = relationship(back_populates="copies")
