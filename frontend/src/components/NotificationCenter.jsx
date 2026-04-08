/**
 * @file NotificationCenter.jsx
 * @description Slide-out notification drawer with grouped notifications, mark-read,
 *              and click-to-navigate functionality.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Data Transformation Pipeline** — Raw notifications from the API are
 *    grouped/merged by a key (type + actor + target), counted, and sorted.
 *    This is a real-world example of transforming server data for display.
 *    Functions: buildNotificationKey -> mergeNotifications -> buildMergedTitle.
 *
 * 2. **useMemo for Expensive Computations** — `mergeNotifications` iterates
 *    the full notification list. `useMemo` ensures this only re-runs when
 *    `notifications` or `showAll` changes, not on every render.
 *
 * 3. **Component Composition** — NotificationCenter is composed of several
 *    small helper functions (buildNotificationKey, buildMergedTitle,
 *    resolveNotificationTarget, NotificationTypeIcon, getTypeColorClass).
 *    This is the "many small functions" pattern for readability.
 *
 * 4. **Backdrop Click to Close** — The drawer uses a transparent backdrop div
 *    that catches clicks outside the drawer panel, closing it. This is the
 *    standard pattern for modals and drawers in React.
 *
 * 5. **Async Click Handlers** — Clicking a notification marks it as read
 *    (async API call), navigates to the target, then closes the drawer.
 *    Operations are sequenced with `await` to ensure correct order.
 *
 * @see {@link ../lib/timeUtils.js} for formatTimeAgo helper
 */

import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { formatTimeAgo } from '../lib/timeUtils';

/**
 * Builds a grouping key for a notification to enable deduplication/merging.
 *
 * Notifications from the same actor, of the same type, targeting the same
 * entity (thread, chat room, user) are grouped together. For example,
 * 5 messages from @alice in room #3 become "5 messages from alice".
 *
 * INTERVIEW TIP: Grouping keys are a common data-processing pattern.
 * The key must be deterministic and capture the "identity" of the group.
 * Think of it like a composite primary key in a database.
 *
 * @param {Object} notification - Notification object from the API
 * @returns {string} A grouping key like "direct_message:alice:3"
 */
function buildNotificationKey(notification) {
  // Extract the actor name from various payload fields (different notification
  // types store the actor in different fields — this normalizes them)
  const actor =
    notification.payload?.from_username ||
    notification.payload?.reporter_username ||
    notification.payload?.username ||
    notification.title;
  const type = notification.notification_type;
  // Extract the target entity (room, thread, or user being reported)
  const target =
    notification.payload?.room_id ||
    notification.payload?.thread_id ||
    notification.payload?.reported_user_id ||
    'general';
  return `${type}:${actor}:${target}`;
}

/**
 * Builds a human-readable merged title for a grouped notification.
 *
 * Examples:
 *   - Single notification: "alice replied to your thread" (original title)
 *   - 5 grouped messages:  "5 messages from alice"
 *   - 3 friend requests:   "3 friend requests from bob"
 *   - Other grouped:       "3x Thread reply notification"
 *
 * @param {Object} notification - Merged notification with `count` field
 * @returns {string} The display title
 */
function buildMergedTitle(notification) {
  const actor =
    notification.payload?.from_username ||
    notification.payload?.reporter_username ||
    notification.payload?.username;

  // If only 1 notification or no actor, use the original title
  if (notification.count <= 1 || !actor) {
    return notification.title;
  }
  // Type-specific plural titles
  if (
    notification.notification_type === 'direct_message' ||
    notification.notification_type === 'group_message'
  ) {
    return `${notification.count} messages from ${actor}`;
  }
  if (notification.notification_type === 'friend_request') {
    return `${notification.count} friend requests from ${actor}`;
  }
  // Generic fallback for other notification types
  return `${notification.count}x ${notification.title}`;
}

/**
 * Merges an array of notifications by grouping key.
 *
 * ALGORITHM:
 * 1. Iterate through all notifications.
 * 2. For each, compute a grouping key via buildNotificationKey().
 * 3. If the key already exists in the Map, increment its count,
 *    collect its ID, update the timestamp to the most recent, and
 *    mark as unread if ANY notification in the group is unread.
 * 4. Sort the resulting groups by most recent first.
 *
 * INTERVIEW TIP: Using a `Map` for grouping is O(n) — one pass through
 * the array. This is more efficient than sorting + grouping, which is O(n log n).
 * The Map preserves insertion order, but we re-sort at the end anyway.
 *
 * @param {Array<Object>} items - Raw notification array from the API
 * @returns {Array<Object>} Merged notifications with `count` and `mergedIds` fields
 */
