import { useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../lib/api';

/**
 * Connects to the /ws/global WebSocket for app-wide real-time events.
 * Calls the provided callbacks when relevant events arrive.
 *
 * @param {{ onCategoryCreated?: (category: object) => void }} handlers
 */
export function useGlobalUpdates(handlers) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);

  useEffect(() => {
    let closed = false;

    function connect() {
      if (closed) return;
      const socket = new WebSocket(`${WS_BASE_URL}/ws/global`);
      socketRef.current = socket;

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event === 'category_created' && handlersRef.current.onCategoryCreated) {
            handlersRef.current.onCategoryCreated(payload.category);
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
  }, []);
}
