/**
 * @fileoverview ThreadPage — Thread detail view with nested comments (Reddit-style).
 *
 * This page renders a single thread with its full comment tree, and provides:
 *   1. The original post (OP) with title, body, tags, attachments, and metadata.
 *   2. Nested replies rendered recursively as a comment tree with collapse lines.
 *   3. Vote controls (upvote/downvote) and emoji reactions on both the OP and replies.
 *   4. Inline reply form with @mention support, file attachments, and Enter-to-send.
 *   5. Edit-in-place and delete for posts/threads the current user owns (or is staff).
 *   6. Report button for flagging content to moderators.
 *   7. Real-time WebSocket updates for new replies, vote changes, and reaction changes.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Recursive component rendering**: `ThreadReply` calls itself for nested replies,
 *     creating an arbitrary-depth comment tree. The `depth` prop tracks nesting level.
 *     CSS uses `border-left` on `.thread-reply` to create visual collapse lines.
 *   - **Immutable recursive state updates**: `updatePostVoteScore` and `updatePostReactions`
 *     recursively traverse the post tree, creating new objects for changed nodes while
 *     preserving references to unchanged ones (structural sharing).
 *   - **WebSocket integration**: `useThreadLiveUpdates` provides real-time updates.
 *     Vote/reaction updates are handled in-place (no re-fetch) for instant feedback,
 *     while new posts trigger a full re-fetch to correctly place nested replies.
 *   - **Optimistic vs. server-confirmed updates**: VoteControls sends the vote to the
 *     server and updates local state from the server response (server-confirmed), rather
 *     than updating optimistically before the response. This avoids UI inconsistencies
 *     if the server rejects the vote.
 *   - **Hash-based scroll**: The URL hash `#post-123` triggers a smooth scroll to a
 *     specific reply, with a temporary highlight animation — useful for notification links.
 *   - **Access control pattern**: `canModify()` checks if the user is the author or
 *     staff (admin/mod) to conditionally render edit/delete buttons.
 *
 * @module pages/ThreadPage
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useThreadLiveUpdates } from '../hooks/useThreadLiveUpdates';
import { apiRequest, API_BASE_URL, getHeaders } from '../lib/api';
import { validateFile, ATTACHMENT_ACCEPT } from '../lib/uploadUtils';
import { formatTimeAgo } from '../lib/timeUtils';
import UserIdentity from '../components/UserIdentity';
import AttachmentList from '../components/AttachmentList';
import RichText from '../components/RichText';
import MentionTextarea from '../components/MentionTextarea';
import LoginPrompt from '../components/LoginPrompt';

/** Quick emoji set for the reaction picker. */
const QUICK_EMOJIS = ['\uD83D\uDC4D', '\u2764\uFE0F', '\uD83D\uDE02', '\uD83D\uDE2E', '\uD83D\uDE4F', '\uD83D\uDD25'];

/* ──────────────────────────────────────────────────────────────
 * Inline sub-components: VoteControls, ReactionsBar, ReportButton,
 * EditPostButton, DeletePostButton, ThreadReply
 * ────────────────────────────────────────────────────────────── */

/**
 * VoteControls — renders upvote/downvote arrows and a clickable score.
 *
 * Interview note: This is a controlled component that receives its initial state
 * from props and syncs via `useEffect` when the parent re-renders with new data
 * (e.g., from a WebSocket broadcast). The vote API call updates local state from
 * the server response rather than optimistically.
 *
 * @param {Object} props
 * @param {'thread'|'post'} props.entityType - Whether this votes on a thread or post.
 * @param {number} props.entityId - The ID of the entity being voted on.
 * @param {number} props.initialScore - Server-provided vote score.
 * @param {number} props.initialUserVote - Current user's vote (-1, 0, or 1).
 * @param {string|null} props.token - JWT access token, null if not authenticated.
 * @returns {JSX.Element}
 */
