import { useEffect, useRef, useState } from 'react';
import { apiRequest, getHeaders, WS_BASE_URL } from '../lib/api';

export function useNotifications(token) {
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [browserPermission, setBrowserPermission] = useState(
    typeof window !== 'undefined' && 'Notification' in window ? window.Notification.permission : 'denied'
  );

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

  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);

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
          const nextNotification = {
            id: `ws-${Date.now()}`,
            notification_type: data.notification_type,
            title: data.title,
            payload: data,
            is_read: false,
            created_at: new Date().toISOString(),
          };
          setUnreadCount((currentValue) => currentValue + 1);
          setNotifications((currentValue) => [nextNotification, ...currentValue]);

          if (typeof window !== 'undefined' && 'Notification' in window && window.Notification.permission === 'granted') {
            new window.Notification(data.title, {
              body: data.notification_type,
              tag: `${data.notification_type}-${data.room_id || data.thread_id || Date.now()}`,
            });
          }
        } catch {
          /* ignore malformed messages */
        }
      };

      socket.onclose = () => {
        if (!closed) {
          reconnectTimerRef.current = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    return () => {
      closed = true;
      clearTimeout(reconnectTimerRef.current);
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [token]);

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

  async function markOneRead(notificationIds) {
    const ids = Array.isArray(notificationIds) ? notificationIds : [notificationIds];
    if (!token || ids.every((id) => String(id).startsWith('ws-'))) {
      setNotifications((currentValue) => currentValue.map((item) => (ids.includes(item.id) ? { ...item, is_read: true } : item)));
      setUnreadCount((currentValue) => Math.max(0, currentValue - ids.length));
      return;
    }

    const persistedIds = ids.filter((id) => !String(id).startsWith('ws-'));
    await Promise.all(
      persistedIds.map((id) =>
        apiRequest(`/notifications/${id}/read`, {
          method: 'PATCH',
          headers: getHeaders(token),
        })
      )
    );
    setNotifications((currentValue) => currentValue.map((item) => (ids.includes(item.id) ? { ...item, is_read: true } : item)));
    setUnreadCount((currentValue) => Math.max(0, currentValue - ids.length));
  }

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
