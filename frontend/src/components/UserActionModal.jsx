import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';

function UserActionModal({ user, isOpen, onClose, onRefresh }) {
  const navigate = useNavigate();
  const { session, profile } = useAuth();
  const [message, setMessage] = useState('');
  const [liveUser, setLiveUser] = useState(user);

  useEffect(() => {
    setLiveUser(user);
  }, [user]);

  useEffect(() => {
    async function loadFreshUser() {
      if (!isOpen || !user?.id || !session?.access_token) {
        return;
      }
      try {
        const data = await apiRequest(`/users/${user.id}`, {
          headers: getHeaders(session.access_token),
        });
        setLiveUser(data);
      } catch {
        setLiveUser(user);
      }
    }

    loadFreshUser();
  }, [isOpen, user?.id, session?.access_token]);

  const activeUser = liveUser || user;
  const avatarSrc = useMemo(() => {
    if (!activeUser?.avatar_url) {
      return null;
    }
    return activeUser.avatar_url.startsWith('http')
      ? activeUser.avatar_url
      : assetUrl(activeUser.avatar_url);
  }, [activeUser?.avatar_url]);

  if (!isOpen || !activeUser) {
    return null;
  }

  const isSelf = profile?.id === activeUser.id;

  async function refreshUserState() {
    if (!session?.access_token) {
      return;
    }
    const data = await apiRequest(`/users/${activeUser.id}`, {
      headers: getHeaders(session.access_token),
    });
    setLiveUser(data);
    await onRefresh?.();
  }

  async function handleFriend() {
    try {
      const data = await apiRequest(`/users/${activeUser.id}/friend`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
      });
      setMessage(data.message);
      await refreshUserState();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function handleMessage() {
    try {
      const data = await apiRequest(
        `/chat/direct/${encodeURIComponent(activeUser.username)}`,
        {
          method: 'POST',
          headers: getHeaders(session.access_token),
        }
      );
      onClose();
      navigate(`/chat?room=${data.id}`);
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function handleReport() {
    try {
      const data = await apiRequest(`/users/${activeUser.id}/report`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({
          reason: `Reported @${activeUser.username} from profile modal`,
        }),
      });
      setMessage(data.message);
    } catch (error) {
      setMessage(error.message);
    }
  }

  const friendStatus = activeUser.friendship_status || 'none';

  const friendLabel =
    friendStatus === 'friends'
      ? 'Friends'
      : friendStatus === 'outgoing_pending'
        ? 'Request sent'
        : friendStatus === 'incoming_pending'
          ? 'Accept request'
          : 'Add friend';

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-4)' }}>
          <h3>@{activeUser.username}</h3>
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-4)', alignItems: 'center', marginBottom: 'var(--space-4)' }}>
          {avatarSrc ? (
            <img
              className="profile-avatar-preview"
              src={avatarSrc}
              alt={activeUser.username}
            />
          ) : (
            <div className="profile-badge">
              {activeUser.username.slice(0, 2).toUpperCase()}
            </div>
          )}
          <div>
            <strong>@{activeUser.username}</strong>
            <p className="muted-copy">
              {activeUser.role} &middot;{' '}
              {activeUser.is_verified ? 'Verified' : 'Unverified'}
            </p>
            <p className="muted-copy">
              {activeUser.bio || 'No bio yet.'}
            </p>
            {!isSelf && (
              <p className="muted-copy">
                Status: {friendStatus.replace('_', ' ')}
              </p>
            )}
          </div>
        </div>

        {!isSelf && (
          <div className="edit-inline-actions">
            <button
              className="action-button"
              type="button"
              onClick={handleMessage}
            >
              Message
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={handleFriend}
              disabled={
                friendStatus === 'friends' ||
                friendStatus === 'outgoing_pending'
              }
            >
              {friendLabel}
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={handleReport}
            >
              Report
            </button>
          </div>
        )}

        {isSelf && (
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              onClose();
              navigate('/profile');
            }}
          >
            Go to my profile
          </button>
        )}

        {message && <p className="success-copy">{message}</p>}
      </div>
    </div>
  );
}

export default UserActionModal;
