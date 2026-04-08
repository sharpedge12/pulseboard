/**
 * @fileoverview HomePage — Main feed page for PulseBoard (Reddit-inspired forum).
 *
 * This is the primary landing page of the application. It renders:
 *   1. A thread feed with server-side filtering, sorting, and pagination.
 *   2. A "Create Post" composer (togglable) with category selection, @mention support,
 *      tag management, and file attachments.
 *   3. A right sidebar with community info, community links, and platform rules.
 *   4. A debounced search bar with a dropdown of backend search results.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **URL-as-state**: The selected community (`?community=`) and page number (`?page=`)
 *     are read from and written to URL query params via `useSearchParams`. This makes the
 *     page bookmarkable, shareable, and back-button friendly. Changing filters resets page
 *     to 1 to avoid showing an out-of-range page.
 *   - **Derived state vs. stored state**: `selectedCategory` and `currentPage` are derived
 *     from the URL on every render instead of being stored in `useState`. This is a
 *     single-source-of-truth pattern that avoids state synchronization bugs.
 *   - **Debounced search**: The search `useEffect` uses `setTimeout` (250ms) to avoid
 *     firing an API call on every keystroke. The cleanup function clears the timeout if
 *     the user types again before the delay expires — classic debounce pattern.
 *   - **Race condition guards**: Every async `useEffect` uses a `cancelled` flag checked
 *     after the `await` to prevent state updates on an unmounted or re-rendered component.
 *   - **Real-time updates**: Uses `useGlobalUpdates` WebSocket hook to live-add new
 *     communities to the sidebar without requiring a page refresh.
 *   - **Optimistic category default**: The composer pre-selects the first category when
 *     categories load, but only if the user hasn't already picked one (preserves intent).
 *
 * @module pages/HomePage
 */

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

/**
 * HomePage component — renders the main thread feed, composer, and sidebar.
 *
 * @returns {JSX.Element} The full-width feed layout with sidebar.
 */
