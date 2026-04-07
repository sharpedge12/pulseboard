import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useNotifications } from '../hooks/useNotifications';
import { apiRequest, API_BASE_URL, getHeaders, assetUrl } from '../lib/api';
import { validateFile, AVATAR_ACCEPT } from '../lib/uploadUtils';
import { formatDate, formatLastSeen, isUserOnline } from '../lib/timeUtils';
import UserIdentity from '../components/UserIdentity';

function ProfilePage() {
  const { userId } = useParams();
  const { profile, session, refreshProfile } = useAuth();
  const isOwnProfile = !userId || Number(userId) === profile?.id;
  const { browserPermission, requestBrowserPermission } = useNotifications(session?.access_token);
  const [bio, setBio] = useState('');
  const [username, setUsername] = useState('');
  const [message, setMessage] = useState('');
  const [viewedProfile, setViewedProfile] = useState(null);
  const [friendData, setFriendData] = useState({
    incoming: [],
    outgoing: [],
    friends: [],
  });

  // Sync local edit state when the auth profile loads or changes
  useEffect(() => {
    if (profile && isOwnProfile) {
      setBio(profile.bio || '');
      setUsername(profile.username || '');
    }
  }, [profile?.bio, profile?.username, isOwnProfile]);

  const activeProfile = userId ? viewedProfile : profile;
  const initials = activeProfile?.username
    ? activeProfile.username.slice(0, 2).toUpperCase()
    : 'DU';
  const avatarSrc = activeProfile?.avatar_url
    ? activeProfile.avatar_url.startsWith('http')
      ? activeProfile.avatar_url
      : assetUrl(activeProfile.avatar_url)
    : null;

  async function loadFriendships() {
    if (!session?.access_token || !isOwnProfile) {
      return;
    }
    const data = await apiRequest('/users/friends', {
      headers: getHeaders(session.access_token),
    });
    setFriendData(data);
  }

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
      await refreshProfile();
    } catch (error) {
      setMessage(error.message || 'Failed to save profile.');
    }
  }

  async function handleAvatarUpload(event) {
    if (!session?.access_token || !event.target.files?.[0] || !isOwnProfile) {
      return;
    }

    const file = event.target.files[0];
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
        await refreshProfile();
      } else {
        const err = await response.json().catch(() => ({}));
        setMessage(err.detail || 'Failed to upload avatar.');
      }
    } catch (err) {
      setMessage(err.message || 'Failed to upload avatar.');
    }
    event.target.value = '';
  }

  async function handleFriendRequest(requestId, action) {
    try {
      const data = await apiRequest(`/users/friends/${requestId}/${action}`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
      });
      setMessage(data.message);
      await loadFriendships();
    } catch (error) {
      setMessage(error.message);
    }
  }

  return (
    <section className="page-grid profile-layout">
      {/* Profile header card — banner-style at top */}
      <div className="panel stack-gap" style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 'var(--space-4)' }}>
        {avatarSrc ? (
          <img className="profile-avatar-preview" src={avatarSrc} alt="avatar" />
        ) : (
          <div className="profile-badge">{initials}</div>
        )}
        <div>
          <h3>
            {activeProfile?.username || 'User'}
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
          {!isOwnProfile && activeProfile?.last_seen && (
            <span className="profile-last-seen">{formatLastSeen(activeProfile.last_seen)}</span>
          )}
          {!isOwnProfile && activeProfile?.bio && (
            <p className="muted-copy">{activeProfile.bio}</p>
          )}
        </div>
      </div>

      {/* Edit profile (own) or account info (other) */}
      {isOwnProfile ? (
        <div className="panel stack-gap">
          <h3>Edit Profile</h3>

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

      {/* Preferences (own profile only) */}
      {isOwnProfile && (
        <div className="panel stack-gap">
          <h3>Preferences</h3>
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

      {/* Friends section (own profile only) */}
      {isOwnProfile && (
        <div className="panel stack-gap">
          <h3>Friends</h3>
          <span className="muted-copy">Requests &amp; connections</span>

          {/* Incoming */}
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

          {/* Outgoing */}
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

          {/* Friends list */}
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
