import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import ThreadCard from '../components/ThreadCard';
import AttachmentList from '../components/AttachmentList';
import MentionTextarea from '../components/MentionTextarea';
import Pagination from '../components/Pagination';
import { useAuth } from '../context/AuthContext';
import { apiRequest, API_BASE_URL, getHeaders } from '../lib/api';
import { validateFile, ATTACHMENT_ACCEPT } from '../lib/uploadUtils';
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

  /* Load categories once on mount */
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
        /* categories fetch failed */
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

  // Real-time: add new communities
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

    const file = event.target.files[0];
    const { valid, error } = validateFile(file);
    if (!valid) {
      setCreateMessage(error);
      event.target.value = '';
      return;
    }

    const formData = new FormData();
    formData.append('linked_entity_type', 'draft');
    formData.append('linked_entity_id', '0');
    formData.append('file', file);

    const response = await fetch(`${API_BASE_URL}/uploads`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${session.access_token}` },
      body: formData,
    });

    if (response.ok) {
      const data = await response.json();
      setDraftAttachments((c) => [...c, data]);
    } else {
      const err = await response.json().catch(() => ({}));
      setCreateMessage(err.detail || 'Upload failed.');
    }
    event.target.value = '';
  }

  async function handleThreadCreate(event) {
    event.preventDefault();
    if (!session?.access_token) {
      setCreateMessage('Sign in first to create a thread.');
      return;
    }
    const catId = Number(createForm.category_id);
    if (!catId || catId < 1) {
      setCreateMessage('Please select a community first.');
      return;
    }
    if (!createForm.title.trim()) {
      setCreateMessage('Title is required.');
      return;
    }
    if (!createForm.body.trim()) {
      setCreateMessage('Body is required.');
      return;
    }

    try {
      const createdThread = await apiRequest('/threads', {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({
          category_id: catId,
          title: createForm.title.trim(),
          body: createForm.body.trim(),
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

  const activeCat = categories.find((c) => c.slug === selectedCategory);

  return (
    <section className="page-grid feed-layout">
      {/* ── Left: Main Feed ── */}
      <div className="feed-main">
        {/* Create Post Bar */}
        {session?.access_token && (
          <div style={{ marginBottom: 'var(--space-3)' }}>
            <button
              className="composer-toggle"
              type="button"
              onClick={() => setComposerOpen((c) => !c)}
            >
              <span
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  background: 'var(--color-accent)',
                  color: 'white',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '1.2rem',
                  fontWeight: 800,
                  flexShrink: 0,
                }}
              >
                +
              </span>
              <span style={{ flex: 1, color: 'var(--color-text-muted)' }}>
                Create Post
              </span>
            </button>

            {composerOpen && (
              <div className="panel stack-gap" style={{ marginTop: 'var(--space-2)' }}>
                <form className="stack-gap" onSubmit={handleThreadCreate}>
                  <select
                    className="input"
                    value={createForm.category_id}
                    onChange={(e) =>
                      setCreateForm({ ...createForm, category_id: e.target.value })
                    }
                    disabled={categories.length === 0}
                  >
                    <option value="">Choose a community</option>
                    {categories.map((cat) => (
                      <option key={cat.id} value={cat.id}>
                        r/{cat.slug} - {cat.title}
                      </option>
                    ))}
                  </select>

                  <input
                    className="input"
                    placeholder="Title"
                    value={createForm.title}
                    onChange={(e) =>
                      setCreateForm({ ...createForm, title: e.target.value })
                    }
                    disabled={categories.length === 0}
                  />

                  <MentionTextarea
                    className="input textarea"
                    placeholder="Text (optional). Type @ to mention users. Ctrl+Enter to post."
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

                  <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
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
                      style={{ flex: 1 }}
                    />
                  </div>

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

                  <div style={{ display: 'flex', gap: 'var(--space-2)', justifyContent: 'flex-end' }}>
                    <label className="secondary-button" style={{ cursor: 'pointer' }}>
                      Attach
                      <input type="file" hidden accept={ATTACHMENT_ACCEPT} onChange={handleDraftAttachmentUpload} />
                    </label>
                    <button className="action-button" type="submit" disabled={categories.length === 0}>
                      Post <span className="kbd-hint">Ctrl+Enter</span>
                    </button>
                  </div>

                  <AttachmentList attachments={draftAttachments} />
                </form>

                {createMessage && <p className="success-copy">{createMessage}</p>}
              </div>
            )}
          </div>
        )}

        {/* Sort Tabs */}
        <div className="panel" style={{ padding: 'var(--space-2) var(--space-3)', marginBottom: 'var(--space-3)', display: 'flex', gap: 'var(--space-1)', alignItems: 'center', flexWrap: 'wrap' }}>
          {[
            { value: 'new', label: 'New' },
            { value: 'top', label: 'Top' },
            { value: 'trending', label: 'Hot' },
          ].map((option) => (
            <button
              key={option.value}
              className={sortBy === option.value ? 'pill pill-active' : 'pill'}
              type="button"
              onClick={() => {
                setSortBy(option.value);
                const next = new URLSearchParams(searchParams);
                next.delete('page');
                setSearchParams(next);
              }}
            >
              {option.label}
            </button>
          ))}

          {/* Category pills */}
          <span style={{ width: 1, height: 20, background: 'var(--color-border-default)', margin: '0 4px' }} />
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

          {(sortBy === 'top' || sortBy === 'trending') && (
            <>
              <span style={{ width: 1, height: 20, background: 'var(--color-border-default)', margin: '0 4px' }} />
              {[
                { value: 'all', label: 'All time' },
                { value: 'year', label: 'Year' },
                { value: 'month', label: 'Month' },
                { value: 'week', label: 'Week' },
                { value: 'day', label: 'Today' },
              ].map((option) => (
                <button
                  key={option.value}
                  className={timeRange === option.value ? 'pill pill-accent' : 'pill'}
                  type="button"
                  onClick={() => {
                    setTimeRange(option.value);
                    const next = new URLSearchParams(searchParams);
                    next.delete('page');
                    setSearchParams(next);
                  }}
                >
                  {option.label}
                </button>
              ))}
            </>
          )}
        </div>

        {/* Search bar */}
        <div style={{ position: 'relative', marginBottom: 'var(--space-3)' }}>
          <input
            className="input"
            placeholder="Search threads, authors, communities..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {searchResults.length > 0 && (
            <div className="search-results-dropdown">
              {searchResults.slice(0, 5).map((result) => (
                <button
                  key={`${result.result_type}-${result.id}`}
                  className="search-result-item"
                  type="button"
                  onClick={() =>
                    navigate(
                      result.result_type === 'thread'
                        ? `/threads/${result.id}`
                        : `/threads/${result.thread_id || result.id}`
                    )
                  }
                >
                  <span className="search-result-type">{result.result_type}</span>
                  <span>{result.title}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Thread feed */}
        <div className="stack-gap">
          {status === 'loading' && <p className="muted-copy">Loading...</p>}
          {status === 'error' && <p className="error-copy">Could not load forum data.</p>}
          {status === 'ready' && filteredThreads.length === 0 && (
            <div className="panel" style={{ textAlign: 'center', padding: 'var(--space-8)' }}>
              <p className="muted-copy">No threads yet. Be the first to post!</p>
            </div>
          )}
          {filteredThreads.map((thread) => (
            <ThreadCard key={thread.id} thread={thread} />
          ))}
        </div>

        {/* Pagination */}
        <Pagination
          currentPage={currentPage}
          totalPages={pagination.total_pages}
          totalItems={pagination.total}
          onPageChange={setCurrentPage}
          itemLabel="threads"
        />
      </div>

      {/* ── Right: Sidebar ── */}
      <div className="feed-sidebar">
        {/* About Community */}
        <div className="sidebar-widget">
          <div className="sidebar-widget-header">
            {activeCat ? `About r/${activeCat.slug}` : 'About PulseBoard'}
          </div>
          <div className="sidebar-widget-body">
            <p>{activeCat ? activeCat.description : 'A real-time discussion forum for teams. Create threads, chat, and collaborate.'}</p>
            <div style={{ display: 'flex', gap: 'var(--space-4)', marginTop: 'var(--space-2)' }}>
              <div style={{ textAlign: 'center' }}>
                <strong style={{ display: 'block', color: 'var(--color-text-primary)' }}>{pagination.total}</strong>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>Threads</span>
              </div>
              <div style={{ textAlign: 'center' }}>
                <strong style={{ display: 'block', color: 'var(--color-text-primary)' }}>{categories.length}</strong>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>Communities</span>
              </div>
            </div>
            {session?.access_token && (
              <button
                className="action-button"
                type="button"
                onClick={() => setComposerOpen(true)}
                style={{ width: '100%', marginTop: 'var(--space-2)' }}
              >
                Create Post
              </button>
            )}
          </div>
        </div>

        {/* Communities */}
        <div className="sidebar-widget">
          <div className="sidebar-widget-header">
            Communities
          </div>
          <div className="sidebar-widget-body">
            {categories.map((cat, i) => (
              <button
                key={cat.slug}
                className="sidebar-community-link"
                onClick={() => setSelectedCategory(cat.slug)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', width: '100%' }}
              >
                <span className="sidebar-community-dot" />
                <span style={{ flex: 1, textAlign: 'left' }}>r/{cat.slug}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Rules */}
        <div className="sidebar-widget">
          <div className="sidebar-widget-header">
            Rules
          </div>
          <div className="sidebar-widget-body">
            <div className="sidebar-rule">
              <span className="sidebar-rule-num">1.</span>
              <span>Be respectful and constructive</span>
            </div>
            <div className="sidebar-rule">
              <span className="sidebar-rule-num">2.</span>
              <span>No spam or self-promotion</span>
            </div>
            <div className="sidebar-rule">
              <span className="sidebar-rule-num">3.</span>
              <span>Post in the right community</span>
            </div>
            <div className="sidebar-rule">
              <span className="sidebar-rule-num">4.</span>
              <span>Use @pulse for AI assistance</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default HomePage;