function HomePage() {
  const navigate = useNavigate();

  /**
   * useSearchParams gives us a URLSearchParams-like object and a setter.
   * We use it as the single source of truth for filter/page state so the
   * URL always reflects the current view (deep-linkable).
   */
  const [searchParams, setSearchParams] = useSearchParams();
  const { session, profile } = useAuth();

  // ── Server-driven state ──
  const [categories, setCategories] = useState([]);     // All available communities
  const [threads, setThreads] = useState([]);            // Current page of threads from the API
  const [search, setSearch] = useState('');               // Local search input value
  const [status, setStatus] = useState('loading');        // 'loading' | 'ready' | 'error'
  const [pagination, setPagination] = useState({ page: 1, total_pages: 1, total: 0 });

  /**
   * Ref to track whether categories have been loaded at least once.
   * This prevents the composer from overriding a user's category selection
   * if the categories re-fetch for some reason.
   */
  const categoriesLoadedRef = useRef(false);

  // ── Derived state from URL query params ──
  // These are computed on every render — NOT stored in useState.
  // Interview tip: "derived state" avoids synchronization bugs between URL and component state.
  const selectedCategory = searchParams.get('community') || '';
  const currentPage = Math.max(1, parseInt(searchParams.get('page') || '1', 10) || 1);

  /**
   * Updates the `?community=` query param and resets page to 1.
   * Deleting the page param (instead of setting it to '1') keeps the URL clean.
   *
   * @param {string} slug - The category slug to filter by, or '' for all.
   */
  function setSelectedCategory(slug) {
    const next = new URLSearchParams(searchParams);
    next.delete('page'); // Reset to page 1 on category change
    if (slug) {
      next.set('community', slug);
    } else {
      next.delete('community');
    }
    setSearchParams(next);
  }

  /**
   * Updates the `?page=` query param. Page 1 is omitted for clean URLs.
   *
   * @param {number} page - The page number to navigate to.
   */
  function setCurrentPage(page) {
    const next = new URLSearchParams(searchParams);
    if (page > 1) {
      next.set('page', String(page));
    } else {
      next.delete('page'); // Page 1 is the default — no need to clutter the URL
    }
    setSearchParams(next);
  }

  // ── Composer (new thread form) state ──
  const [createForm, setCreateForm] = useState({ category_id: '', title: '', body: '', tagInput: '' });
  const [draftAttachments, setDraftAttachments] = useState([]);  // Files uploaded before thread creation
  const [draftTags, setDraftTags] = useState([]);                // Tag chips accumulated by the user
  const [createMessage, setCreateMessage] = useState('');        // Success or error message
  const [searchResults, setSearchResults] = useState([]);        // Backend search results dropdown
  const [composerOpen, setComposerOpen] = useState(false);       // Toggle for the post composer panel

  // ── Sort & time range filters (local state — not in URL) ──
  const [sortBy, setSortBy] = useState('new');
  const [timeRange, setTimeRange] = useState('all');

  /**
   * Load categories once on mount.
   *
   * Interview note: The empty dependency array `[]` means this runs once after
   * the first render. The `cancelled` flag prevents setting state if the
   * component unmounts before the fetch completes (avoids React warning).
   *
   * The first category is pre-selected in the composer form as a default,
   * but only if the user hasn't already selected one (the ternary in setCreateForm).
   */
  useEffect(() => {
    let cancelled = false;
    async function loadCategories() {
      try {
        const data = await apiRequest('/categories');
        if (!cancelled) {
          setCategories(data);
          categoriesLoadedRef.current = true;
          // Pre-select first category in the composer if none is chosen yet
          if (data[0]) {
            setCreateForm((c) => c.category_id ? c : { ...c, category_id: String(data[0].id) });
          }
        }
      } catch {
        /* categories fetch failed — sidebar will be empty but page still works */
      }
    }
    loadCategories();
    return () => { cancelled = true; };
  }, []);

  /**
   * Load threads whenever filters or page change.
   *
   * Dependencies: selectedCategory, sortBy, timeRange, currentPage.
   * Any change re-fetches the thread list from the backend.
   *
   * Uses AbortController so an in-flight request can be cancelled if
   * the user changes filters before it completes (prevents stale data).
   */
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function loadThreads() {
      try {
        setStatus('loading');
        // Build query string from current filter state
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

  /**
   * Real-time handler: when a new community is created (via WebSocket),
   * add it to the local categories list if it isn't already present.
   * useCallback ensures a stable reference so the hook doesn't reconnect.
   */
  const handleCategoryCreated = useCallback((category) => {
    setCategories((prev) => {
      if (prev.some((c) => c.id === category.id)) return prev; // Deduplicate
      return [...prev, category];
    });
  }, []);

  // Subscribe to global WebSocket events for live community creation
  useGlobalUpdates({ onCategoryCreated: handleCategoryCreated });

  /**
   * Debounced backend search.
   *
   * Interview note: This is a manual debounce implementation using setTimeout.
   * Every time `search` changes, we clear the previous timeout (via the cleanup
   * function) and schedule a new one in 250ms. The `ignore` flag prevents stale
   * responses from overwriting newer results.
   *
   * Alternative approaches: lodash.debounce, useDeferredValue (React 18), or
   * a custom useDebounce hook.
   */
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

    // Schedule the search after a 250ms debounce delay
    const timeoutId = window.setTimeout(runSearch, 250);
    return () => {
      ignore = true;
      window.clearTimeout(timeoutId); // Cancel pending search on re-render
    };
  }, [search]);

  /**
   * Client-side filter: further narrows the server-returned threads
   * by the local search query. This gives instant feedback while the
   * debounced backend search is in flight.
   *
   * useMemo prevents re-filtering on every render unless `search` or `threads` change.
   */
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

  /**
   * Uploads a file attachment for the "draft" thread (before the thread is created).
   * The backend stores the file and returns metadata; we accumulate these in
   * `draftAttachments` and send their IDs when the thread is finally created.
   *
   * @param {Event} event - The file input change event.
   */
  async function handleDraftAttachmentUpload(event) {
    if (!session?.access_token || !event.target.files?.[0]) {
      return;
    }

    const file = event.target.files[0];
    // Client-side validation: checks file size, MIME type, and extension
    const { valid, error } = validateFile(file);
    if (!valid) {
      setCreateMessage(error);
      event.target.value = ''; // Reset file input
      return;
    }

    // Upload as a "draft" attachment — linked_entity_id=0 means no thread yet
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
      setDraftAttachments((c) => [...c, data]); // Append to the attachment list
    } else {
      const err = await response.json().catch(() => ({}));
      setCreateMessage(err.detail || 'Upload failed.');
    }
    event.target.value = ''; // Reset file input so same file can be re-selected
  }

  /**
   * Handles the "Create Thread" form submission.
   * Validates inputs, sends a POST to `/threads`, and navigates to the new thread.
   * Attachment IDs and tag names accumulated during composition are sent along.
   *
   * @param {Event} event - The form submit event.
   */
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
      // Reset the composer form
      setCreateForm((c) => ({ ...c, title: '', body: '', tagInput: '' }));
      setDraftAttachments([]);
      setDraftTags([]);
      setComposerOpen(false);
      // Navigate to the newly created thread
      navigate(`/threads/${createdThread.id}`);
    } catch (error) {
      setCreateMessage(error.message);
    }
  }

  /** The currently active category object (for the sidebar "About" widget). */
  const activeCat = categories.find((c) => c.slug === selectedCategory);

  return (
    <section className="page-grid feed-layout">
      {/* ── Left: Main Feed ── */}
      <div className="feed-main">
        {/* Create Post Bar — only shown to authenticated users */}
        {session?.access_token && (
          <div style={{ marginBottom: 'var(--space-3)' }}>
            {/* Toggle button to expand/collapse the thread composer */}
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

            {/* ── Thread Composer Panel ── */}
            {composerOpen && (
              <div className="panel stack-gap" style={{ marginTop: 'var(--space-2)' }}>
                <form className="stack-gap" onSubmit={handleThreadCreate}>
                  {/* Category selector dropdown */}
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

                  {/* Thread title input */}
                  <input
                    className="input"
                    placeholder="Title"
                    value={createForm.title}
                    onChange={(e) =>
                      setCreateForm({ ...createForm, title: e.target.value })
                    }
                    disabled={categories.length === 0}
                  />

                  {/*
                    MentionTextarea — custom component that provides @mention autocomplete.
                    onKeyDown: Ctrl/Cmd+Enter submits the form (long-form shortcut pattern).
                  */}
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

                  {/*
                    Tag input — pressing Enter adds the current value as a tag chip.
                    preventDefault() stops the form from submitting on Enter.
                    Duplicate tags are silently ignored.
                  */}
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

                  {/* Render tag chips with click-to-remove */}
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

                  {/* Attachment upload button and submit button */}
                  <div style={{ display: 'flex', gap: 'var(--space-2)', justifyContent: 'flex-end' }}>
                    <label className="secondary-button" style={{ cursor: 'pointer' }}>
                      Attach
                      <input type="file" hidden accept={ATTACHMENT_ACCEPT} onChange={handleDraftAttachmentUpload} />
                    </label>
                    <button className="action-button" type="submit" disabled={categories.length === 0}>
                      Post <span className="kbd-hint">Ctrl+Enter</span>
                    </button>
                  </div>

                  {/* Preview of attached files */}
                  <AttachmentList attachments={draftAttachments} />
                </form>

                {createMessage && <p className="success-copy">{createMessage}</p>}
              </div>
            )}
          </div>
        )}

        {/* ── Sort Tabs & Category Filter Pills ── */}
        <div className="panel" style={{ padding: 'var(--space-2) var(--space-3)', marginBottom: 'var(--space-3)', display: 'flex', gap: 'var(--space-1)', alignItems: 'center', flexWrap: 'wrap' }}>
          {/* Sort options: New, Top, Hot */}
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
                // Reset page to 1 when changing sort order
                const next = new URLSearchParams(searchParams);
                next.delete('page');
                setSearchParams(next);
              }}
            >
              {option.label}
            </button>
          ))}

          {/* Visual separator between sort and category pills */}
          <span style={{ width: 1, height: 20, background: 'var(--color-border-default)', margin: '0 4px' }} />

          {/* "All" category pill */}
          <button
            className={selectedCategory === '' ? 'pill pill-active' : 'pill'}
            type="button"
            onClick={() => setSelectedCategory('')}
          >
            All
          </button>

          {/* One pill per community category */}
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

          {/* Time range filters — only shown for "Top" and "Hot" sort modes */}
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

        {/* ── Search Bar with Dropdown ── */}
        <div style={{ position: 'relative', marginBottom: 'var(--space-3)' }}>
          <input
            className="input"
            placeholder="Search threads, authors, communities..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {/* Dropdown appears when backend search returns results */}
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

        {/* ── Thread Feed ── */}
        <div className="stack-gap">
          {status === 'loading' && <p className="muted-copy">Loading...</p>}
          {status === 'error' && <p className="error-copy">Could not load forum data.</p>}
          {status === 'ready' && filteredThreads.length === 0 && (
            <div className="panel" style={{ textAlign: 'center', padding: 'var(--space-8)' }}>
              <p className="muted-copy">No threads yet. Be the first to post!</p>
            </div>
          )}
          {/* Each thread is rendered by the reusable ThreadCard component */}
          {filteredThreads.map((thread) => (
            <ThreadCard key={thread.id} thread={thread} />
          ))}
        </div>

        {/* ── Pagination ── */}
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
        {/* About Community / About PulseBoard widget */}
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

        {/* Communities list widget */}
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

        {/* Platform rules widget */}
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
