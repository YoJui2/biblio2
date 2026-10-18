from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecordFields(BaseModel):
    section: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(min_length=1, max_length=500)
    imprint: str = Field(default="", max_length=10000)
    binding: str = Field(default="", max_length=300)
    inventory_numbers: list[int] = Field(min_length=1, max_length=10000)
    inventory_source: str = Field(default="", max_length=120)

    @field_validator("section", "title", "author")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Este campo no puede estar vacío.")
        return value

    @field_validator("inventory_numbers")
    @classmethod
    def inventory_numbers_are_valid(cls, values: list[int]) -> list[int]:
        if any(number < 1 for number in values):
            raise ValueError("Los números de inventario deben ser mayores que cero.")
        if len(values) != len(set(values)):
            raise ValueError("El registro contiene números de inventario repetidos.")
        return sorted(values)


class RecordCreate(RecordFields):
    pass


class RecordUpdate(RecordFields):
    pass


class RecordRead(BaseModel):
    id: int
    section: str
    title: str
    author: str
    imprint: str
    binding: str
    inventory_source: str
    inventory_numbers: list[int]
    deleted_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ImportResult(BaseModel):
    records: int
    copies: int
    sections: int


class BulkCreateResult(BaseModel):
    records: int
    copies: int


class DeleteAllResult(BaseModel):
    deleted_records: int
    deleted_copies: int


class OCRStatus(BaseModel):
    available: bool
    engine: str = "trocr"
    model: str | None = None
    formats: str | None = None
    detail: str | None = None


class OCRResult(BaseModel):
    status: str
    engine: str = "trocr"
    confidence: float
    raw_text: str
    width: int
    height: int
    section: str = ""
    title: str = ""
    author: str = ""
    imprint: str = ""
    binding: str = ""
    inventory_numbers: list[int] = Field(default_factory=list)
    inventory_source: str = ""
    warnings: list[str] = Field(default_factory=list)


class OCRRecord(BaseModel):
    section: str = ""
    title: str = ""
    author: str = ""
    imprint: str = ""
    binding: str = ""
    inventory_numbers: list[int] = Field(default_factory=list)
    inventory_source: str = ""
    warnings: list[str] = Field(default_factory=list)


class OCRScanResult(BaseModel):
    status: str
    engine: str = "deepseek"
    confidence: float
    raw_text: str
    width: int
    height: int
    records: list[OCRRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)

    @field_validator("message")
    @classmethod
    def message_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Escribí una pregunta sobre el inventario.")
        return value


class ChatReply(BaseModel):
    reply: str
    model: str


class ChatbotStatus(BaseModel):
    available: bool
    model: str
    context_files: int = 0
    detail: str | None = None
