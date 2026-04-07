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

  const isPulseBot = user?.username === 'pulse';
  const avatarSrc = isPulseBot
    ? '/pulse-avatar.svg'
    : user.avatar_url
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
          className="user-identity"
          type="button"
          onClick={() => setIsModalOpen(true)}
          style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0 }}
        >
          <div className="user-avatar" style={{ position: 'relative' }}>
            {avatarSrc ? (
              <img src={avatarSrc} alt={user.username} />
            ) : (
              <span className="user-avatar-fallback">{initials}</span>
            )}
            {online && <span className="online-indicator" />}
          </div>
          <span>
            <span className="user-identity-name">@{user.username}</span>
            {showRole && (
              <span className="user-identity-role"> &middot; {user.role}</span>
            )}
          </span>
        </button>
        {!compact && (
          <button
            className="user-identity-link"
            type="button"
            onClick={() => navigate(`/profile/${user.id}`)}
            style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0 }}
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