function VoteControls({ entityType, entityId, initialScore, initialUserVote, token }) {
  const [voteScore, setVoteScore] = useState(initialScore ?? 0);
  const [userVote, setUserVote] = useState(initialUserVote ?? 0);
  const [showVoters, setShowVoters] = useState(false);
  const [voters, setVoters] = useState(null);
  const [votersLoading, setVotersLoading] = useState(false);
  const [showLoginPrompt, setShowLoginPrompt] = useState(false);

  // Sync score when a WS broadcast updates the parent's thread state
  useEffect(() => {
    setVoteScore(initialScore ?? 0);
  }, [initialScore]);

  /**
   * Sends a vote to the backend. Shows login prompt if unauthenticated.
   * @param {number} value - 1 for upvote, -1 for downvote.
   */
  async function handleVote(value) {
    if (!token) {
      setShowLoginPrompt(true);
      return;
    }
    try {
      const result = await apiRequest(`/${entityType}s/${entityId}/vote`, {
        method: 'POST',
        headers: getHeaders(token),
        body: JSON.stringify({ value }),
      });
      // Update from server response (server-confirmed, not optimistic)
      setVoteScore(result.vote_score);
      setUserVote(result.value);
    } catch {
      /* ignore — vote failed silently */
    }
  }

  /**
   * Toggles the voters popover and lazily loads the voter list.
   * Only fetches from API on the first open.
   */
  async function handleShowVoters() {
    if (showVoters) {
      setShowVoters(false);
      return;
    }
    setVotersLoading(true);
    setShowVoters(true);
    try {
      const data = await apiRequest(`/${entityType}s/${entityId}/voters`);
      setVoters(data);
    } catch {
      setVoters([]);
    } finally {
      setVotersLoading(false);
    }
  }

  return (
    <div className="vote-controls">
      {/* Upvote arrow — highlighted when user has upvoted */}
      <button
        className={`vote-btn ${userVote === 1 ? 'upvoted' : ''}`}
        type="button"
        title="Upvote"
        onClick={() => handleVote(1)}
      >
        &#x25B2;
      </button>
      {/* Clickable score — opens voter list popover */}
      <button
        className="vote-score vote-score-clickable"
        type="button"
        title="View voters"
        onClick={handleShowVoters}
      >
        {voteScore}
      </button>
      {/* Downvote arrow — highlighted when user has downvoted */}
      <button
        className={`vote-btn ${userVote === -1 ? 'downvoted' : ''}`}
        type="button"
        title="Downvote"
        onClick={() => handleVote(-1)}
      >
        &#x25BC;
      </button>
      {/* Voters popover — opens above the vote controls */}
      {showVoters && (
        <div className="voters-popover">
          <div className="voters-popover-header">
            <span className="voters-popover-title">Voters</span>
            <button className="voters-popover-close" type="button" onClick={() => setShowVoters(false)}>&#x2715;</button>
          </div>
          {votersLoading && <p className="muted-copy">Loading...</p>}
          {!votersLoading && voters && voters.length === 0 && (
            <p className="muted-copy">No votes yet.</p>
          )}
          {!votersLoading && voters && voters.length > 0 && (
            <ul className="voters-list">
              {voters.map((v) => (
                <li key={v.user_id} className="voters-item">
                  <span className="voters-username">{v.username}</span>
                  <span className={`voters-value ${v.value === 1 ? 'voters-up' : 'voters-down'}`}>
                    {v.value === 1 ? '\u25B2' : '\u25BC'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {/* Login prompt shown when an unauthenticated user tries to vote */}
      {showLoginPrompt && (
        <div style={{ position: 'absolute', top: '100%', left: 0, zIndex: 15, minWidth: 260 }}>
          <LoginPrompt
            message="Log in to vote on posts."
            onClose={() => setShowLoginPrompt(false)}
          />
        </div>
      )}
    </div>
  );
}

/**
 * ReactionsBar — displays emoji reaction chips and a picker for adding new reactions.
 *
 * Clicking an existing reaction chip toggles the current user's reaction.
 * The picker shows a predefined set of quick emojis (QUICK_EMOJIS).
 *
 * @param {Object} props
 * @param {'thread'|'post'} props.entityType
 * @param {number} props.entityId
 * @param {Array} props.initialReactions - Array of {emoji, count} objects.
 * @param {string|null} props.token
 * @returns {JSX.Element}
 */
function ReactionsBar({ entityType, entityId, initialReactions, token }) {
  const [reactions, setReactions] = useState(initialReactions ?? []);
  const [showPicker, setShowPicker] = useState(false);

  // Sync reactions when the parent re-renders with new data (e.g., from WebSocket)
  useEffect(() => {
    setReactions(initialReactions ?? []);
  }, [initialReactions]);

  /**
   * Toggles a reaction on this entity. The backend returns updated counts.
   * @param {string} emoji - The emoji character.
   */
  async function handleReaction(emoji) {
    if (!token) return;
    try {
      const counts = await apiRequest(`/${entityType}s/${entityId}/react`, {
        method: 'POST',
        headers: getHeaders(token),
        body: JSON.stringify({ emoji }),
      });
      setReactions(counts);
      setShowPicker(false);
    } catch {
      /* ignore */
    }
  }

  return (
    <>
      {/* Existing reaction chips — clicking toggles the user's reaction */}
      {reactions.length > 0 && (
        <div className="reactions-row">
          {reactions.map((r) => (
            <button
              key={r.emoji}
              className="reaction-chip"
              type="button"
              onClick={() => handleReaction(r.emoji)}
            >
              {r.emoji} {r.count}
            </button>
          ))}
        </div>
      )}
      {/* Button to toggle the quick emoji picker */}
      <button
        className="thread-action-btn"
        type="button"
        title="Add reaction"
        onClick={() => setShowPicker((c) => !c)}
      >
        +&#x263A;
      </button>
      {/* Quick emoji picker row */}
      {showPicker && (
        <div className="emoji-picker-row">
          {QUICK_EMOJIS.map((emoji) => (
            <button
              key={emoji}
              className="emoji-pick-btn"
              type="button"
              onClick={() => handleReaction(emoji)}
            >
              {emoji}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

/**
 * ReportButton — inline form for reporting a thread or post to moderators.
 *
 * @param {Object} props
 * @param {'thread'|'post'} props.entityType
 * @param {number} props.entityId
 * @param {string} props.token - JWT token (required).
 * @returns {JSX.Element}
 */
function ReportButton({ entityType, entityId, token }) {
  const [showForm, setShowForm] = useState(false);
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');

  /** Submits the report to the backend. */
  async function handleSubmit(event) {
    event.preventDefault();
    if (!token || !reason.trim()) return;
    try {
      await apiRequest(`/${entityType}s/${entityId}/report`, {
        method: 'POST',
        headers: getHeaders(token),
        body: JSON.stringify({ reason }),
      });
      setMessage('Reported. Thank you.');
      setReason('');
      setShowForm(false);
    } catch (error) {
      setMessage(error.message);
    }
  }

  return (
    <>
      <button
        className="thread-action-btn"
        type="button"
        title="Report"
        onClick={() => setShowForm((c) => !c)}
      >
        &#x26A0; Report
      </button>
      {showForm && (
        <form className="report-form-inline" onSubmit={handleSubmit}>
          <input
            className="input"
            placeholder="Reason for report..."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button className="secondary-button" type="submit">
            Submit
          </button>
        </form>
      )}
      {message && <p className="muted-copy">{message}</p>}
    </>
  );
}

/* ──────────────────────────────────────────────────────────────
 * Access control helper
 * ────────────────────────────────────────────────────────────── */

/**
 * Determines whether the current user can edit/delete a given post or thread.
 * Returns true if the user is the author, an admin, or a moderator.
 *
 * Interview note: This is a presentation-layer guard. The backend also enforces
 * these permissions — the frontend check just hides buttons the user can't use.
 *
 * @param {Object|null} profile - Current user's profile.
 * @param {Object|null} author - The content author.
 * @returns {boolean}
 */
function canModify(profile, author) {
  if (!profile) return false;
  if (profile.id === author?.id) return true;
  if (profile.role === 'admin' || profile.role === 'moderator') return true;
  return false;
}

/**
 * EditPostButton — toggles between a "Edit" button and an inline editor.
 *
 * Keyboard shortcuts:
 *   - Ctrl/Cmd+Enter: Save the edit.
 *   - Escape: Cancel editing and restore original text.
 *
 * @param {Object} props
 * @param {number} props.postId
 * @param {string} props.currentBody - The post's current text (for cancel/restore).
 * @param {string} props.token
 * @param {Function} props.onUpdated - Callback to refresh the thread after editing.
 * @returns {JSX.Element}
 */
function EditPostButton({ postId, currentBody, token, onUpdated }) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(currentBody);

  /** Saves the edited post body to the backend. */
  async function handleSave() {
    if (!token || !body.trim()) return;
    try {
      await apiRequest(`/posts/${postId}`, {
        method: 'PATCH',
        headers: getHeaders(token),
        body: JSON.stringify({ body }),
      });
      setEditing(false);
      if (onUpdated) onUpdated();
    } catch {
      /* ignore */
    }
  }

  if (editing) {
    return (
      <div className="edit-inline">
        <textarea
          className="input"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              handleSave();
            } else if (e.key === 'Escape') {
              setEditing(false);
              setBody(currentBody); // Restore original text on cancel
            }
          }}
        />
        <div className="edit-inline-actions">
          <button className="action-button" type="button" onClick={handleSave}>
            Save <span className="kbd-hint">Ctrl+Enter</span>
          </button>
          <button className="secondary-button" type="button" onClick={() => { setEditing(false); setBody(currentBody); }}>
            Cancel <span className="kbd-hint">Esc</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <button className="thread-action-btn" type="button" onClick={() => setEditing(true)}>
      Edit
    </button>
  );
}

/**
 * DeletePostButton — two-step delete with confirmation.
 *
 * First click shows "Confirm delete" / "Cancel"; second click actually deletes.
 * This prevents accidental deletion (a common UX pattern).
 *
 * @param {Object} props
 * @param {number} props.postId
 * @param {string} props.token
 * @param {Function} props.onDeleted - Callback to refresh the thread after deletion.
 * @returns {JSX.Element}
 */
function DeletePostButton({ postId, token, onDeleted }) {
  const [confirming, setConfirming] = useState(false);

  async function handleDelete() {
    if (!token) return;
    try {
      await apiRequest(`/posts/${postId}`, {
        method: 'DELETE',
        headers: getHeaders(token),
      });
      if (onDeleted) onDeleted();
    } catch {
      /* ignore */
    }
  }

  if (confirming) {
    return (
      <span className="edit-inline-actions">
        <button className="action-link action-link-danger" type="button" onClick={handleDelete}>
          Confirm delete
        </button>
        <button className="thread-action-btn" type="button" onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </span>
    );
  }

  return (
    <button className="thread-action-btn" type="button" onClick={() => setConfirming(true)}>
      Delete
    </button>
  );
}

/**
 * ThreadReply — a single reply in the nested comment tree.
 *
 * This component is **recursive**: it renders its own `reply.replies` children
 * as nested `<ThreadReply>` components. This creates Reddit-style threaded comments.
 *
 * Each reply includes:
 *   - Author identity, timestamp, and rich text body
 *   - Vote controls and reactions
 *   - Reply, Edit, Delete, and Report action buttons
 *   - Attachments (images, files)
 *
 * CSS uses `border-left` and `padding-left` on `.thread-reply` to create the
 * visual collapse/nesting lines.
 *
 * @param {Object} props
 * @param {Object} props.reply - The reply/post object with nested `replies` array.
 * @param {number} [props.depth=0] - Current nesting depth (for styling purposes).
 * @param {Function} props.onReplySelect - Callback to set the reply target post ID.
 * @param {string|null} props.token - JWT token.
 * @param {Object|null} props.profile - Current user profile.
 * @param {Function} props.onRefresh - Callback to re-fetch the entire thread.
 * @returns {JSX.Element}
 */
function ThreadReply({ reply, depth = 0, onReplySelect, token, profile, onRefresh }) {
  return (
    <div
      id={`post-${reply.id}`}
      className="thread-reply"
    >
      <div className="thread-card-meta">
        <UserIdentity user={reply.author} compact />
        <span className="timestamp" title={reply.created_at}>{formatTimeAgo(reply.created_at)}</span>
      </div>
      <RichText text={reply.body} />
      <AttachmentList attachments={reply.attachments} />

      {/* Action bar: vote, react, reply, edit, delete, report */}
      <div className="thread-card-actions">
        <VoteControls
          entityType="post"
          entityId={reply.id}
          initialScore={reply.vote_score}
          initialUserVote={reply.user_vote}
          token={token}
        />
        <ReactionsBar
          entityType="post"
          entityId={reply.id}
          initialReactions={reply.reactions}
          token={token}
        />
        <button
          className="thread-action-btn"
          type="button"
          onClick={() => onReplySelect(reply.id)}
        >
          Reply
        </button>
        {/* Edit/Delete only shown if user has permission */}
        {canModify(profile, reply.author) && (
          <>
            <EditPostButton
              postId={reply.id}
              currentBody={reply.body}
              token={token}
              onUpdated={onRefresh}
            />
            <DeletePostButton
              postId={reply.id}
              token={token}
              onDeleted={onRefresh}
            />
          </>
        )}
        {/* Report button only for authenticated users */}
        {token && (
          <ReportButton entityType="post" entityId={reply.id} token={token} />
        )}
      </div>

      {/* Recursive rendering of child replies — this creates the nested tree */}
      {reply.replies?.map((childReply) => (
        <ThreadReply
          key={childReply.id}
          reply={childReply}
          depth={depth + 1}
          onReplySelect={onReplySelect}
          token={token}
          profile={profile}
          onRefresh={onRefresh}
        />
      ))}
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────
 * Main ThreadPage component
 * ────────────────────────────────────────────────────────────── */

/**
 * ThreadPage — the main page component for viewing a single thread.
 *
 * Uses `useParams` to extract `threadId` from the URL path `/threads/:threadId`.
 * Fetches the thread data on mount and subscribes to real-time WebSocket updates.
 *
 * @returns {JSX.Element}
 */
function ThreadPage() {
  /** Extract the thread ID from the URL: /threads/:threadId */
  const { threadId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { session, profile } = useAuth();

  // ── Component state ──
  const [thread, setThread] = useState(null);          // The full thread object with nested posts
  const [replyBody, setReplyBody] = useState('');       // Text content of the reply being composed
  const [replyToPostId, setReplyToPostId] = useState(null); // If set, the reply is nested under this post
  const [status, setStatus] = useState('loading');      // 'loading' | 'ready' | 'error'
  const [replyAttachments, setReplyAttachments] = useState([]); // Uploaded files for the reply
  const [subscribeMessage, setSubscribeMessage] = useState('');
  const [editingThread, setEditingThread] = useState(false); // Whether the thread title/body is being edited
  const [editTitle, setEditTitle] = useState('');
  const [editBody, setEditBody] = useState('');

  /** Convenience: extract the token so we don't repeat `session?.access_token` everywhere. */
  const token = session?.access_token;

  /**
   * Fetches the thread from the API. Wrapped in useCallback so it can be
   * passed as a stable dependency to the WebSocket hook (avoids reconnects).
   */
  const fetchThread = useCallback(async () => {
    try {
      setStatus('loading');
      const data = await apiRequest(`/threads/${threadId}`);
      setThread(data);
      setStatus('ready');
    } catch {
      setStatus('error');
    }
  }, [threadId]);

  /** Fetch the thread on mount and whenever threadId changes. */
  useEffect(() => {
    fetchThread();
  }, [fetchThread]);

  /**
   * Scroll-to-post on hash navigation.
   *
   * When the URL contains a hash like `#post-123`, this effect finds the
   * corresponding DOM element and scrolls to it with a highlight animation.
   * This is used when clicking a notification link that points to a specific reply.
   *
   * Dependencies: only runs when the thread finishes loading or the hash changes.
   */
  useEffect(() => {
    if (status !== 'ready' || !location.hash) return;
    const element = document.getElementById(location.hash.slice(1));
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      element.classList.add('reply-highlight'); // CSS animation for temporary highlight
      const timer = setTimeout(() => element.classList.remove('reply-highlight'), 3000);
      return () => clearTimeout(timer);
    }
  }, [status, location.hash]);

  // ── Real-time WebSocket update helpers ──

  /**
   * Recursively updates the `vote_score` of a specific post within the nested tree.
   *
   * Interview note: This is an immutable update — we create new objects for every
   * node on the path to the target, but reuse unchanged sub-trees (structural sharing).
   * This is essential for React's reconciliation: only changed nodes will re-render.
   *
   * @param {Array} posts - Array of post objects (each may have a `replies` array).
   * @param {number} entityId - The post ID to update.
   * @param {number} newScore - The new vote score.
   * @returns {Array} A new array with the updated post.
   */
  function updatePostVoteScore(posts, entityId, newScore) {
    return posts.map((post) => {
      const updated =
        post.id === entityId ? { ...post, vote_score: newScore } : post;
      if (updated.replies?.length) {
        return {
          ...updated,
          replies: updatePostVoteScore(updated.replies, entityId, newScore),
        };
      }
      return updated;
    });
  }

  /**
   * Recursively updates the `reactions` array of a specific post within the nested tree.
   * Same structural sharing pattern as updatePostVoteScore.
   *
   * @param {Array} posts
   * @param {number} entityId
   * @param {Array} newReactions
   * @returns {Array}
   */
  function updatePostReactions(posts, entityId, newReactions) {
    return posts.map((post) => {
      const updated =
        post.id === entityId ? { ...post, reactions: newReactions } : post;
      if (updated.replies?.length) {
        return {
          ...updated,
          replies: updatePostReactions(updated.replies, entityId, newReactions),
        };
      }
      return updated;
    });
  }

  /**
   * WebSocket callback: handles real-time vote score changes.
   * Updates thread-level or post-level vote scores without a full re-fetch.
   */
  const handleVoteUpdated = useCallback(
    ({ entity_type, entity_id, vote_score }) => {
      setThread((prev) => {
        if (!prev) return prev;
        // Thread-level vote update
        if (entity_type === 'thread' && entity_id === prev.id) {
          return { ...prev, vote_score };
        }
        // Post-level vote update — recursive tree traversal
        if (entity_type === 'post') {
          return { ...prev, posts: updatePostVoteScore(prev.posts, entity_id, vote_score) };
        }
        return prev;
      });
    },
    []
  );

  /**
   * WebSocket callback: handles real-time reaction changes.
   * Same pattern as handleVoteUpdated but for reactions.
   */
  const handleReactionUpdated = useCallback(
    ({ entity_type, entity_id, reactions }) => {
      setThread((prev) => {
        if (!prev) return prev;
        if (entity_type === 'thread' && entity_id === prev.id) {
          return { ...prev, reactions };
        }
        if (entity_type === 'post') {
          return { ...prev, posts: updatePostReactions(prev.posts, entity_id, reactions) };
        }
        return prev;
      });
    },
    []
  );

  /**
   * Memoized callbacks object passed to useThreadLiveUpdates.
   * Using useMemo ensures the WebSocket hook doesn't reconnect on every render.
   */
  const wsCallbacks = useMemo(
    () => ({
      onPostCreated: fetchThread,         // New reply → full re-fetch (correct nesting)
      onVoteUpdated: handleVoteUpdated,    // Vote change → in-place update
      onReactionUpdated: handleReactionUpdated, // Reaction change → in-place update
    }),
    [fetchThread, handleVoteUpdated, handleReactionUpdated]
  );

  /** Subscribe to real-time thread updates via WebSocket. */
  useThreadLiveUpdates(threadId, wsCallbacks);

  /**
   * Handles file upload for reply attachments.
   * Same "draft" upload pattern as HomePage — the file is uploaded first,
   * and its ID is sent with the reply when submitted.
   *
   * @param {Event} event - The file input change event.
   */
  async function handleReplyAttachmentUpload(event) {
    if (!token || !event.target.files?.[0]) {
      return;
    }

    const file = event.target.files[0];
    const { valid, error } = validateFile(file);
    if (!valid) {
      alert(error);
      event.target.value = '';
      return;
    }

    try {
      const formData = new FormData();
      formData.append('linked_entity_type', 'draft');
      formData.append('linked_entity_id', '0');
      formData.append('file', file);

      const response = await fetch(`${API_BASE_URL}/uploads`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      if (response.ok) {
        const data = await response.json();
        setReplyAttachments((c) => [...c, data]);
      } else {
        const err = await response.json().catch(() => ({}));
        alert(err.detail || 'Upload failed.');
      }
    } catch {
      /* upload failed — silently ignore */
    }
    event.target.value = '';
  }

  /**
   * Handles reply submission.
   * If `replyToPostId` is set, the reply becomes a nested child of that post.
   * After success, clears the form and re-fetches the thread.
   *
   * @param {Event} event - The form submit event.
   */
  async function handleReplySubmit(event) {
    event.preventDefault();
    if (!token || !replyBody.trim()) {
      return;
    }

    try {
      await apiRequest(`/threads/${threadId}/posts`, {
        method: 'POST',
        headers: getHeaders(token),
        body: JSON.stringify({
          body: replyBody,
          parent_post_id: replyToPostId, // null = top-level reply, number = nested
          attachment_ids: replyAttachments.map((item) => item.id),
        }),
      });
      // Reset form state after successful submission
      setReplyBody('');
      setReplyToPostId(null);
      setReplyAttachments([]);
      fetchThread(); // Re-fetch to show the new reply in the correct position
    } catch {
      /* reply failed — silently ignore */
    }
  }

  /** Subscribes to email/push notifications for activity on this thread. */
  async function handleSubscribe() {
    if (!token) {
      return;
    }

    try {
      await apiRequest(`/threads/${threadId}/subscribe`, {
        method: 'POST',
        headers: getHeaders(token),
      });
      setSubscribeMessage('Subscribed. You will receive activity notifications.');
    } catch {
      setSubscribeMessage('Failed to subscribe.');
    }
  }

  /** Enters thread edit mode, pre-populating fields with current values. */
  function startEditThread() {
    if (!thread) return;
    setEditTitle(thread.title);
    setEditBody(thread.body);
    setEditingThread(true);
  }

  /** Saves thread title/body edits to the backend. */
  async function handleThreadEditSave() {
    if (!token) return;
    try {
      await apiRequest(`/threads/${threadId}`, {
        method: 'PATCH',
        headers: getHeaders(token),
        body: JSON.stringify({ title: editTitle, body: editBody }),
      });
      setEditingThread(false);
      fetchThread(); // Re-fetch to show updated content
    } catch {
      /* ignore */
    }
  }

  /** Deletes the entire thread after a browser confirmation dialog. */
  async function handleThreadDelete() {
    if (!token) return;
    if (!window.confirm('Are you sure you want to delete this thread? This cannot be undone.')) return;
    try {
      await apiRequest(`/threads/${threadId}`, {
        method: 'DELETE',
        headers: getHeaders(token),
      });
      navigate('/'); // Redirect to home after deletion
    } catch {
      /* ignore */
    }
  }

  return (
    <section className="page-grid thread-layout">
      {/* Thread content — full width article */}
      <article className="panel stack-gap">
        <p className="eyebrow">Thread #{threadId}</p>

        {status === 'loading' && (
          <p className="muted-copy">Loading thread...</p>
        )}
        {status === 'error' && (
          <p className="error-copy">Could not load this thread.</p>
        )}

        {thread && (
          <>
            {/* Thread title/body — either in edit mode or display mode */}
            {editingThread ? (
              <div className="stack-gap">
                <input
                  className="input"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  placeholder="Thread title"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                      e.preventDefault();
                      handleThreadEditSave();
                    } else if (e.key === 'Escape') {
                      setEditingThread(false);
                    }
                  }}
                />
                <textarea
                  className="input"
                  value={editBody}
                  onChange={(e) => setEditBody(e.target.value)}
                  placeholder="Thread body"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                      e.preventDefault();
                      handleThreadEditSave();
                    } else if (e.key === 'Escape') {
                      setEditingThread(false);
                    }
                  }}
                />
                <div className="edit-inline-actions">
                  <button className="action-button" type="button" onClick={handleThreadEditSave}>
                    Save <span className="kbd-hint">Ctrl+Enter</span>
                  </button>
                  <button className="secondary-button" type="button" onClick={() => setEditingThread(false)}>
                    Cancel <span className="kbd-hint">Esc</span>
                  </button>
                </div>
              </div>
            ) : (
              <>
                <h3>{thread.title}</h3>
                {/* Pinned/Locked badges */}
                {(thread.is_pinned || thread.is_locked) && (
                  <div className="edit-inline-actions">
                    {thread.is_pinned && <span className="thread-pill">Pinned</span>}
                    {thread.is_locked && (
                      <span className="thread-pill thread-pill-muted">Locked</span>
                    )}
                  </div>
                )}
                <RichText text={thread.body} />
              </>
            )}
            <AttachmentList attachments={thread.attachments} />

            {/* Tags rendered as pill chips */}
            {thread.tags && thread.tags.length > 0 && (
              <div className="pill-row">
                {thread.tags.map((tag) => (
                  <span key={tag.id} className="pill">{tag.name}</span>
                ))}
              </div>
            )}

            {/* Thread metadata: community, reply count, timestamp */}
            <p className="muted-copy">
              r/{thread.category.slug} &middot; {thread.reply_count} replies &middot;{' '}
              <span className="timestamp" title={thread.created_at}>{formatTimeAgo(thread.created_at)}</span>
            </p>
            <UserIdentity user={thread.author} />

            {/* Thread-level vote controls and reactions */}
            <div className="thread-card-actions">
              <VoteControls
                entityType="thread"
                entityId={thread.id}
                initialScore={thread.vote_score}
                initialUserVote={thread.user_vote}
                token={token}
              />
              <ReactionsBar
                entityType="thread"
                entityId={thread.id}
                initialReactions={thread.reactions}
                token={token}
              />
            </div>

            {/* Thread action buttons: subscribe, edit, delete, report */}
            <div className="edit-inline-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={handleSubscribe}
              >
                Subscribe to thread
              </button>
              {canModify(profile, thread.author) && !editingThread && (
                <>
                  <button className="secondary-button" type="button" onClick={startEditThread}>
                    Edit thread
                  </button>
                  <button className="secondary-button action-link-danger" type="button" onClick={handleThreadDelete}>
                    Delete thread
                  </button>
                </>
              )}
              {token && (
                <ReportButton entityType="thread" entityId={thread.id} token={token} />
              )}
            </div>
            {subscribeMessage && <p className="success-copy">{subscribeMessage}</p>}

            {/* ── Nested reply tree ── */}
            {thread.posts.map((reply) => (
              <ThreadReply
                key={reply.id}
                reply={reply}
                onReplySelect={setReplyToPostId}
                token={token}
                profile={profile}
                onRefresh={fetchThread}
              />
            ))}
          </>
        )}
      </article>

      {/* ── Reply Composer (below the thread) ── */}
      <div className="panel stack-gap">
        <div className="thread-card-meta" style={{ justifyContent: 'space-between' }}>
          <h3>Reply</h3>
          <span className="muted-copy">
            {replyToPostId ? `Replying to #${replyToPostId}` : 'New reply'}
          </span>
        </div>

        {/* Show login prompt for unauthenticated users */}
        {!token ? (
          <LoginPrompt message="Log in to join the conversation and post replies." />
        ) : (
          <form className="stack-gap" onSubmit={handleReplySubmit}>
            {/*
              MentionTextarea with Enter-to-send (quick-reply pattern).
              Shift+Enter inserts a newline instead of submitting.
            */}
            <MentionTextarea
              className="input"
              placeholder="Write a reply. Type @ to mention users, @pulse for AI help. Enter to send, Shift+Enter for new line."
              value={replyBody}
              onChange={setReplyBody}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleReplySubmit(e);
                }
              }}
              token={token}
            />
            <div className="edit-inline-actions">
              <label className="secondary-button" style={{ cursor: 'pointer' }}>
                Attach file
                <input
                  type="file"
                  accept={ATTACHMENT_ACCEPT}
                  hidden
                  onChange={handleReplyAttachmentUpload}
                />
              </label>
              <button
                className="action-button"
                type="submit"
              >
                Post reply <span className="kbd-hint">Enter</span>
              </button>
              {/* Cancel nested reply — resets to top-level reply */}
              {replyToPostId && (
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setReplyToPostId(null)}
                >
                  Cancel
                </button>
              )}
            </div>
            <AttachmentList attachments={replyAttachments} />
          </form>
        )}
      </div>
    </section>
  );
}

export default ThreadPage;
