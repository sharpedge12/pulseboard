/**
 * @fileoverview PeoplePage — User search and discovery page.
 *
 * This page allows authenticated users to search for other platform members
 * by username and interact with them through a modal (send friend request,
 * message, report, view profile).
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Debounce-free search**: Unlike the HomePage search, this uses a form
 *     submission (button click or Enter key) rather than debounced keystrokes.
 *     This is appropriate because user search is a deliberate action, not a
 *     type-ahead experience.
 *   - **useCallback for stable handlers**: `handleSearch` is wrapped in
 *     `useCallback` with dependencies on `query` and `session`. This ensures
 *     referential stability for the `onRefresh` prop passed to `UserActionModal`.
 *   - **Modal delegation pattern**: The `UserActionModal` component handles all
 *     user interaction logic (friend request, message, report). The page just
 *     manages which user is selected and passes the `onRefresh` callback to
 *     re-fetch results after an action (e.g., after sending a friend request,
 *     the friendship status badge should update).
 *   - **Avatar URL handling**: Same pattern as ProfilePage — external OAuth URLs
 *     (starting with 'http') are used directly; relative paths are prepended
 *     with the API base URL via `assetUrl()`.
 *   - **Online status display**: Uses `isUserOnline()` (5-minute threshold) and
 *     `formatLastSeen()` from the shared `timeUtils.js` module.
 *
 * @module pages/PeoplePage
 */

import { useState, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';
import { formatLastSeen, isUserOnline } from '../lib/timeUtils';
import UserActionModal from '../components/UserActionModal';

/**
 * PeoplePage component — renders a user search form and results grid.
 *
 * Requires authentication. Unauthenticated users see a sign-in prompt.
 *
 * @returns {JSX.Element}
 */
function PeoplePage() {
  const { session, profile } = useAuth();

  // ── Search state ──
  const [query, setQuery] = useState('');            // Search input value
  const [results, setResults] = useState([]);        // Array of user objects from the API
  const [searched, setSearched] = useState(false);   // Whether a search has been performed
  const [loading, setLoading] = useState(false);     // Loading indicator
  const [error, setError] = useState('');            // Error message
  const [selectedUser, setSelectedUser] = useState(null); // User selected for modal actions

  /**
   * Performs a user search via the API.
   * Wrapped in useCallback so it can be passed as `onRefresh` to the modal
   * without causing unnecessary re-renders.
   *
   * @param {Event} [e] - Optional form submit event.
   */
  const handleSearch = useCallback(
    async (e) => {
      e?.preventDefault();
      if (!query.trim() || !session?.access_token) return;
      setLoading(true);
      setError('');
      try {
        const data = await apiRequest(
          `/users/search?q=${encodeURIComponent(query.trim())}`,
          { headers: getHeaders(session.access_token) }
        );
        setResults(data);
        setSearched(true);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    },
    [query, session?.access_token]
  );

  // Gate: unauthenticated users see a sign-in prompt
  if (!profile) {
    return (
      <section className="page-grid feed-layout">
        <div className="panel stack-gap">
          <p className="muted-copy">Sign in to search for people.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="page-grid feed-layout">
      <div className="panel stack-gap">
        <h3>Find People</h3>
        <span className="muted-copy">Search by username</span>

        {/* Search form — submit via Enter key or button click */}
        <form className="people-search-form" onSubmit={handleSearch}>
          <input
            className="input"
            type="text"
            placeholder="Search usernames..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          <button
            className="action-button"
            type="submit"
            disabled={loading || !query.trim()}
          >
            {loading ? 'Searching...' : 'Search'}
          </button>
        </form>

        {error && <p className="error-copy">{error}</p>}

        {/* Empty state — only shown after a search has been performed */}
        {searched && results.length === 0 && !loading && (
          <p className="muted-copy">
            No users found matching "{query}".
          </p>
        )}

        {/* User cards grid */}
        <div className="people-grid">
          {results.map((user) => (
            <button
              key={user.id}
              className="people-card"
              type="button"
              onClick={() => setSelectedUser(user)}
            >
              <div className="people-card-header">
                {/* Avatar with fallback to first letter of username */}
                <div className="people-card-avatar">
                  {user.avatar_url ? (
                    <img
                      src={
                        user.avatar_url.startsWith('http')
                          ? user.avatar_url
                          : assetUrl(user.avatar_url)
                      }
                      alt={user.username}
                    />
                  ) : (
                    user.username.charAt(0).toUpperCase()
                  )}
                </div>
                <div className="people-card-info">
                  <span className="people-card-name">{user.username}</span>
                  <span className="people-card-role">{user.role}</span>
                </div>
              </div>
              {user.bio && (
                <span className="people-card-bio">{user.bio}</span>
              )}
              <div className="people-card-footer">
                {/* Online status indicator and last-seen text */}
                <span className={`people-status ${isUserOnline(user.last_seen) ? 'people-online' : ''}`}>
                  {formatLastSeen(user.last_seen)}
                </span>
                {/* Friendship badge — shows current relationship status */}
                {user.friendship_status === 'accepted' && (
                  <span className="friendship-badge">Friend</span>
                )}
                {user.friendship_status === 'pending' && (
                  <span className="friendship-badge" style={{ background: 'var(--color-warning-muted)', color: 'var(--color-warning)' }}>Pending</span>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* User action modal — shows friend/message/report options for selected user */}
      <UserActionModal
        user={selectedUser}
        isOpen={!!selectedUser}
        onClose={() => setSelectedUser(null)}
        onRefresh={handleSearch}
      />
    </section>
  );
}

export default PeoplePage;
