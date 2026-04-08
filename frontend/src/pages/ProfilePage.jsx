/**
 * @fileoverview ProfilePage — User profile view and editor.
 *
 * This page serves a dual purpose:
 *   1. **Own profile** (`/profile`): Shows an editable profile with avatar upload,
 *      username/bio editing, desktop notification preferences, and a friends list
 *      with incoming/outgoing request management.
 *   2. **Other user's profile** (`/profile/:userId`): Shows a read-only view with
 *      the user's avatar, username, role, bio, online status, and friend actions.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Dual-mode component**: A single component handles both "view own" and
 *     "view other" modes. The `isOwnProfile` flag drives conditional rendering.
 *     This avoids duplicating layout/styling across two separate components.
 *   - **Controlled form with external sync**: The `bio` and `username` state values
 *     are initialized from `profile` (AuthContext) via a `useEffect`. This means the
 *     form stays in sync with the server state, but the user can freely edit without
 *     immediately pushing changes. The `handleProfileSave` function sends changes
 *     to the backend and calls `refreshProfile()` to update the global auth context.
 *   - **Avatar URL handling**: OAuth users may have external avatar URLs (e.g.,
 *     `https://lh3.googleusercontent.com/...`). The component checks if the URL
 *     starts with `http` before calling `assetUrl()` (which prepends the API base
 *     URL for relative paths). This prevents broken images for OAuth users.
 *   - **Browser notification permissions**: Uses the `useNotifications` hook to
 *     check and request browser notification permissions. The Notification API
 *     has three states: 'default' (not asked), 'granted', 'denied'.
 *   - **Friend request management**: The friends section shows three lists
 *     (incoming, outgoing, friends) and supports accept/decline actions. After
 *     each action, `loadFriendships()` re-fetches the data to keep the UI in sync.
 *
 * @module pages/ProfilePage
 */

import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useNotifications } from '../hooks/useNotifications';
import { apiRequest, API_BASE_URL, getHeaders, assetUrl } from '../lib/api';
import { validateFile, AVATAR_ACCEPT } from '../lib/uploadUtils';
import { formatDate, formatLastSeen, isUserOnline } from '../lib/timeUtils';
import UserIdentity from '../components/UserIdentity';

/**
 * ProfilePage component — renders the user's own profile or another user's profile.
 *
 * @returns {JSX.Element}
 */
