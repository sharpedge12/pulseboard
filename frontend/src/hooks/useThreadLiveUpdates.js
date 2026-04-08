/**
 * @fileoverview useThreadLiveUpdates — WebSocket hook for real-time thread updates.
 *
 * This hook subscribes to the `/ws/threads/:threadId` WebSocket endpoint and
 * dispatches callbacks when events occur on the thread:
 *   - `post_created`: A new reply was added to the thread.
 *   - `vote_updated`: A vote score changed on the thread or one of its posts.
 *   - `reaction_updated`: Reaction counts changed on the thread or a post.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Event-driven architecture**: The backend publishes thread events to Redis
 *     pub/sub. The Gateway's Redis-to-WebSocket bridge picks up these events and
 *     broadcasts them to all WebSocket clients subscribed to `thread:{id}`.
 *     This hook is the frontend consumer of that pipeline.
 *   - **Callback-based API**: The hook accepts a `callbacks` object with named
 *     handlers. This decouples the WebSocket plumbing from the business logic.
 *     The parent component decides what to do with each event type.
 *   - **Auto-reconnect with cleanup**: Same pattern as useNotifications — the
 *     `onclose` handler schedules a reconnect after 3 seconds, and the cleanup
 *     function sets a `closed` flag to prevent reconnection after unmount.
 *   - **Stable callbacks requirement**: The hook lists `callbacks` as a dependency
 *     of the useEffect. The parent component (ThreadPage) wraps callbacks in
 *     `useMemo` to ensure referential stability and prevent unnecessary reconnections.
 *   - **Selective update strategy**: `vote_updated` and `reaction_updated` are
 *     handled in-place (the parent uses recursive state updaters). `post_created`
 *     triggers a full re-fetch because inserting a new post at the correct nesting
 *     level is complex and error-prone with local state manipulation.
 *
 * @module hooks/useThreadLiveUpdates
 */

import { useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../lib/api';

/**
 * Subscribe to real-time thread updates via WebSocket.
 *
 * Handles three event types broadcast on `thread:{id}` channels:
 * - post_created   -> new reply added
 * - vote_updated   -> vote score changed on a thread or post
 * - reaction_updated -> reaction counts changed on a thread or post
 *
 * @param {number|string} threadId - The thread to subscribe to. If falsy, no
 *   connection is made (hook is inactive).
 * @param {Object} callbacks - Event handler functions:
 * @param {Function} [callbacks.onPostCreated] - Called when a new post is created.
 *   Receives the new post object as argument.
 * @param {Function} [callbacks.onVoteUpdated] - Called when a vote score changes.
 *   Receives { entity_type, entity_id, vote_score }.
 * @param {Function} [callbacks.onReactionUpdated] - Called when reactions change.
 *   Receives { entity_type, entity_id, reactions }.
 */
export function useThreadLiveUpdates(threadId, callbacks) {
  /** Ref to the active WebSocket instance. */
  const socketRef = useRef(null);
  /** Ref to the reconnect timer ID. */
  const reconnectTimerRef = useRef(null);

  /**
   * Main effect: establishes and manages the WebSocket connection.
   *
   * Dependencies: threadId and callbacks.
   * - When threadId changes (user navigates to a different thread), the old
   *   socket is closed and a new one is opened.
   * - callbacks should be memoized by the parent to avoid unnecessary reconnections.
   */
  useEffect(() => {
    if (!threadId) {
      return undefined; // No thread ID — don't connect
    }

    let closed = false;

    /**
     * Creates a WebSocket connection and sets up event handlers.
     * Called on initial mount and after each reconnection.
     */
    function connect() {
      if (closed) return;
      const socket = new WebSocket(`${WS_BASE_URL}/ws/threads/${threadId}`);
      socketRef.current = socket;

      /**
       * Message handler: parses the JSON payload and dispatches to the
       * appropriate callback based on the `event` field.
       */
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);

          switch (payload.event) {
            case 'post_created':
              // A new reply was added — delegate to parent for re-fetch
              if (callbacks.onPostCreated) {
                callbacks.onPostCreated(payload.post);
              }
              break;
            case 'vote_updated':
              // Vote score changed — delegate for in-place state update
              if (callbacks.onVoteUpdated) {
                callbacks.onVoteUpdated({
                  entity_type: payload.entity_type,
                  entity_id: payload.entity_id,
                  vote_score: payload.vote_score,
                });
              }
              break;
            case 'reaction_updated':
              // Reactions changed — delegate for in-place state update
              if (callbacks.onReactionUpdated) {
                callbacks.onReactionUpdated({
                  entity_type: payload.entity_type,
                  entity_id: payload.entity_id,
                  reactions: payload.reactions,
                });
              }
              break;
            default:
              break; // Unknown event type — ignore
          }
        } catch {
          /* ignore malformed messages */
        }
      };

      /**
       * Auto-reconnect on close: schedules a new connection attempt after 3s.
       * The `closed` flag prevents reconnection after the cleanup function runs.
       */
      socket.onclose = () => {
        if (!closed) {
          reconnectTimerRef.current = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    /**
     * Cleanup function: prevents reconnection, clears the timer, and closes
     * the socket. Runs when the component unmounts or dependencies change.
     */
    return () => {
      closed = true;
      clearTimeout(reconnectTimerRef.current);
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [threadId, callbacks]);
}
