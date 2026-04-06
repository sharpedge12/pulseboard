import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import ThreadCard from '../components/ThreadCard';
import AttachmentList from '../components/AttachmentList';
import MentionTextarea from '../components/MentionTextarea';
import Pagination from '../components/Pagination';
import { useAuth } from '../context/AuthContext';
import { apiRequest, API_BASE_URL, getHeaders } from '../lib/api';
import { useGlobalUpdates } from '../hooks/useGlobalUpdates';

function HomePage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { session, profile } = useAuth();
  const [categories, setCategories] = useState([]);
  const [threads, setThreads] = useState([]);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('loading');
  const [pagination, setPagination] = useState({ page: 1, total_pages: 1, total: 0 });
  const categoriesLoadedRef = useRef(false);

  /* Derive selected category and page from URL query params */
  const selectedCategory = searchParams.get('community') || '';
  const currentPage = Math.max(1, parseInt(searchParams.get('page') || '1', 10) || 1);

  function setSelectedCategory(slug) {
    const next = new URLSearchParams(searchParams);
    next.delete('page');
    if (slug) {
      next.set('community', slug);
    } else {
      next.delete('community');
    }
    setSearchParams(next);
  }

  function setCurrentPage(page) {
    const next = new URLSearchParams(searchParams);
    if (page > 1) {
      next.set('page', String(page));
    } else {
      next.delete('page');
    }
    setSearchParams(next);
  }

  const [createForm, setCreateForm] = useState({ category_id: '', title: '', body: '', tagInput: '' });
  const [draftAttachments, setDraftAttachments] = useState([]);
  const [draftTags, setDraftTags] = useState([]);
  const [createMessage, setCreateMessage] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [composerOpen, setComposerOpen] = useState(false);

  /* Sort & time range filters */
  const [sortBy, setSortBy] = useState('new');
  const [timeRange, setTimeRange] = useState('all');

  /* Load categories once on mount (they rarely change) */
  useEffect(() => {
    let cancelled = false;
    async function loadCategories() {
      try {
        const data = await apiRequest('/categories');
        if (!cancelled) {
          setCategories(data);
          categoriesLoadedRef.current = true;
          if (data[0]) {
            setCreateForm((c) => c.category_id ? c : { ...c, category_id: String(data[0].id) });
          }
        }
      } catch {
        /* categories fetch failed — threads effect will still show content */
      }
    }
    loadCategories();
    return () => { cancelled = true; };
  }, []);

  /* Load threads whenever filters / page change */
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function loadThreads() {
      try {
        setStatus('loading');
        const params = new URLSearchParams();
        if (selectedCategory) params.set('category', selectedCategory);
        if (sortBy && sortBy !== 'new') params.set('sort', sortBy);
        if (timeRange && timeRange !== 'all') params.set('time_range', timeRange);
        params.set('page', String(currentPage));
        params.set('page_size', '10');
        const qs = params.toString();

        const threadsData = await apiRequest(`/threads${qs ? '?' + qs : ''}`);
        if (!cancelled) {
          setThreads(threadsData.items || []);
          setPagination({
            page: threadsData.page || 1,
            total_pages: threadsData.total_pages || 1,
            total: threadsData.total || 0,
          });
          setStatus('ready');
        }
      } catch {
        if (!cancelled) setStatus('error');
      }
    }

    loadThreads();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selectedCategory, sortBy, timeRange, currentPage]);

  // Real-time: add new communities as they are created
  const handleCategoryCreated = useCallback((category) => {
    setCategories((prev) => {
      if (prev.some((c) => c.id === category.id)) return prev;
      return [...prev, category];
    });
  }, []);

  useGlobalUpdates({ onCategoryCreated: handleCategoryCreated });

  useEffect(() => {
    let ignore = false;

    async function runSearch() {
      if (!search.trim()) {
        setSearchResults([]);
        return;
      }

      try {
        const data = await apiRequest(`/search?q=${encodeURIComponent(search.trim())}`);
        if (!ignore) {
          setSearchResults(data.results);
        }
      } catch {
        if (!ignore) {
          setSearchResults([]);
        }
      }
    }

    const timeoutId = window.setTimeout(runSearch, 250);
    return () => {
      ignore = true;
      window.clearTimeout(timeoutId);
    };
  }, [search]);

  const filteredThreads = useMemo(() => {
    return threads.filter((thread) => {
      const q = search.toLowerCase();
      return (
        thread.title.toLowerCase().includes(q) ||
        thread.body.toLowerCase().includes(q) ||
        thread.author.username.toLowerCase().includes(q) ||
        thread.category.title.toLowerCase().includes(q)
      );
    });
  }, [search, threads]);

  async function handleDraftAttachmentUpload(event) {
    if (!session?.access_token || !event.target.files?.[0]) {
      return;
    }

    const formData = new FormData();
    formData.append('linked_entity_type', 'draft');
    formData.append('linked_entity_id', '0');
    formData.append('file', event.target.files[0]);

    const response = await fetch(`${API_BASE_URL}/uploads`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${session.access_token}` },
      body: formData,
    });

    if (response.ok) {
      const data = await response.json();
      setDraftAttachments((c) => [...c, data]);
    }
  }

  async function handleThreadCreate(event) {
    event.preventDefault();
    if (!session?.access_token) {
      setCreateMessage('Sign in first to create a thread.');
      return;
    }

    try {
      const createdThread = await apiRequest('/threads', {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({
          category_id: Number(createForm.category_id),
          title: createForm.title,
          body: createForm.body,
          attachment_ids: draftAttachments.map((item) => item.id),
          tag_names: draftTags,
        }),
      });
      setCreateMessage('Thread created successfully.');
      setCreateForm((c) => ({ ...c, title: '', body: '', tagInput: '' }));
      setDraftAttachments([]);
      setDraftTags([]);
      setComposerOpen(false);
      navigate(`/threads/${createdThread.id}`);
    } catch (error) {
      setCreateMessage(error.message);
    }
  }

  return (
    <section className="page-grid feed-layout">
      {/* Compact hero */}
      <div className="hero-card hero-card-rich">
        <h3>Community Feed</h3>
        <p>
          Search, create, mention, message, and moderate — all in one place.
        </p>
      </div>

      {/* Collapsible Composer */}
      {session?.access_token && (
        <>
          <button
            className="composer-toggle"
            type="button"
            onClick={() => setComposerOpen((c) => !c)}
          >
            <span>
              {composerOpen ? 'Hide composer' : 'Create a new thread'}
            </span>
            <span>{composerOpen ? '\u2212' : '+'}</span>
          </button>

          {composerOpen && (
            <div className="panel stack-gap composer-collapse">
              <div className="panel-header">
                <h3>New Thread</h3>
                <span className="muted-copy">
                  {profile ? profile.username : 'Sign in required'}
                </span>
              </div>

              <form className="stack-gap" onSubmit={handleThreadCreate}>
                <select
                  className="input"
                  value={createForm.category_id}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, category_id: e.target.value })
                  }
                  disabled={categories.length === 0}
                >
                  <option value="">Select community</option>
                  {categories.map((cat) => (
                    <option key={cat.id} value={cat.id}>
                      {cat.title} ({cat.slug})
                    </option>
                  ))}
                </select>

                <input
                  className="input"
                  placeholder="Thread title"
                  value={createForm.title}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, title: e.target.value })
                  }
                  disabled={categories.length === 0}
                />

                <MentionTextarea
                  className="input textarea"
                  placeholder="Start the discussion. Type @ to mention users, @pulse for AI help. Ctrl+Enter to publish."
                  value={createForm.body}
                  onChange={(newBody) =>
                    setCreateForm((c) => ({ ...c, body: newBody }))
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                      e.preventDefault();
                      handleThreadCreate(e);
                    }
                  }}
                  disabled={categories.length === 0}
                  token={session?.access_token}
                />

                <div className="tag-input-row">
                  <input
                    className="input"
                    placeholder="Add tags (press Enter)"
                    value={createForm.tagInput}
                    onChange={(e) =>
                      setCreateForm({ ...createForm, tagInput: e.target.value })
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        const tag = createForm.tagInput.trim().toLowerCase();
                        if (tag && !draftTags.includes(tag)) {
                          setDraftTags((t) => [...t, tag]);
                        }
                        setCreateForm((c) => ({ ...c, tagInput: '' }));
                      }
                    }}
                  />
                  {draftTags.length > 0 && (
                    <div className="pill-row">
                      {draftTags.map((tag) => (
                        <button
                          key={tag}
                          className="pill"
                          type="button"
                          onClick={() => setDraftTags((t) => t.filter((x) => x !== tag))}
                          title="Click to remove"
                        >
                          {tag} &times;
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="inline-actions">
                  <label className="secondary-button upload-label">
                    Attach file
                    <input
                      type="file"
                      hidden
                      onChange={handleDraftAttachmentUpload}
                    />
                  </label>
                  <button
                    className="action-button"
                    type="submit"
                    disabled={categories.length === 0}
                  >
                    Publish thread <span className="kbd-hint">Ctrl+Enter</span>
                  </button>
                </div>

                <AttachmentList attachments={draftAttachments} />
              </form>

              {createMessage && <p className="success-copy">{createMessage}</p>}
            </div>
          )}
        </>
      )}

      {/* Search + filters */}
      <input
        className="input"
        placeholder="Search threads, authors, communities..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      <div className="pill-row">
        <button
          className={selectedCategory === '' ? 'pill pill-active' : 'pill'}
          type="button"
          onClick={() => setSelectedCategory('')}
        >
          All
        </button>
        {categories.map((cat) => (
          <button
            key={cat.id}
            className={selectedCategory === cat.slug ? 'pill pill-active' : 'pill'}
            type="button"
            onClick={() => setSelectedCategory(cat.slug)}
          >
            r/{cat.slug}
          </button>
        ))}
      </div>

      {/* Sort + Time Range filters */}
      <div className="filter-bar">
        <div className="pill-row">
          {[
            { value: 'new', label: 'New' },
            { value: 'top', label: 'Top' },
            { value: 'trending', label: 'Trending' },
          ].map((option) => (
            <button
              key={option.value}
              className={sortBy === option.value ? 'pill pill-active' : 'pill'}
              type="button"
              onClick={() => {
                setSortBy(option.value);
                /* Reset page to 1 in URL when changing sort */
                const next = new URLSearchParams(searchParams);
                next.delete('page');
                setSearchParams(next);
              }}
            >
              {option.label}
            </button>
          ))}
        </div>

        {(sortBy === 'top' || sortBy === 'trending') && (
          <div className="pill-row">
            {[
              { value: 'all', label: 'All time' },
              { value: 'year', label: 'Past year' },
              { value: 'month', label: 'Past month' },
              { value: 'week', label: 'Past week' },
              { value: 'day', label: 'Today' },
              { value: 'hour', label: 'Past hour' },
            ].map((option) => (
              <button
                key={option.value}
                className={timeRange === option.value ? 'pill pill-accent' : 'pill'}
                type="button"
                onClick={() => {
                setTimeRange(option.value);
                /* Reset page to 1 in URL when changing time range */
                const next = new URLSearchParams(searchParams);
                next.delete('page');
                setSearchParams(next);
              }}
              >
                {option.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Search results */}
      {searchResults.length > 0 && (
        <div className="search-results">
          {searchResults.slice(0, 5).map((result) => (
            <button
              key={`${result.result_type}-${result.id}`}
              className="notification-item result-button"
              type="button"
              onClick={() =>
                navigate(
                  result.result_type === 'thread'
                    ? `/threads/${result.id}`
                    : `/threads/${result.thread_id || result.id}`
                )
              }
            >
              <strong>{result.title}</strong>
              <p className="muted-copy">{result.snippet}</p>
            </button>
          ))}
        </div>
      )}

      {/* Thread feed */}
      {status === 'loading' && <p className="muted-copy">Loading threads...</p>}
      {status === 'error' && <p className="error-copy">Could not load forum data.</p>}
      {status === 'ready' && filteredThreads.length === 0 && (
        <p className="muted-copy">No threads found. Start one!</p>
      )}
      {filteredThreads.map((thread) => (
        <ThreadCard key={thread.id} thread={thread} />
      ))}

      {/* Pagination controls */}
      <Pagination
        currentPage={currentPage}
        totalPages={pagination.total_pages}
        totalItems={pagination.total}
        onPageChange={setCurrentPage}
        itemLabel="threads"
      />
    </section>
  );
}

export default HomePage;