function ProfilePage() {
  /** userId from the URL — undefined when viewing own profile (/profile). */
  const { userId } = useParams();
  const { profile, session, refreshProfile } = useAuth();

  /** Determine if we're viewing our own profile (no userId param, or userId matches). */
  const isOwnProfile = !userId || Number(userId) === profile?.id;

  /** Browser notification permission state from the useNotifications hook. */
  const { browserPermission, requestBrowserPermission } = useNotifications(session?.access_token);

  // ── Editable form state (own profile only) ──
  const [bio, setBio] = useState('');
  const [username, setUsername] = useState('');
  const [message, setMessage] = useState('');          // Success/error feedback
  const [viewedProfile, setViewedProfile] = useState(null); // Other user's profile data

  /** Friend request data: incoming, outgoing, and accepted friends. */
  const [friendData, setFriendData] = useState({
    incoming: [],
    outgoing: [],
    friends: [],
  });

  /**
   * Sync local edit state when the auth profile loads or changes.
   *
   * Interview note: We watch `profile?.bio` and `profile?.username` (not the
   * entire `profile` object) to avoid unnecessary re-syncs. This only runs when
   * the profile data actually changes (e.g., after refreshProfile() or login).
   */
  useEffect(() => {
    if (profile && isOwnProfile) {
      setBio(profile.bio || '');
      setUsername(profile.username || '');
    }
  }, [profile?.bio, profile?.username, isOwnProfile]);

  /**
   * Determine which profile to display:
   * - If a userId param is present, use the fetched `viewedProfile`.
   * - Otherwise, use the authenticated user's `profile` from AuthContext.
   */
  const activeProfile = userId ? viewedProfile : profile;

  /** Fallback initials for the avatar placeholder (first 2 chars of username). */
  const initials = activeProfile?.username
    ? activeProfile.username.slice(0, 2).toUpperCase()
    : 'DU';

  /**
   * Avatar source URL — handles three cases:
   *   1. External URL (OAuth avatar): starts with 'http', use as-is.
   *   2. Relative path (uploaded avatar): prepend API base URL via assetUrl().
   *   3. No avatar: null (shows initials placeholder).
   */
  const avatarSrc = activeProfile?.avatar_url
    ? activeProfile.avatar_url.startsWith('http')
      ? activeProfile.avatar_url
      : assetUrl(activeProfile.avatar_url)
    : null;

  /**
   * Fetches the friend data (incoming, outgoing, accepted) for the current user.
   * Only loads for the user's own profile.
   */
  async function loadFriendships() {
    if (!session?.access_token || !isOwnProfile) {
      return;
    }
    const data = await apiRequest('/users/friends', {
      headers: getHeaders(session.access_token),
    });
    setFriendData(data);
  }

  /**
   * Loads the viewed user's profile (when viewing another user) and
   * the current user's friendships. Runs when userId, session, or profile changes.
   */
  useEffect(() => {
    async function loadViewedProfile() {
      if (!userId || !session?.access_token) {
        setViewedProfile(null);
        return;
      }

      try {
        const data = await apiRequest(`/users/${userId}`, {
          headers: getHeaders(session.access_token),
        });
        setViewedProfile(data);
      } catch (error) {
        setMessage(error.message);
      }
    }

    loadViewedProfile();
    loadFriendships();
  }, [userId, session, profile?.id]);

  /**
   * Saves the username and bio to the backend, then refreshes the global
   * auth profile to keep the navbar and other components up to date.
   */
  async function handleProfileSave() {
    if (!session?.access_token || !isOwnProfile) {
      return;
    }

    try {
      const data = await apiRequest('/users/me', {
        method: 'PATCH',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ username, bio }),
      });
      setMessage('Profile updated.');
      setBio(data.bio || '');
      setUsername(data.username || '');
      await refreshProfile(); // Update the global auth context
    } catch (error) {
      setMessage(error.message || 'Failed to save profile.');
    }
  }

  /**
   * Handles avatar file upload with client-side validation.
   * Uses a separate endpoint (`/users/me/avatar`) for avatar uploads.
   *
   * @param {Event} event - The file input change event.
   */
  async function handleAvatarUpload(event) {
    if (!session?.access_token || !event.target.files?.[0] || !isOwnProfile) {
      return;
    }

    const file = event.target.files[0];
    // imageOnly flag restricts to image MIME types (no videos/documents)
    const { valid, error } = validateFile(file, { imageOnly: true });
    if (!valid) {
      setMessage(error);
      event.target.value = '';
      return;
    }

    try {
      const formData = new FormData();
      formData.append('file', file);
      const response = await fetch(`${API_BASE_URL}/users/me/avatar`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
        body: formData,
      });
      if (response.ok) {
        setMessage('Avatar uploaded.');
        await refreshProfile(); // Refresh to show the new avatar everywhere
      } else {
        const err = await response.json().catch(() => ({}));
        setMessage(err.detail || 'Failed to upload avatar.');
      }
    } catch (err) {
      setMessage(err.message || 'Failed to upload avatar.');
    }
    event.target.value = '';
  }

  /**
   * Handles accepting or declining a friend request.
   *
   * @param {number} requestId - The friend request ID.
   * @param {'accept'|'decline'} action - The action to take.
   */
  async function handleFriendRequest(requestId, action) {
    try {
      const data = await apiRequest(`/users/friends/${requestId}/${action}`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
      });
      setMessage(data.message);
      await loadFriendships(); // Refresh the friend lists
    } catch (error) {
      setMessage(error.message);
    }
  }

  return (
    <section className="page-grid profile-layout">
      {/* ── Profile Header Card — banner-style at top ── */}
      <div className="panel stack-gap" style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 'var(--space-4)' }}>
        {/* Avatar: show image if available, otherwise show initials badge */}
        {avatarSrc ? (
          <img className="profile-avatar-preview" src={avatarSrc} alt="avatar" />
        ) : (
          <div className="profile-badge">{initials}</div>
        )}
        <div>
          <h3>
            {activeProfile?.username || 'User'}
            {/* Green dot online indicator — shown if user was active within 5 minutes */}
            {activeProfile?.last_seen && isUserOnline(activeProfile.last_seen) && (
              <span className="online-indicator" style={{ display: 'inline-block', position: 'relative', marginLeft: 'var(--space-2)' }} />
            )}
          </h3>
          <span className="muted-copy">
            {isOwnProfile ? 'Your profile' : 'Member profile'}
            {activeProfile?.role ? ` \u00B7 ${activeProfile.role}` : ''}
          </span>
          {activeProfile?.created_at && (
            <span className="profile-joined">Joined {formatDate(activeProfile.created_at)}</span>
          )}
          {/* Last seen — only shown on other users' profiles */}
          {!isOwnProfile && activeProfile?.last_seen && (
            <span className="profile-last-seen">{formatLastSeen(activeProfile.last_seen)}</span>
          )}
          {!isOwnProfile && activeProfile?.bio && (
            <p className="muted-copy">{activeProfile.bio}</p>
          )}
        </div>
      </div>

      {/* ── Edit Profile (own) or Account Info (other) ── */}
      {isOwnProfile ? (
        <div className="panel stack-gap">
          <h3>Edit Profile</h3>

          {/* Username input with Ctrl+Enter to save */}
          <input
            className="input"
            placeholder="Display name"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                handleProfileSave();
              }
            }}
          />
          {/* Bio textarea with Ctrl+Enter to save */}
          <textarea
            className="input"
            placeholder="Short bio"
            value={bio}
            onChange={(e) => setBio(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                handleProfileSave();
              }
            }}
          />
          <div className="edit-inline-actions">
            {/* Hidden file input triggered by the label button */}
            <label className="secondary-button" style={{ cursor: 'pointer' }}>
              Upload avatar
              <input
                type="file"
                accept={AVATAR_ACCEPT}
                hidden
                onChange={handleAvatarUpload}
              />
            </label>
            <button
              className="action-button"
              type="button"
              onClick={handleProfileSave}
            >
              Save profile <span className="kbd-hint">Ctrl+Enter</span>
            </button>
          </div>
          {message && <p className="success-copy">{message}</p>}
        </div>
      ) : (
        <div className="panel stack-gap">
          <h3>Account Info</h3>
          {activeProfile && (
            <UserIdentity user={activeProfile} onRefresh={loadFriendships} />
          )}
          <p className="muted-copy">
            Verification:{' '}
            {activeProfile?.is_verified ? 'Verified' : 'Pending'}
          </p>
          {message && <p className="success-copy">{message}</p>}
        </div>
      )}

      {/* ── Preferences Panel (own profile only) ── */}
      {isOwnProfile && (
        <div className="panel stack-gap">
          <h3>Preferences</h3>
          {/*
            Browser notification toggle.
            Three states: 'granted' (on), 'denied' (blocked by browser), 'default' (not asked).
            Interview note: The Notification API permission is browser-level — once denied,
            the user must change it in browser settings. We can't programmatically re-ask.
          */}
          <div className="pref-row">
            <div className="pref-info">
              <span className="pref-label">Desktop notifications</span>
              <span className="muted-copy">
                {browserPermission === 'granted'
                  ? 'Enabled — you will receive browser notifications for new messages and replies.'
                  : browserPermission === 'denied'
                    ? 'Blocked — update your browser settings to allow notifications from this site.'
                    : 'Disabled — enable to get notified about new replies, messages, and mentions.'}
              </span>
            </div>
            {browserPermission === 'granted' ? (
              <span className="pref-status pref-status-on">On</span>
            ) : browserPermission === 'denied' ? (
              <span className="pref-status pref-status-off">Blocked</span>
            ) : (
              <button
                className="action-button"
                type="button"
                onClick={requestBrowserPermission}
              >
                Enable
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Friends Section (own profile only) ── */}
      {isOwnProfile && (
        <div className="panel stack-gap">
          <h3>Friends</h3>
          <span className="muted-copy">Requests &amp; connections</span>

          {/* Incoming friend requests — can accept or decline */}
          <div className="stack-gap">
            <span className="card-label">Incoming requests</span>
            {friendData.incoming.length === 0 && (
              <p className="muted-copy">No incoming requests.</p>
            )}
            {friendData.incoming.map((request) => (
              <div key={request.id} className="admin-list-item">
                <UserIdentity
                  user={request.user}
                  compact
                  onRefresh={loadFriendships}
                />
                <div className="edit-inline-actions">
                  <button
                    className="action-button"
                    type="button"
                    onClick={() => handleFriendRequest(request.id, 'accept')}
                  >
                    Accept
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => handleFriendRequest(request.id, 'decline')}
                  >
                    Decline
                  </button>
                </div>
              </div>
            ))}
          </div>

          {/* Outgoing friend requests — pending, no actions available */}
          <div className="stack-gap">
            <span className="card-label">Outgoing requests</span>
            {friendData.outgoing.length === 0 && (
              <p className="muted-copy">No outgoing requests.</p>
            )}
            {friendData.outgoing.map((request) => (
              <div key={request.id} className="admin-list-item">
                <UserIdentity
                  user={request.user}
                  compact
                  onRefresh={loadFriendships}
                />
                <span className="muted-copy">Pending</span>
              </div>
            ))}
          </div>

          {/* Accepted friends list */}
          <div className="stack-gap">
            <span className="card-label">Friends</span>
            {friendData.friends.length === 0 && (
              <p className="muted-copy">No friends added yet.</p>
            )}
            {friendData.friends.map((friend) => (
              <div key={friend.id} className="admin-list-item">
                <UserIdentity
                  user={friend}
                  compact
                  onRefresh={loadFriendships}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export default ProfilePage;
