/**
 * @fileoverview useChatRoom — Custom hook for real-time chat room messaging.
 *
 * This hook manages the complete message lifecycle for a single chat room:
 *   1. **Initial load**: Fetches existing messages from the REST API when the
 *      room ID changes.
 *   2. **Real-time delivery**: Connects to a WebSocket (`/ws/chat/:roomId`) and
 *      appends new messages to the local state as they arrive.
 *   3. **Room switching**: When `roomId` changes, both the REST fetch and the
 *      WebSocket connection are re-established for the new room.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Two-effect separation**: This hook uses two separate `useEffect` hooks:
 *       a. **REST effect**: Loads historical messages from the API. Uses the
 *          `ignore` flag pattern to prevent stale state updates.
 *       b. **WebSocket effect**: Manages the real-time connection with auto-reconnect.
 *     Separating these concerns makes each effect simpler and easier to reason about.
 *     They share the same dependencies (`roomId`, `token`) so they re-run together.
 *   - **Append-only message updates**: When a `message_created` event arrives via
 *     WebSocket, the new message is appended to the end of the array. This works
 *     because chat messages are always in chronological order. The ChatPage's
 *     auto-scroll effect then scrolls to the new message.
 *   - **No manual message insertion**: After sending a message (in ChatPage), we
 *     do NOT manually append it to the local state. Instead, we rely on the
 *     WebSocket to deliver the message back. This ensures consistency — the
 *     message shown is exactly what the server processed (with sanitization,
 *     @mention resolution, etc.).
 *   - **Exposed setMessages**: The hook returns `setMessages` alongside `messages`,
 *     allowing the parent component to manipulate messages if needed (though it's
 *     not currently used in ChatPage).
 *   - **Auto-reconnect**: Same 3-second reconnect pattern as the other WS hooks.
 *
 * @module hooks/useChatRoom
 */

import { useEffect, useRef, useState } from 'react';
import { apiRequest, getHeaders, WS_BASE_URL } from '../lib/api';

/**
 * Custom hook for managing a chat room's messages with real-time WebSocket updates.
 *
 * @param {number|null} roomId - The chat room ID to connect to. If null, the hook
 *   is inactive (no API calls, no WebSocket).
 * @param {string|null} token - JWT access token. Required for both REST and WebSocket.
 * @returns {Object} Chat room state:
 *   @returns {Array} messages - Array of message objects in chronological order.
 *   @returns {Function} setMessages - State setter for direct manipulation (escape hatch).
 */
export function useChatRoom(roomId, token) {
  const [messages, setMessages] = useState([]);

  /** Ref to the active WebSocket instance (for cleanup). */
  const socketRef = useRef(null);
  /** Ref to the reconnect timer ID (for cleanup). */
  const reconnectTimerRef = useRef(null);

  /**
   * Effect 1: Load historical messages from the REST API.
   *
   * Runs when roomId or token changes. When switching rooms, the messages
   * state is cleared (either by the new fetch result or the empty-state guard).
   *
   * The `ignore` flag prevents stale responses from overwriting the state
   * if the user switches rooms before the API call completes.
   */
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

  /**
   * Effect 2: WebSocket connection for real-time message delivery.
   *
   * Connects to `/ws/chat/:roomId?token=...` and listens for `message_created`
   * events. Each new message is appended to the end of the messages array.
   *
   * The token is sent as a query parameter (not a header) because the
   * WebSocket API doesn't support custom headers in the browser.
   */
  useEffect(() => {
    if (!roomId || !token) {
      return undefined;
    }

    let closed = false;

    /**
     * Creates a WebSocket connection to the chat room channel.
     * Called on initial mount and after each reconnection.
     */
    function connect() {
      if (closed) return;
      const socket = new WebSocket(`${WS_BASE_URL}/ws/chat/${roomId}?token=${token}`);
      socketRef.current = socket;

      /**
       * Message handler: parses the JSON payload and appends new messages.
       * Only handles `message_created` events (other event types may be
       * added in the future).
       */
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event === 'message_created') {
            // Append the new message to the end of the chronological list
            setMessages((currentValue) => [...currentValue, payload.message]);
          }
        } catch {
          /* ignore malformed messages */
        }
      };

      /**
       * Auto-reconnect on close: schedules a new connection after 3 seconds.
       * The `closed` flag prevents reconnection after cleanup.
       */
      socket.onclose = () => {
        if (!closed) {
          reconnectTimerRef.current = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    /**
     * Cleanup: set the closed flag, clear the reconnect timer, and close
     * the socket. This runs when roomId/token changes or the component unmounts.
     */
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
