/**
 * @fileoverview DashboardPage — Personalized member hub with stats and shortcuts.
 *
 * This page provides a quick overview for authenticated users, including:
 *   1. Quick stats: unread notifications, friend count, pending requests, total threads.
 *   2. Quick action buttons: create thread, open chat, find people, edit profile.
 *   3. Recent notifications (up to 5) with type badges and timestamps.
 *   4. Latest forum threads (up to 5) with metadata and a "View all" link.
 *   5. Friends preview (up to 8) with avatars and profile links.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Promise.allSettled for resilient data loading**: Unlike Promise.all (which
 *     rejects if ANY promise fails), Promise.allSettled waits for ALL promises to
 *     complete and reports each one's status individually. This means a failure in
 *     the notifications API won't prevent threads and friends from rendering.
 *     The error state is only shown when ALL three requests fail.
 *   - **Defensive data access**: After Promise.allSettled, each result is checked
 *     for `status === 'fulfilled'` before accessing its value. This prevents
 *     "Cannot read property of undefined" errors when an endpoint is down.
 *   - **Navigation shortcuts**: The quick action buttons use `useNavigate()` to
 *     route to other pages. "Create Thread" navigates to the home page where the
 *     composer can be opened — this avoids duplicating the thread creation UI.
 *   - **Avatar URL guard**: Same pattern as other pages — checks `startsWith('http')`
 *     to handle both OAuth external URLs and uploaded relative paths.
 *
 * @module pages/DashboardPage
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';
import { formatTimeAgo } from '../lib/timeUtils';

/**
 * DashboardPage component — a personalized hub for regular members.
 * Shows quick stats, recent threads, friend count, and shortcuts.
 *
 * @returns {JSX.Element}
 */
function DashboardPage() {
  const navigate = useNavigate();
  const { session, profile } = useAuth();

  // ── Dashboard data state ──
  const [stats, setStats] = useState(null);                // Aggregated stats object
  const [recentThreads, setRecentThreads] = useState([]);   // Latest 5 threads
  const [friendData, setFriendData] = useState(null);       // Friends data (friends, incoming, outgoing)
  const [notifications, setNotifications] = useState(null);  // Notifications with items and unread_count
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  /**
   * Loads all dashboard data in parallel using Promise.allSettled.
   *
   * Interview note: Promise.allSettled is the key pattern here. It ensures
   * that a single failing endpoint doesn't break the entire dashboard.
   * Each result has a `status` of either 'fulfilled' (with `.value`) or
   * 'rejected' (with `.reason`).
   */
  useEffect(() => {
    if (!session?.access_token) {
      setLoading(false);
      return;
    }

    async function loadDashboard() {
      setLoading(true);
      setError(null);

      const headers = getHeaders(session.access_token);

      // Fetch all three endpoints independently — one failure won't block others
      const [threadsResult, friendsResult, notifResult] = await Promise.allSettled([
        apiRequest('/threads?page=1&page_size=5&sort=new'),
        apiRequest('/users/friends', { headers }),
        apiRequest('/notifications', { headers }),
      ]);

      // Safely extract values — null if the request failed
      const threadsData = threadsResult.status === 'fulfilled' ? threadsResult.value : null;
      const friendsData = friendsResult.status === 'fulfilled' ? friendsResult.value : null;
      const notifData = notifResult.status === 'fulfilled' ? notifResult.value : null;

      setRecentThreads(threadsData?.items || []);
      setFriendData(friendsData);
      setNotifications(notifData);

      // Build aggregated stats from the individual responses
      setStats({
        totalThreads: threadsData?.total || 0,
        friends: friendsData?.friends?.length || 0,
        pendingRequests: friendsData?.incoming?.length || 0,
        unreadNotifications: notifData?.unread_count || 0,
      });

      // Only show an error message if ALL three endpoints failed
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

  // Gate: unauthenticated users see a sign-in prompt
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
            {/* ── Quick Stats Grid ── */}
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

            {/* ── Quick Action Buttons ── */}
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

            {/* ── Recent Notifications ── */}
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
                        {/* Replace underscores with spaces for display (e.g., "friend_request" -> "friend request") */}
                        <span className="dash-notif-type">{notif.notification_type.replace(/_/g, ' ')}</span>
                        <span className="dash-notif-time">{formatTimeAgo(notif.created_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ── Latest Threads ── */}
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

            {/* ── Friends Preview (up to 8 chips) ── */}
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
