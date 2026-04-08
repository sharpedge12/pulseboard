/**
 * @fileoverview useNotifications — Custom hook for notification management with WebSocket.
 *
 * This hook manages the full notification lifecycle:
 *   1. **Initial load**: Fetches existing notifications from the REST API on mount.
 *   2. **Real-time delivery**: Connects to a WebSocket (`/ws/notifications`) to receive
 *      new notifications as they happen (new replies, mentions, friend requests, etc.).
 *   3. **Browser notifications**: If the user has granted permission, shows native
 *      desktop notifications via the Web Notifications API.
 *   4. **Read management**: Provides `markAllRead()` and `markOneRead()` functions
 *      to update read status both locally and on the server.
 *   5. **Permission management**: Exposes `browserPermission` state and a
 *      `requestBrowserPermission()` function for the Notification API.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **WebSocket with auto-reconnect**: The `onclose` handler schedules a reconnect
 *     after 3 seconds. The `closed` flag prevents reconnection after the component
 *     unmounts. This provides resilient real-time connectivity.
 *   - **Hybrid real-time + REST**: The initial state is loaded from REST, and
 *     WebSocket messages are merged into the local state. This is a common pattern
 *     because WebSocket only delivers events that happen after connection — you need
 *     REST for historical data.
 *   - **WebSocket-only notification IDs**: Notifications received via WebSocket get
 *     synthetic IDs prefixed with `ws-` (e.g., `ws-1703123456789`). These IDs are
 *     used to distinguish them from persisted notifications when calling `markOneRead`.
 *     WS-only notifications are marked as read locally without an API call.
 *   - **Browser Notification API**: The hook creates native `new Notification()` objects
 *     when a WS message arrives and the user has granted permission. The `tag` property
 *     deduplicates notifications from the same source.
 *   - **Cleanup pattern**: The cleanup function sets `closed = true`, clears the
 *     reconnect timer, and closes the socket. This prevents memory leaks and
 *     state updates on unmounted components.
 *
 * @module hooks/useNotifications
 */

import { useEffect, useRef, useState } from 'react';
import { apiRequest, getHeaders, WS_BASE_URL } from '../lib/api';

/**
 * Custom hook for managing notifications with real-time WebSocket updates.
 *
 * @param {string|null} token - JWT access token. If null, the hook is inactive
 *   (no API calls, no WebSocket connection).
 * @returns {Object} Notification state and actions:
 *   @returns {Array} notifications - Array of notification objects.
 *   @returns {number} unreadCount - Number of unread notifications.
 *   @returns {Function} markAllRead - Marks all notifications as read (API + local).
 *   @returns {Function} markOneRead - Marks specific notification(s) as read.
 *   @returns {string} browserPermission - 'granted' | 'denied' | 'default'.
 *   @returns {Function} requestBrowserPermission - Prompts the user for notification permission.
 */
