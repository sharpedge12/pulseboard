import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';
import { formatTimeAgo } from '../lib/timeUtils';
import UserIdentity from './UserIdentity';
import AttachmentList from './AttachmentList';
import LoginPrompt from './LoginPrompt';

const QUICK_EMOJIS = ['\uD83D\uDC4D', '\u2764\uFE0F', '\uD83D\uDE02', '\uD83D\uDE2E', '\uD83D\uDE4F', '\uD83D\uDD25'];

function ThreadCard({ thread }) {
  const navigate = useNavigate();
  const { session } = useAuth();
  const [voteScore, setVoteScore] = useState(thread.vote_score ?? 0);
  const [userVote, setUserVote] = useState(thread.user_vote ?? 0);
  const [reactions, setReactions] = useState(thread.reactions ?? []);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [showReportForm, setShowReportForm] = useState(false);
  const [reportReason, setReportReason] = useState('');
  const [reportMessage, setReportMessage] = useState('');
  const [showLoginPrompt, setShowLoginPrompt] = useState(false);

  async function handleVote(event, value) {
    event.stopPropagation();
    if (!session?.access_token) {
      setShowLoginPrompt(true);
      return;
    }

    try {
      const result = await apiRequest(`/threads/${thread.id}/vote`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ value }),
      });
      setVoteScore(result.vote_score);
      setUserVote(result.value);
    } catch {
      /* ignore */
    }
  }

  async function handleReaction(event, emoji) {
    event.stopPropagation();
    if (!session?.access_token) {
      setShowLoginPrompt(true);
      return;
    }

    try {
      const counts = await apiRequest(`/threads/${thread.id}/react`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ emoji }),
      });
      setReactions(counts);
      setShowEmojiPicker(false);
    } catch {
      /* ignore */
    }
  }

  async function handleReport(event) {
    event.stopPropagation();
    event.preventDefault();
    if (!session?.access_token || !reportReason.trim()) return;

    try {
      await apiRequest(`/threads/${thread.id}/report`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ reason: reportReason }),
      });
      setReportMessage('Reported. Thank you.');
      setReportReason('');
      setShowReportForm(false);
    } catch (error) {
      setReportMessage(error.message);
    }
  }

  return (
    <div
      className="thread-card thread-card-clickable"
      role="button"
      tabIndex={0}
      onClick={() => navigate(`/threads/${thread.id}`)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          navigate(`/threads/${thread.id}`);
        }
      }}
    >
      {/* Vote column */}
      <div className="vote-column" onClick={(e) => e.stopPropagation()}>
        <button
          className={`vote-btn ${userVote === 1 ? 'upvoted' : ''}`}
          type="button"
          title="Upvote"
          onClick={(e) => handleVote(e, 1)}
        >
          &#x25B2;
        </button>
        <span className={`vote-score ${voteScore > 0 ? 'positive' : voteScore < 0 ? 'negative' : ''}`}>{voteScore}</span>
        <button
          className={`vote-btn ${userVote === -1 ? 'downvoted' : ''}`}
          type="button"
          title="Downvote"
          onClick={(e) => handleVote(e, -1)}
        >
          &#x25BC;
        </button>
      </div>

      {/* Main content */}
      <div className="thread-card-body">
        <div>
          <p className="thread-card-community">r/{thread.category.slug}</p>
          <div className="thread-card-title">
            {thread.title}
            {thread.is_pinned && <span className="thread-pill">Pinned</span>}
            {thread.is_locked && (
              <span className="thread-pill thread-pill-muted">Locked</span>
            )}
          </div>
          <p className="thread-card-preview">{thread.body.slice(0, 140)}</p>
          <AttachmentList attachments={thread.attachments} />
          {thread.tags && thread.tags.length > 0 && (
            <div className="pill-row" onClick={(e) => e.stopPropagation()}>
              {thread.tags.map((tag) => (
                <span key={tag.id} className="pill" style={{ fontSize: 'var(--text-xs)' }}>
                  {tag.name}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Reactions row */}
        {reactions.length > 0 && (
          <div className="reactions-row" onClick={(e) => e.stopPropagation()}>
            {reactions.map((r) => (
              <button
                key={r.emoji}
                className="reaction-chip"
                type="button"
                onClick={(e) => handleReaction(e, r.emoji)}
              >
                {r.emoji} {r.count}
              </button>
            ))}
          </div>
        )}

        <div className="thread-card-meta">
          <UserIdentity user={thread.author} compact />
          <span className="timestamp" title={thread.created_at}>{formatTimeAgo(thread.created_at)}</span>
          <span>{thread.reply_count} replies</span>

          {/* Action buttons */}
          <div className="thread-card-actions" onClick={(e) => e.stopPropagation()}>
            <button
              className="thread-action-btn"
              type="button"
              title="Add reaction"
              onClick={(e) => { e.stopPropagation(); setShowEmojiPicker((c) => !c); }}
            >
              +&#x263A;
            </button>
            {session?.access_token && (
              <button
                className="thread-action-btn"
                type="button"
                title="Report"
                onClick={(e) => { e.stopPropagation(); setShowReportForm((c) => !c); }}
              >
                &#x26A0;
              </button>
            )}
          </div>
        </div>

        {/* Emoji picker dropdown */}
        {showEmojiPicker && (
          <div className="emoji-picker-row" onClick={(e) => e.stopPropagation()}>
            {QUICK_EMOJIS.map((emoji) => (
              <button
                key={emoji}
                className="emoji-pick-btn"
                type="button"
                onClick={(e) => handleReaction(e, emoji)}
              >
                {emoji}
              </button>
            ))}
          </div>
        )}

        {/* Report form */}
        {showReportForm && (
          <form
            className="report-form-inline"
            onClick={(e) => e.stopPropagation()}
            onSubmit={handleReport}
          >
            <input
              className="input"
              placeholder="Reason for report..."
              value={reportReason}
              onChange={(e) => setReportReason(e.target.value)}
            />
            <button className="secondary-button" type="submit">
              Submit report
            </button>
          </form>
        )}
        {reportMessage && <p className="muted-copy">{reportMessage}</p>}

        {showLoginPrompt && (
          <div onClick={(e) => e.stopPropagation()}>
            <LoginPrompt
              message="Log in to vote, react, and join the discussion."
              onClose={() => setShowLoginPrompt(false)}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default ThreadCard;
