import { useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../lib/api';

/**
 * Subscribe to real-time thread updates via WebSocket.
 *
 * Handles three event types broadcast on `thread:{id}` channels:
 * - post_created   → new reply added
 * - vote_updated   → vote score changed on a thread or post
 * - reaction_updated → reaction counts changed on a thread or post
 *
 * @param {number|string} threadId
 * @param {object} callbacks
 * @param {function} callbacks.onPostCreated  - called with the new post object
 * @param {function} callbacks.onVoteUpdated  - called with { entity_type, entity_id, vote_score }
 * @param {function} callbacks.onReactionUpdated - called with { entity_type, entity_id, reactions }
 */
export function useThreadLiveUpdates(threadId, callbacks) {
  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);

  useEffect(() => {
    if (!threadId) {
      return undefined;
    }

    let closed = false;

    function connect() {
      if (closed) return;
      const socket = new WebSocket(`${WS_BASE_URL}/ws/threads/${threadId}`);
      socketRef.current = socket;

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);

          switch (payload.event) {
            case 'post_created':
              if (callbacks.onPostCreated) {
                callbacks.onPostCreated(payload.post);
              }
              break;
            case 'vote_updated':
              if (callbacks.onVoteUpdated) {
                callbacks.onVoteUpdated({
                  entity_type: payload.entity_type,
                  entity_id: payload.entity_id,
                  vote_score: payload.vote_score,
                });
              }
              break;
            case 'reaction_updated':
              if (callbacks.onReactionUpdated) {
                callbacks.onReactionUpdated({
                  entity_type: payload.entity_type,
                  entity_id: payload.entity_id,
                  reactions: payload.reactions,
                });
              }
              break;
            default:
              break;
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
  }, [threadId, callbacks]);
}