function mergeNotifications(items) {
  /** @type {Map<string, Object>} Groups keyed by notification identity */
  const groups = new Map();

  items.forEach((notification) => {
    const key = buildNotificationKey(notification);
    const current = groups.get(key);

    if (!current) {
      // First notification with this key — seed the group
      groups.set(key, { ...notification, count: 1, mergedIds: [notification.id] });
      return;
    }

    // Merge into existing group
    current.count += 1;
    current.mergedIds.push(notification.id);
    // Keep the most recent timestamp (for sorting and display)
    current.created_at =
      notification.created_at > current.created_at
        ? notification.created_at
        : current.created_at;
    // Group is unread if ANY member is unread (logical AND: all must be read for group to be read)
    current.is_read = current.is_read && notification.is_read;
    groups.set(key, current);
  });

  // Convert Map values to array and sort by most recent first
  return Array.from(groups.values()).sort(
    (a, b) => new Date(b.created_at) - new Date(a.created_at)
  );
}

/**
 * Resolves the navigation target URL for a notification.
 *
 * Different notification types link to different pages:
 *   - moderation_action -> /profile (user's own profile to see the action)
 *   - chat messages     -> /chat?room=<id>
 *   - thread replies    -> /threads/<id> or /threads/<id>#post-<postId>
 *   - user reports      -> /profile/<reported_user_id>
 *   - friend requests   -> /profile/<from_user_id>
 *   - fallback          -> /profile
 *
 * INTERVIEW TIP: This is the "strategy pattern" — selecting behavior based
 * on a discriminator (notification_type / payload shape). Each condition
 * maps a notification to its logical destination in the app.
 *
 * @param {Object} notification - Notification with `notification_type` and `payload`
 * @returns {string} The URL path to navigate to
 */
