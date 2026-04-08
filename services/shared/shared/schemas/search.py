"""
Search Schemas
===============

This module defines Pydantic models for the full-text search API response.

**Interview Concept: Polymorphic search results**

Search results are *polymorphic* — a single search can return a mix of
threads and posts.  Rather than returning separate lists for each type,
we use a single ``SearchResultItem`` model with a ``result_type``
discriminator field ("thread" or "post") that tells the frontend how to
render each result.

This pattern is common in search APIs (think Google returning web pages,
images, videos, and news all in one list).  The alternative — separate
endpoints for each content type — would require the frontend to make
multiple API calls and merge the results client-side.

**Interview Concept: Snippets vs full content**

The ``snippet`` field contains a short excerpt of the matching content
(typically 100-200 characters around the search term), not the full
thread body or post body.  This is for performance and UX:
- Performance: Sending full bodies for 50 search results would be a
  large payload.
- UX: Users scan snippets to find the most relevant result before
  clicking through to the full content.
"""

from pydantic import BaseModel


class SearchResultItem(BaseModel):
    """
    A single search result — could be either a thread or a post.

    Fields:
    - ``result_type``: Discriminator — ``"thread"`` or ``"post"``.  The
      frontend uses this to decide how to render the result (threads
      link to ``/thread/{id}``, posts link to ``/thread/{thread_id}``
      and scroll to the specific post).
    - ``id``: The ID of the matching entity.
    - ``title``: Thread title.  For post results, this is the parent
      thread's title (since posts don't have their own titles).
    - ``snippet``: Short excerpt with the search term highlighted.
    - ``category``: Category name (for context in the result list).
      ``None`` if the result type doesn't have a category.
    - ``author``: Username of the content author.
    - ``thread_id``: For post results, this links back to the parent
      thread.  ``None`` for thread results (the ``id`` is the thread ID).
    """

    result_type: str  # "thread" or "post"
    id: int
    title: str
    snippet: str  # Short excerpt of matching content
    category: str | None = None
    author: str
    thread_id: int | None = None  # For post results: parent thread ID


class SearchResponse(BaseModel):
    """
    Top-level search response wrapping the result list.

    Fields:
    - ``query``: The original search query string, echoed back so the
      frontend can display "Results for: {query}".
    - ``total``: Total number of matching results (may be more than
      the returned list if server-side pagination/limits are applied).
    - ``results``: The list of matching items.
    """

    query: str
    total: int
    results: list[SearchResultItem]
