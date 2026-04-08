/**
 * @fileoverview useGlobalUpdates — WebSocket hook for app-wide real-time events.
 *
 * This hook connects to the `/ws/global` WebSocket endpoint, which broadcasts
 * events that affect the entire application (not scoped to a specific thread
 * or chat room). Currently handles:
 *   - `category_created`: A new community was created by an admin.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **useRef for latest handlers**: The `handlersRef` pattern stores the latest
 *     handlers object in a ref that is updated on every render. This solves the
 *     stale closure problem: the WebSocket's `onmessage` callback was created
 *     when the effect ran, but it always reads the *current* handlers through
 *     the ref. Without this, the callback would capture the initial handlers
 *     and never see updates.
 *   - **Empty dependency array**: The `useEffect` has `[]` as dependencies,
 *     meaning the WebSocket connection is established once and never re-created.
 *     This is intentional — global events don't depend on any props or state.
 *     The `handlersRef` pattern allows the handlers to change without reconnecting.
 *   - **Global vs. scoped WebSockets**: The app has 4 WebSocket channels:
 *       1. `/ws/global` (this hook) — app-wide events, no auth required.
 *       2. `/ws/threads/:id` (useThreadLiveUpdates) — thread-specific events.
 *       3. `/ws/chat/:id` (useChatRoom) — room-specific messages.
 *       4. `/ws/notifications` (useNotifications) — user-specific, auth required.
 *     This separation keeps each channel focused and reduces unnecessary traffic.
 *   - **Extensibility**: New global event types can be added by:
 *       1. Adding a new handler to the `handlers` object type.
 *       2. Adding a new case in the `onmessage` handler.
 *     The ref pattern means no WebSocket reconnection is needed.
 *
 * @module hooks/useGlobalUpdates
 */

import { useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../lib/api';

/**
 * Connects to the /ws/global WebSocket for app-wide real-time events.
 * Calls the provided callbacks when relevant events arrive.
 *
 * @param {Object} handlers - Event handler callbacks:
 * @param {Function} [handlers.onCategoryCreated] - Called when a new community
 *   is created. Receives the category object as argument.
 *
 * @example
 * useGlobalUpdates({
 *   onCategoryCreated: (category) => {
 *     setCategories(prev => [...prev, category]);
 *   },
 * });
 */
export function useGlobalUpdates(handlers) {
  /**
   * Ref to always hold the latest handlers.
   *
   * Interview note: This is the "ref callback" pattern for solving stale closures
   * in WebSocket/event handlers. The ref is updated on every render (line below),
   * and the WebSocket onmessage reads from the ref, so it always has access to
   * the latest handlers without needing to reconnect the socket.
   */
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers; // Update ref on every render

  /** Ref to the active WebSocket instance. */
  const socketRef = useRef(null);
  /** Ref to the reconnect timer ID. */
  const reconnectTimerRef = useRef(null);

  /**
   * Single effect that manages the global WebSocket connection.
   *
   * Empty dependency array `[]` — the connection is established once on mount
   * and cleaned up on unmount. The ref pattern handles handler updates.
   */
  useEffect(() => {
    let closed = false;

    /**
     * Creates a WebSocket connection to the global channel.
     * No authentication required — global events are public.
     */
    function connect() {
      if (closed) return;
      const socket = new WebSocket(`${WS_BASE_URL}/ws/global`);
      socketRef.current = socket;

      /**
       * Message handler: dispatches events to the appropriate callback.
       * Reads handlers from the ref (not the closure) to avoid stale references.
       */
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event === 'category_created' && handlersRef.current.onCategoryCreated) {
            handlersRef.current.onCategoryCreated(payload.category);
          }
          // Future global events can be added here:
          // if (payload.event === 'announcement' && handlersRef.current.onAnnouncement) { ... }
        } catch {
          /* ignore malformed messages */
        }
      };

      /** Auto-reconnect after 3 seconds on close. */
      socket.onclose = () => {
        if (!closed) {
          reconnectTimerRef.current = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    /** Cleanup: prevent reconnection, clear timer, close socket. */
    return () => {
      closed = true;
      clearTimeout(reconnectTimerRef.current);
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, []); // Empty deps — connect once, use ref for handler updates
}
