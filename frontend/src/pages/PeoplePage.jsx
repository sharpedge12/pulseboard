import { useState, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';
import { formatLastSeen, isUserOnline } from '../lib/timeUtils';
import UserActionModal from '../components/UserActionModal';

function PeoplePage() {
  const { session, profile } = useAuth();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedUser, setSelectedUser] = useState(null);

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

        {searched && results.length === 0 && !loading && (
          <p className="muted-copy">
            No users found matching "{query}".
          </p>
        )}

        <div className="people-grid">
          {results.map((user) => (
            <button
              key={user.id}
              className="people-card"
              type="button"
              onClick={() => setSelectedUser(user)}
            >
              <div className="people-card-header">
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
                <span className={`people-status ${isUserOnline(user.last_seen) ? 'people-online' : ''}`}>
                  {formatLastSeen(user.last_seen)}
                </span>
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
