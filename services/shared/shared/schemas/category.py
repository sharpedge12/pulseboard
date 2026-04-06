from pydantic import BaseModel, ConfigDict, Field


class CategoryCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=500)


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    description: str | None
    thread_count: int = 0


class CommunityCreateRequest(CategoryCreateRequest):
    pass