export function useNotifications(token) {
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);

  /**
   * Browser notification permission state.
   * Initialized from the current browser permission on mount.
   * SSR-safe: checks for window and Notification API existence.
   */
  const [browserPermission, setBrowserPermission] = useState(
    typeof window !== 'undefined' && 'Notification' in window ? window.Notification.permission : 'denied'
  );

  /**
   * Effect 1: Load existing notifications from the REST API.
   *
   * Runs when the token changes (login/logout). The `ignore` flag prevents
   * state updates if the component unmounts or the token changes before the
   * request completes.
   */
  useEffect(() => {
    let ignore = false;

    async function loadNotifications() {
      if (!token) {
        setNotifications([]);
        setUnreadCount(0);
        return;
      }

      try {
        const data = await apiRequest('/notifications', {
          headers: getHeaders(token),
        });
        if (!ignore) {
          setNotifications(data.items);
          setUnreadCount(data.unread_count);
        }
      } catch (error) {
        if (!ignore) {
          setNotifications([]);
          setUnreadCount(0);
        }
      }
    }

    loadNotifications();
    return () => {
      ignore = true;
    };
  }, [token]);

  /** Ref to the active WebSocket instance (for cleanup). */
  const socketRef = useRef(null);
  /** Ref to the reconnect timer ID (for cleanup). */
  const reconnectTimerRef = useRef(null);

  /**
   * Effect 2: WebSocket connection for real-time notification delivery.
   *
   * Connects to `/ws/notifications?token=...` and listens for incoming messages.
   * Each message is parsed as JSON and merged into the local notifications array.
   *
   * Auto-reconnect: on close, schedules a reconnect after 3 seconds.
   * The `closed` flag prevents reconnection after cleanup (unmount or token change).
   */
  useEffect(() => {
    if (!token) {
      return undefined;
    }

    let closed = false;

    function connect() {
      if (closed) return;
      const socket = new WebSocket(`${WS_BASE_URL}/ws/notifications?token=${token}`);
      socketRef.current = socket;

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          // Create a local notification object with a synthetic WS ID
          const nextNotification = {
            id: `ws-${Date.now()}`,  // Synthetic ID — not persisted on server
            notification_type: data.notification_type,
            title: data.title,
            payload: data,
            is_read: false,
            created_at: new Date().toISOString(),
          };

          // Update local state: increment unread count and prepend notification
          setUnreadCount((currentValue) => currentValue + 1);
          setNotifications((currentValue) => [nextNotification, ...currentValue]);

          // Show a browser desktop notification if permission is granted
          if (typeof window !== 'undefined' && 'Notification' in window && window.Notification.permission === 'granted') {
            new window.Notification(data.title, {
              body: data.notification_type,
              // `tag` deduplicates: same tag replaces the previous notification
              tag: `${data.notification_type}-${data.room_id || data.thread_id || Date.now()}`,
            });
          }
        } catch {
          /* ignore malformed messages */
        }
      };

      // Auto-reconnect after 3 seconds on close
      socket.onclose = () => {
        if (!closed) {
          reconnectTimerRef.current = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    // Cleanup: prevent reconnection, clear timer, close socket
    return () => {
      closed = true;
      clearTimeout(reconnectTimerRef.current);
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [token]);

  /**
   * Marks all notifications as read on the server and updates local state.
   * The server returns the updated notification list and unread count.
   */
  async function markAllRead() {
    if (!token) {
      return;
    }

    const data = await apiRequest('/notifications/read-all', {
      method: 'PATCH',
      headers: getHeaders(token),
    });
    setNotifications(data.items);
    setUnreadCount(data.unread_count);
  }

  /**
   * Marks one or more notifications as read.
   *
   * Handles two cases:
   *   1. **WebSocket-only notifications** (IDs starting with 'ws-'): Updated locally
   *      only, since they don't exist on the server.
   *   2. **Persisted notifications**: Sends a PATCH to the server for each ID,
   *      then updates local state.
   *
   * @param {string|number|Array} notificationIds - Single ID or array of IDs.
   */
  async function markOneRead(notificationIds) {
    const ids = Array.isArray(notificationIds) ? notificationIds : [notificationIds];

    // If all IDs are WebSocket-only, just update local state
    if (!token || ids.every((id) => String(id).startsWith('ws-'))) {
      setNotifications((currentValue) => currentValue.map((item) => (ids.includes(item.id) ? { ...item, is_read: true } : item)));
      setUnreadCount((currentValue) => Math.max(0, currentValue - ids.length));
      return;
    }

    // For persisted notifications, send API requests
    const persistedIds = ids.filter((id) => !String(id).startsWith('ws-'));
    await Promise.all(
      persistedIds.map((id) =>
        apiRequest(`/notifications/${id}/read`, {
          method: 'PATCH',
          headers: getHeaders(token),
        })
      )
    );

    // Update local state for all IDs (both WS-only and persisted)
    setNotifications((currentValue) => currentValue.map((item) => (ids.includes(item.id) ? { ...item, is_read: true } : item)));
    setUnreadCount((currentValue) => Math.max(0, currentValue - ids.length));
  }

  /**
   * Requests browser notification permission from the user.
   * Returns the resulting permission string ('granted', 'denied', or 'default').
   *
   * Interview note: The Notification.requestPermission() API shows a browser-level
   * prompt. Once the user clicks "Block", the permission becomes 'denied' and cannot
   * be re-requested programmatically — the user must change it in browser settings.
   *
   * @returns {Promise<string>} The permission result.
   */
  async function requestBrowserPermission() {
    if (typeof window === 'undefined' || !('Notification' in window)) {
      return 'denied';
    }
    const permission = await window.Notification.requestPermission();
    setBrowserPermission(permission);
    return permission;
  }

  return {
    notifications,
    unreadCount,
    markAllRead,
    markOneRead,
    browserPermission,
    requestBrowserPermission,
  };
}
