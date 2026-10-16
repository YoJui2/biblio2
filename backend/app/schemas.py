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

    model_config = ConfigDict(from_attributes=True)


class ImportResult(BaseModel):
    records: int
    copies: int
    sections: int
