from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class TagResponse(BaseModel):
    id: int
    name: str


class TagCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return sanitize_text(v.strip().lower())