function resolveNotificationTarget(notification) {
  if (notification.notification_type === 'moderation_action') {
    return '/profile';
  }
  if (notification.payload?.room_id) {
    return `/chat?room=${notification.payload.room_id}`;
  }
  if (notification.payload?.thread_id) {
    const base = `/threads/${notification.payload.thread_id}`;
    // Deep-link to a specific post via URL hash anchor
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

/**
 * NotificationTypeIcon — Renders an SVG icon based on the notification type.
 *
 * INTERVIEW TIP: This is a "lookup table" component pattern. Instead of
 * a switch statement or if/else chain in JSX, it uses an object literal
 * (`iconMap`) to map notification types to SVG elements. This is cleaner
 * and easier to extend — just add a new key-value pair.
 *
 * @param {Object} props
 * @param {string} props.type - The notification_type string (e.g., "direct_message")
 * @returns {JSX.Element} An SVG icon wrapped in a span
 */
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

  // Fallback icon for unknown notification types (generic info circle)
  const icon = iconMap[type] || (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  );

  return <span className="notif-icon">{icon}</span>;
}

/**
 * Maps notification type to a CSS color class for the icon container.
 *
 * INTERVIEW TIP: Separating color logic from the icon component follows
 * the separation of concerns principle. The icon handles SHAPE, this
 * function handles COLOR. They can evolve independently.
 *
 * @param {string} type - The notification_type string
 * @returns {string} CSS class name for coloring the icon, or empty string
 */
function getTypeColorClass(type) {
  if (type === 'direct_message' || type === 'group_message') return 'notif-icon-chat';
  if (type === 'friend_request') return 'notif-icon-friend';
  if (type === 'user_report') return 'notif-icon-report';
  if (type === 'moderation_action') return 'notif-icon-warning';
  if (type === 'mention') return 'notif-icon-mention';
  return '';
}

/**
 * NotificationCenter — Slide-out drawer displaying the user's notifications.
 *
 * Features:
 * - Groups/merges duplicate notifications (e.g., "5 messages from alice")
 * - Toggle between "Unread" and "All" views
 * - "Mark all read" bulk action
 * - Click a notification to mark it read, navigate to its target, and close the drawer
 * - Visual indicators: unread dot, type-specific colored icons, relative timestamps
 *
 * ARCHITECTURE:
 * This component does NOT fetch notifications itself. It receives them as
 * props from a parent (typically MainLayout), which manages the WebSocket
 * connection and API polling. This is the "smart parent, dumb child" pattern
 * (also called "container/presentational" in older React conventions).
 *
 * @param {Object} props
 * @param {boolean}  props.isOpen         - Whether the drawer is currently visible
 * @param {Array}    props.notifications  - Full array of notification objects from the API
 * @param {number}   props.unreadCount    - Count of unread notifications (for badge display)
 * @param {Function} props.markAllRead    - Callback to mark all notifications as read
 * @param {Function} props.markOneRead    - Callback: (ids: number[]) => Promise<void> — marks specific notification(s) as read
 * @param {Function} props.onClose        - Callback to close the drawer
 * @returns {JSX.Element|null} The notification drawer, or null when closed
 */
function NotificationCenter({
  isOpen,
  notifications,
  unreadCount,
  markAllRead,
  markOneRead,
  onClose,
}) {
  const navigate = useNavigate();

  /**
   * Toggle between showing all notifications vs. unread only.
   * Defaults to false (unread only) — users typically want to see what's new.
   */
  const [showAll, setShowAll] = useState(false);

  /**
   * Compute the visible (and merged/grouped) notification list.
   *
   * INTERVIEW TIP: `useMemo` is critical here. `mergeNotifications` iterates
   * the entire notification array and builds a Map. Without memoization,
   * this would run on EVERY render (including when the user hovers over
   * notification cards, triggering state changes in child elements).
   *
   * The dependency array `[notifications, showAll]` means this only
   * recomputes when the notification data changes or the filter toggles.
   *
   * @type {Array<Object>} Merged/sorted notification groups
   */
  const visibleNotifications = useMemo(() => {
    const filtered = showAll
      ? notifications
      : notifications.filter((item) => !item.is_read);
    return mergeNotifications(filtered);
  }, [notifications, showAll]);

  /*
   * Early return — if the drawer isn't open, render nothing.
   *
   * INTERVIEW TIP: This is more efficient than wrapping the entire JSX
   * in a conditional. It avoids computing any rendered output when the
   * drawer is hidden, which includes the useMemo above (though useMemo
   * still runs since hooks must be called unconditionally — that's a
   * React rule: hooks cannot be called inside conditions).
   */
  if (!isOpen) {
    return null;
  }

  return (
    <>
      {/*
        ── Backdrop ──────────────────────────────────────────────
        A full-screen transparent overlay behind the drawer.
        Clicking it closes the drawer (standard UX pattern for drawers/modals).

        INTERVIEW TIP: The backdrop is a separate element from the drawer.
        Its onClick calls onClose directly. The drawer itself uses
        e.stopPropagation() implicitly (clicks inside the <aside> don't
        reach the backdrop because they are sibling elements, not nested).
        Actually here they ARE siblings within a Fragment, so the backdrop
        click won't interfere with the drawer — they're at the same DOM level.
      */}
      <div className="drawer-backdrop" onClick={onClose} />

      <aside className="notification-drawer">
        {/* ── Drawer Header ──────────────────────────────────────── */}
        <div className="drawer-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h3>Notifications</h3>
            {/* Unread count badge — only shown when there are unread notifications */}
            {unreadCount > 0 && (
              <span className="notif-badge">{unreadCount}</span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            {/*
              Toggle pill — switches between "Unread" and "All" views.
              Uses the functional updater form: setShowAll(c => !c).

              INTERVIEW TIP: The functional updater `(c) => !c` is safer
              than `!showAll` because it reads the LATEST state value.
              This matters when React batches multiple state updates.
            */}
            <button
              className="pill"
              type="button"
              onClick={() => setShowAll((c) => !c)}
            >
              {showAll ? 'Unread' : 'All'}
            </button>
            {/* Mark all read — only shown when there are unread notifications */}
            {unreadCount > 0 && (
              <button className="action-link" type="button" onClick={markAllRead}>
                Mark all read
              </button>
            )}
            {/* Close button */}
            <button className="drawer-close" type="button" onClick={onClose}>
              &times;
            </button>
          </div>
        </div>

        {/* ── Drawer Body (notification list) ────────────────────── */}
        <div className="drawer-body">
          {/*
            Empty state — shows a bell icon and helpful message.

            INTERVIEW TIP: Always design for the empty state. Users should
            see a clear, informative message — not a blank white panel.
            The message changes based on the current filter (all vs. unread).
          */}
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

          {/*
            ── Notification Cards ─────────────────────────────────
            Each merged notification renders as a clickable card.

            INTERVIEW TIP: Each card is a <button>, not a <div>, because it
            has a click action (mark read + navigate). Semantic HTML matters
            for accessibility — buttons are focusable and announced by
            screen readers. The `key` uses both `id` and `count` to force
            React to re-render when the merge count changes.
          */}
          {visibleNotifications.map((notification) => (
            <button
              key={`${notification.id}-${notification.count}`}
              className={`notif-card ${
                notification.is_read ? '' : 'unread'
              }`}
              type="button"
              onClick={async () => {
                /*
                 * INTERVIEW TIP: Async click handler pattern.
                 * 1. Mark the notification(s) as read via API
                 * 2. Navigate to the notification's target page
                 * 3. Close the drawer
                 *
                 * The `await` ensures the read status is persisted
                 * before navigation (prevents flicker if the user
                 * navigates back quickly).
                 */
                await markOneRead(notification.mergedIds || [notification.id]);
                navigate(resolveNotificationTarget(notification));
                onClose();
              }}
            >
              {/* Type-specific colored icon */}
              <div className={`notif-icon ${getTypeColorClass(notification.notification_type)}`}>
                <NotificationTypeIcon type={notification.notification_type} />
              </div>
              <div className="notif-body">
                {/* Merged title (e.g., "5 messages from alice") */}
                <span className="notif-title">{buildMergedTitle(notification)}</span>
                <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
                  {/* Human-readable type label (underscores replaced with spaces) */}
                  <span className="notif-type">
                    {notification.notification_type.replace(/_/g, ' ')}
                  </span>
                  {/* Relative timestamp (e.g., "2h ago") */}
                  <span className="notif-time">
                    {formatTimeAgo(notification.created_at)}
                  </span>
                </div>
              </div>
              {/* Blue unread indicator dot — only shown for unread notifications */}
              {!notification.is_read && <span className="notif-dot" />}
            </button>
          ))}
        </div>
      </aside>
    </>
  );
}

export default NotificationCenter;
