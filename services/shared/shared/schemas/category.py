from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.services.sanitize import sanitize_text


class CategoryCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        return sanitize_text(v)

    @field_validator("description")
    @classmethod
    def clean_description(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return sanitize_text(v)


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    description: str | None
    thread_count: int = 0


class CommunityCreateRequest(CategoryCreateRequest):
    pass
