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
        <div className="panel-header">
          <h3>Find People</h3>
          <span className="muted-copy">Search by username</span>
        </div>

        <form className="people-search-form" onSubmit={handleSearch}>
          <input
            className="input people-search-input"
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
          <div className="people-empty-state">
            <p className="muted-copy">
              No users found matching "{query}".
            </p>
          </div>
        )}

        <div className="people-results">
          {results.map((user) => (
            <button
              key={user.id}
              className="people-card"
              type="button"
              onClick={() => setSelectedUser(user)}
            >
              <div className="people-card-avatar">
                {user.avatar_url ? (
                  <img
                    src={
                      user.avatar_url.startsWith('http')
                        ? user.avatar_url
                        : assetUrl(user.avatar_url)
                    }
                    alt={user.username}
                    className="people-card-img"
                  />
                ) : (
                  <span className="people-card-initial">
                    {user.username.charAt(0).toUpperCase()}
                  </span>
                )}
              </div>
              <div className="people-card-info">
                <span className="people-card-name">{user.username}</span>
                <span className="people-card-role">{user.role}</span>
                {user.bio && (
                  <span className="people-card-bio">{user.bio}</span>
                )}
                <span className={`people-card-status-text ${isUserOnline(user.last_seen) ? 'people-card-online' : ''}`}>
                  {formatLastSeen(user.last_seen)}
                </span>
              </div>
              <div className="people-card-status">
                {user.friendship_status === 'accepted' && (
                  <span className="people-badge people-badge-friend">Friend</span>
                )}
                {user.friendship_status === 'pending' && (
                  <span className="people-badge people-badge-pending">Pending</span>
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
