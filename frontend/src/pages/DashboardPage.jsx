import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';
import { formatTimeAgo } from '../lib/timeUtils';

/**
 * Member Dashboard — a personalized hub for regular members.
 * Shows quick stats, recent threads, friend count, and shortcuts.
 */
function DashboardPage() {
  const navigate = useNavigate();
  const { session, profile } = useAuth();
  const [stats, setStats] = useState(null);
  const [recentThreads, setRecentThreads] = useState([]);
  const [friendData, setFriendData] = useState(null);
  const [notifications, setNotifications] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!session?.access_token) {
      setLoading(false);
      return;
    }

    async function loadDashboard() {
      setLoading(true);
      setError(null);

      const headers = getHeaders(session.access_token);

      // Fetch each endpoint independently so one failure doesn't block all
      const [threadsResult, friendsResult, notifResult] = await Promise.allSettled([
        apiRequest('/threads?page=1&page_size=5&sort=new'),
        apiRequest('/users/friends', { headers }),
        apiRequest('/notifications', { headers }),
      ]);

      const threadsData = threadsResult.status === 'fulfilled' ? threadsResult.value : null;
      const friendsData = friendsResult.status === 'fulfilled' ? friendsResult.value : null;
      const notifData = notifResult.status === 'fulfilled' ? notifResult.value : null;

      setRecentThreads(threadsData?.items || []);
      setFriendData(friendsData);
      setNotifications(notifData);
      setStats({
        totalThreads: threadsData?.total || 0,
        friends: friendsData?.friends?.length || 0,
        pendingRequests: friendsData?.incoming?.length || 0,
        unreadNotifications: notifData?.unread_count || 0,
      });

      // Show error only when all three fail
      if (
        threadsResult.status === 'rejected' &&
        friendsResult.status === 'rejected' &&
        notifResult.status === 'rejected'
      ) {
        setError('Failed to load dashboard data. Please try again later.');
      }

      setLoading(false);
    }

    loadDashboard();
  }, [session?.access_token]);

  if (!profile) {
    return (
      <section className="page-grid feed-layout">
        <div className="panel stack-gap">
          <p className="muted-copy">Sign in to view your dashboard.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="page-grid feed-layout">
      <div className="panel stack-gap">
        <h3>Dashboard</h3>
        <span className="muted-copy">Welcome back, {profile.username}</span>

        {loading && <p className="muted-copy">Loading dashboard...</p>}

        {!loading && error && (
          <p className="muted-copy">{error}</p>
        )}

        {!loading && stats && (
          <>
            {/* Quick stats */}
            <div className="stat-grid">
              <div className="stat-card">
                <span className="stat-number">{stats.unreadNotifications}</span>
                <span className="stat-label">Unread Notifications</span>
              </div>
              <div className="stat-card">
                <span className="stat-number">{stats.friends}</span>
                <span className="stat-label">Friends</span>
              </div>
              <div className="stat-card">
                <span className="stat-number">{stats.pendingRequests}</span>
                <span className="stat-label">Pending Requests</span>
              </div>
              <div className="stat-card">
                <span className="stat-number">{stats.totalThreads}</span>
                <span className="stat-label">Forum Threads</span>
              </div>
            </div>

            {/* Quick actions */}
            <div className="quick-actions">
              <button
                className="action-button"
                type="button"
                onClick={() => navigate('/')}
              >
                Create Thread
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => navigate('/chat')}
              >
                Open Chat
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => navigate('/people')}
              >
                Find People
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => navigate('/profile')}
              >
                Edit Profile
              </button>
            </div>

            {/* Recent unread notifications */}
            {notifications && notifications.items && notifications.items.length > 0 && (
              <div className="dash-section">
                <h3>Recent Notifications</h3>
                <div className="stack-gap">
                  {notifications.items.slice(0, 5).map((notif) => (
                    <div
                      key={notif.id}
                      className={`dash-notif-item ${notif.is_read ? '' : 'unread'}`}
                    >
                      <span className="dash-notif-title">{notif.title}</span>
                      <div className="edit-inline-actions">
                        <span className="dash-notif-type">{notif.notification_type.replace(/_/g, ' ')}</span>
                        <span className="dash-notif-time">{formatTimeAgo(notif.created_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent threads */}
            {recentThreads.length > 0 && (
              <div className="dash-section">
                <h3>Latest Threads</h3>
                <div className="stack-gap">
                  {recentThreads.map((thread) => (
                    <button
                      key={thread.id}
                      className="dash-thread-item"
                      type="button"
                      onClick={() => navigate(`/threads/${thread.id}`)}
                    >
                      <div>
                        <span className="dash-thread-title">{thread.title}</span>
                        <span className="dash-thread-meta">
                          r/{thread.category.slug} &middot; {thread.reply_count} replies &middot; {thread.vote_score} votes &middot; {formatTimeAgo(thread.created_at)}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
                <button
                  className="action-link"
                  type="button"
                  onClick={() => navigate('/')}
                >
                  View all threads
                </button>
              </div>
            )}

            {/* Friends list preview */}
            {friendData && friendData.friends && friendData.friends.length > 0 && (
              <div className="dash-section">
                <h3>Friends</h3>
                <div className="friend-chips">
                  {friendData.friends.slice(0, 8).map((friend) => (
                    <button
                      key={friend.id}
                      className="friend-chip"
                      type="button"
                      onClick={() => navigate(`/profile/${friend.id}`)}
                      title={friend.username}
                    >
                      {friend.avatar_url ? (
                        <img
                          src={
                            friend.avatar_url.startsWith('http')
                              ? friend.avatar_url
                              : assetUrl(friend.avatar_url)
                          }
                          alt={friend.username}
                          className="friend-chip-avatar"
                        />
                      ) : (
                        <span className="friend-chip-avatar">
                          {friend.username.charAt(0).toUpperCase()}
                        </span>
                      )}
                      <span className="friend-chip-name">{friend.username}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

export default DashboardPage;
