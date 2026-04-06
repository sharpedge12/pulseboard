import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { isUserOnline } from '../lib/timeUtils';
import { assetUrl } from '../lib/api';
import UserActionModal from './UserActionModal';

function UserIdentity({ user, compact = false, showRole = true, onRefresh }) {
  const navigate = useNavigate();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const initials = useMemo(
    () => (user?.username ? user.username.slice(0, 2).toUpperCase() : 'DU'),
    [user?.username]
  );
  const online = user?.is_online || isUserOnline(user?.last_seen);

  if (!user) {
    return null;
  }

  const avatarSrc = user.avatar_url
    ? user.avatar_url.startsWith('http')
      ? user.avatar_url
      : assetUrl(user.avatar_url)
    : null;

  return (
    <>
      <div
        className={
          compact ? 'user-identity user-identity-compact' : 'user-identity'
        }
      >
        <button
          className="user-summary-button"
          type="button"
          onClick={() => setIsModalOpen(true)}
        >
          <div className="user-avatar-wrapper">
            {avatarSrc ? (
              <img className="user-avatar" src={avatarSrc} alt={user.username} />
            ) : (
              <div className="user-avatar user-avatar-fallback">{initials}</div>
            )}
            {online && <span className="online-dot" />}
          </div>
          <span>
            <strong>@{user.username}</strong>
            {showRole && (
              <span className="muted-copy"> &middot; {user.role}</span>
            )}
          </span>
        </button>
        {!compact && (
          <button
            className="reply-link"
            type="button"
            onClick={() => navigate(`/profile/${user.id}`)}
          >
            View profile
          </button>
        )}
      </div>
      <UserActionModal
        user={user}
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onRefresh={onRefresh}
      />
    </>
  );
}

export default UserIdentity;
