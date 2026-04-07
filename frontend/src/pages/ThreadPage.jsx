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

const QUICK_EMOJIS = ['\uD83D\uDC4D', '\u2764\uFE0F', '\uD83D\uDE02', '\uD83D\uDE2E', '\uD83D\uDE4F', '\uD83D\uDD25'];

/* ---- Inline vote/reaction/report helpers ---- */

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
      setVoteScore(result.vote_score);
      setUserVote(result.value);
    } catch {
      /* ignore */
    }
  }

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
      <button
        className={`vote-btn ${userVote === 1 ? 'upvoted' : ''}`}
        type="button"
        title="Upvote"
        onClick={() => handleVote(1)}
      >
        &#x25B2;
      </button>
      <button
        className="vote-score vote-score-clickable"
        type="button"
        title="View voters"
        onClick={handleShowVoters}
      >
        {voteScore}
      </button>
      <button
        className={`vote-btn ${userVote === -1 ? 'downvoted' : ''}`}
        type="button"
        title="Downvote"
        onClick={() => handleVote(-1)}
      >
        &#x25BC;
      </button>
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

function ReactionsBar({ entityType, entityId, initialReactions, token }) {
  const [reactions, setReactions] = useState(initialReactions ?? []);
  const [showPicker, setShowPicker] = useState(false);

  // Sync reactions when a WS broadcast updates the parent's thread state
  useEffect(() => {
    setReactions(initialReactions ?? []);
  }, [initialReactions]);

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
      <button
        className="thread-action-btn"
        type="button"
        title="Add reaction"
        onClick={() => setShowPicker((c) => !c)}
      >
        +&#x263A;
      </button>
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

function ReportButton({ entityType, entityId, token }) {
  const [showForm, setShowForm] = useState(false);
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');

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

/* ---- Reply component ---- */

function canModify(profile, author) {
  if (!profile) return false;
  if (profile.id === author?.id) return true;
  if (profile.role === 'admin' || profile.role === 'moderator') return true;
  return false;
}

function EditPostButton({ postId, currentBody, token, onUpdated }) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(currentBody);

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
              setBody(currentBody);
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
        {token && (
          <ReportButton entityType="post" entityId={reply.id} token={token} />
        )}
      </div>

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

/* ---- Main page ---- */

function ThreadPage() {
  const { threadId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { session, profile } = useAuth();
  const [thread, setThread] = useState(null);
  const [replyBody, setReplyBody] = useState('');
  const [replyToPostId, setReplyToPostId] = useState(null);
  const [status, setStatus] = useState('loading');
  const [replyAttachments, setReplyAttachments] = useState([]);
  const [subscribeMessage, setSubscribeMessage] = useState('');
  const [editingThread, setEditingThread] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [editBody, setEditBody] = useState('');

  const token = session?.access_token;

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

  useEffect(() => {
    fetchThread();
  }, [fetchThread]);

  // Scroll to specific post if URL has a hash like #post-123
  useEffect(() => {
    if (status !== 'ready' || !location.hash) return;
    const element = document.getElementById(location.hash.slice(1));
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      element.classList.add('reply-highlight');
      const timer = setTimeout(() => element.classList.remove('reply-highlight'), 3000);
      return () => clearTimeout(timer);
    }
  }, [status, location.hash]);

  // --- Real-time WS update helpers ---

  /** Recursively update vote_score on a post within the tree */
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

  /** Recursively update reactions on a post within the tree */
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

  const handleVoteUpdated = useCallback(
    ({ entity_type, entity_id, vote_score }) => {
      setThread((prev) => {
        if (!prev) return prev;
        if (entity_type === 'thread' && entity_id === prev.id) {
          return { ...prev, vote_score };
        }
        if (entity_type === 'post') {
          return { ...prev, posts: updatePostVoteScore(prev.posts, entity_id, vote_score) };
        }
        return prev;
      });
    },
    []
  );

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

  const wsCallbacks = useMemo(
    () => ({
      onPostCreated: fetchThread,
      onVoteUpdated: handleVoteUpdated,
      onReactionUpdated: handleReactionUpdated,
    }),
    [fetchThread, handleVoteUpdated, handleReactionUpdated]
  );

  useThreadLiveUpdates(threadId, wsCallbacks);

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
          parent_post_id: replyToPostId,
          attachment_ids: replyAttachments.map((item) => item.id),
        }),
      });
      setReplyBody('');
      setReplyToPostId(null);
      setReplyAttachments([]);
      fetchThread();
    } catch {
      /* reply failed — silently ignore */
    }
  }

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

  function startEditThread() {
    if (!thread) return;
    setEditTitle(thread.title);
    setEditBody(thread.body);
    setEditingThread(true);
  }

  async function handleThreadEditSave() {
    if (!token) return;
    try {
      await apiRequest(`/threads/${threadId}`, {
        method: 'PATCH',
        headers: getHeaders(token),
        body: JSON.stringify({ title: editTitle, body: editBody }),
      });
      setEditingThread(false);
      fetchThread();
    } catch {
      /* ignore */
    }
  }

  async function handleThreadDelete() {
    if (!token) return;
    if (!window.confirm('Are you sure you want to delete this thread? This cannot be undone.')) return;
    try {
      await apiRequest(`/threads/${threadId}`, {
        method: 'DELETE',
        headers: getHeaders(token),
      });
      navigate('/');
    } catch {
      /* ignore */
    }
  }

  return (
    <section className="page-grid thread-layout">
      {/* Thread content — full width */}
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

            {thread.tags && thread.tags.length > 0 && (
              <div className="pill-row">
                {thread.tags.map((tag) => (
                  <span key={tag.id} className="pill">{tag.name}</span>
                ))}
              </div>
            )}

            <p className="muted-copy">
              r/{thread.category.slug} &middot; {thread.reply_count} replies &middot;{' '}
              <span className="timestamp" title={thread.created_at}>{formatTimeAgo(thread.created_at)}</span>
            </p>
            <UserIdentity user={thread.author} />

            {/* Thread-level vote / reactions / report */}
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

            {/* Replies */}
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

      {/* Inline reply composer — below the thread */}
      <div className="panel stack-gap">
        <div className="thread-card-meta" style={{ justifyContent: 'space-between' }}>
          <h3>Reply</h3>
          <span className="muted-copy">
            {replyToPostId ? `Replying to #${replyToPostId}` : 'New reply'}
          </span>
        </div>

        {!token ? (
          <LoginPrompt message="Log in to join the conversation and post replies." />
        ) : (
          <form className="stack-gap" onSubmit={handleReplySubmit}>
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
