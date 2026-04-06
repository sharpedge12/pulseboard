from pydantic import BaseModel


class SearchResultItem(BaseModel):
    result_type: str
    id: int
    title: str
    snippet: str
    category: str | None = None
    author: str
    thread_id: int | None = None


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResultItem]
