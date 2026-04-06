import { useEffect, useRef, useState } from 'react';
import { apiRequest, getHeaders, WS_BASE_URL } from '../lib/api';

export function useChatRoom(roomId, token) {
  const [messages, setMessages] = useState([]);
  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);

  useEffect(() => {
    let ignore = false;

    async function loadMessages() {
      if (!roomId || !token) {
        setMessages([]);
        return;
      }

      try {
        const data = await apiRequest(`/chat/rooms/${roomId}/messages`, {
          headers: getHeaders(token),
        });
        if (!ignore) {
          setMessages(data);
        }
      } catch (error) {
        if (!ignore) {
          setMessages([]);
        }
      }
    }

    loadMessages();
    return () => {
      ignore = true;
    };
  }, [roomId, token]);

  useEffect(() => {
    if (!roomId || !token) {
      return undefined;
    }

    let closed = false;

    function connect() {
      if (closed) return;
      const socket = new WebSocket(`${WS_BASE_URL}/ws/chat/${roomId}?token=${token}`);
      socketRef.current = socket;

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event === 'message_created') {
            setMessages((currentValue) => [...currentValue, payload.message]);
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
  }, [roomId, token]);

  return { messages, setMessages };
}
