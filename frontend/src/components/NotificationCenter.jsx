import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { formatTimeAgo } from '../lib/timeUtils';

function buildNotificationKey(notification) {
  const actor =
    notification.payload?.from_username ||
    notification.payload?.reporter_username ||
    notification.payload?.username ||
    notification.title;
  const type = notification.notification_type;
  const target =
    notification.payload?.room_id ||
    notification.payload?.thread_id ||
    notification.payload?.reported_user_id ||
    'general';
  return `${type}:${actor}:${target}`;
}

function buildMergedTitle(notification) {
  const actor =
    notification.payload?.from_username ||
    notification.payload?.reporter_username ||
    notification.payload?.username;
  if (notification.count <= 1 || !actor) {
    return notification.title;
  }
  if (
    notification.notification_type === 'direct_message' ||
    notification.notification_type === 'group_message'
  ) {
    return `${notification.count} messages from ${actor}`;
  }
  if (notification.notification_type === 'friend_request') {
    return `${notification.count} friend requests from ${actor}`;
  }
  return `${notification.count}x ${notification.title}`;
}

function mergeNotifications(items) {
  const groups = new Map();
  items.forEach((notification) => {
    const key = buildNotificationKey(notification);
    const current = groups.get(key);
    if (!current) {
      groups.set(key, { ...notification, count: 1, mergedIds: [notification.id] });
      return;
    }
    current.count += 1;
    current.mergedIds.push(notification.id);
    current.created_at =
      notification.created_at > current.created_at
        ? notification.created_at
        : current.created_at;
    current.is_read = current.is_read && notification.is_read;
    groups.set(key, current);
  });
  return Array.from(groups.values()).sort(
    (a, b) => new Date(b.created_at) - new Date(a.created_at)
  );
}

function resolveNotificationTarget(notification) {
  if (notification.notification_type === 'moderation_action') {
    return '/profile';
  }
  if (notification.payload?.room_id) {
    return `/chat?room=${notification.payload.room_id}`;
  }
  if (notification.payload?.thread_id) {
    const base = `/threads/${notification.payload.thread_id}`;
    if (notification.payload?.post_id) {
      return `${base}#post-${notification.payload.post_id}`;
    }
    return base;
  }
  if (notification.payload?.reported_user_id) {
    return `/profile/${notification.payload.reported_user_id}`;
  }
  if (notification.payload?.from_user_id) {
    return `/profile/${notification.payload.from_user_id}`;
  }
  return '/profile';
}

function NotificationTypeIcon({ type }) {
  const iconMap = {
    direct_message: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    ),
    group_message: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
    friend_request: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="8.5" cy="7" r="4" />
        <line x1="20" y1="8" x2="20" y2="14" />
        <line x1="23" y1="11" x2="17" y2="11" />
      </svg>
    ),
    user_report: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    ),
    moderation_action: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
    ),
    thread_reply: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="9 17 4 12 9 7" />
        <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
      </svg>
    ),
    mention: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="4" />
        <path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.92 7.94" />
      </svg>
    ),
  };

  const icon = iconMap[type] || (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  );

  return <span className="notif-icon">{icon}</span>;
}

function getTypeColorClass(type) {
  if (type === 'direct_message' || type === 'group_message') return 'notif-icon-chat';
  if (type === 'friend_request') return 'notif-icon-friend';
  if (type === 'user_report') return 'notif-icon-report';
  if (type === 'moderation_action') return 'notif-icon-warning';
  if (type === 'mention') return 'notif-icon-mention';
  return '';
}

function NotificationCenter({
  isOpen,
  notifications,
  unreadCount,
  markAllRead,
  markOneRead,
  onClose,
}) {
  const navigate = useNavigate();
  const [showAll, setShowAll] = useState(false);

  const visibleNotifications = useMemo(() => {
    const filtered = showAll
      ? notifications
      : notifications.filter((item) => !item.is_read);
    return mergeNotifications(filtered);
  }, [notifications, showAll]);

  if (!isOpen) {
    return null;
  }

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />

      <aside className="notification-drawer">
        <div className="drawer-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h3>Notifications</h3>
            {unreadCount > 0 && (
              <span className="notif-badge">{unreadCount}</span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <button
              className="pill"
              type="button"
              onClick={() => setShowAll((c) => !c)}
            >
              {showAll ? 'Unread' : 'All'}
            </button>
            {unreadCount > 0 && (
              <button className="action-link" type="button" onClick={markAllRead}>
                Mark all read
              </button>
            )}
            <button className="drawer-close" type="button" onClick={onClose}>
              &times;
            </button>
          </div>
        </div>

        <div className="drawer-body">
          {visibleNotifications.length === 0 && (
            <div className="notif-empty">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 0 1-3.46 0" />
              </svg>
              <p>
                {showAll ? 'No notifications yet' : 'All caught up!'}
              </p>
              <p className="muted-copy">
                {showAll
                  ? 'Notifications will appear here when you get messages, replies, or friend requests.'
                  : 'You have no unread notifications.'}
              </p>
            </div>
          )}
          {visibleNotifications.map((notification) => (
            <button
              key={`${notification.id}-${notification.count}`}
              className={`notif-card ${
                notification.is_read ? '' : 'unread'
              }`}
              type="button"
              onClick={async () => {
                await markOneRead(notification.mergedIds || [notification.id]);
                navigate(resolveNotificationTarget(notification));
                onClose();
              }}
            >
              <div className={`notif-icon ${getTypeColorClass(notification.notification_type)}`}>
                <NotificationTypeIcon type={notification.notification_type} />
              </div>
              <div className="notif-body">
                <span className="notif-title">{buildMergedTitle(notification)}</span>
                <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
                  <span className="notif-type">
                    {notification.notification_type.replace(/_/g, ' ')}
                  </span>
                  <span className="notif-time">
                    {formatTimeAgo(notification.created_at)}
                  </span>
                </div>
              </div>
              {!notification.is_read && <span className="notif-dot" />}
            </button>
          ))}
        </div>
      </aside>
    </>
  );
}

export default NotificationCenter;
