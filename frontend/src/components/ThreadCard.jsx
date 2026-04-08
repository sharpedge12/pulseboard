/**
 * @file ThreadCard.jsx
 * @description Thread summary card component — the primary unit of the post feed.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Controlled Components** — Vote score, reactions, report form state are all
 *    managed via `useState` and updated through event handlers, keeping the UI
 *    in sync with local state (optimistic updates after API calls).
 *
 * 2. **Event Propagation Control** — The card itself is clickable (navigates to
 *    the thread). Inner interactive elements (vote buttons, emoji picker, report
 *    form) call `event.stopPropagation()` to prevent the click from bubbling up
 *    and triggering navigation. This is a VERY common interview topic.
 *
 * 3. **Conditional Rendering** — Emoji picker, report form, login prompt, tags,
 *    reactions, and pinned/locked badges are all conditionally rendered based on
 *    state. Notice the pattern: `{condition && <JSX />}`.
 *
 * 4. **Authentication-Gated Actions** — Voting, reacting, and reporting check
 *    `session?.access_token` before proceeding. If the user is a guest, a
 *    `LoginPrompt` component is shown instead. This is a UX best practice:
 *    show the action but guide unauthenticated users toward login.
 *
 * 5. **Keyboard Accessibility** — The card has `role="button"`, `tabIndex={0}`,
 *    and an `onKeyDown` handler that responds to Enter/Space, making it
 *    navigable for screen reader and keyboard-only users (WCAG compliance).
 *
 * @see {@link https://react.dev/reference/react/useState} React useState
 * @see {@link https://developer.mozilla.org/en-US/docs/Web/API/Event/stopPropagation} stopPropagation
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';
import { formatTimeAgo } from '../lib/timeUtils';
import UserIdentity from './UserIdentity';
import AttachmentList from './AttachmentList';
import LoginPrompt from './LoginPrompt';

/**
 * Quick emoji palette for thread reactions.
 * Defined outside the component to avoid re-creating the array on every render.
 *
 * INTERVIEW TIP: Constants that don't depend on props or state should live
 * outside the component body. This prevents unnecessary object allocations
 * on each render cycle and is a simple performance optimization.
 *
 * @constant {string[]}
 */
const QUICK_EMOJIS = ['\uD83D\uDC4D', '\u2764\uFE0F', '\uD83D\uDE02', '\uD83D\uDE2E', '\uD83D\uDE4F', '\uD83D\uDD25'];

/**
 * ThreadCard — Renders a single thread as a clickable card in the feed.
 *
 * LAYOUT STRUCTURE (Reddit-inspired):
 * ┌──────────────────────────────────────────────┐
 * │ [▲]  │  r/category-slug                      │
 * │ score│  Thread Title  [Pinned] [Locked]       │
 * │ [▼]  │  Preview text (first 140 chars)...     │
 * │      │  [tag1] [tag2]                         │
 * │      │  [👍 3] [❤️ 1]  (reaction chips)       │
 * │      │  @author · 2h ago · 5 replies  [+😊][⚠]│
 * └──────────────────────────────────────────────┘
 *
 * @param {Object} props
 * @param {Object} props.thread - Thread data from the API containing:
 *   @param {number}   props.thread.id           - Unique thread ID
 *   @param {string}   props.thread.title        - Thread title
 *   @param {string}   props.thread.body         - Full thread body text
 *   @param {Object}   props.thread.category     - Category with `.slug` property
 *   @param {Object}   props.thread.author       - Author user object
 *   @param {number}   props.thread.vote_score   - Net vote score (upvotes - downvotes)
 *   @param {number}   props.thread.user_vote    - Current user's vote (-1, 0, or 1)
 *   @param {Array}    props.thread.reactions     - Array of {emoji, count} objects
 *   @param {Array}    props.thread.tags          - Array of {id, name} tag objects
 *   @param {Array}    props.thread.attachments   - File attachments
 *   @param {boolean}  props.thread.is_pinned     - Whether thread is pinned by mods
 *   @param {boolean}  props.thread.is_locked     - Whether thread is locked
 *   @param {number}   props.thread.reply_count   - Number of replies
 *   @param {string}   props.thread.created_at    - ISO 8601 timestamp
 * @returns {JSX.Element} The rendered thread card
 */
function ThreadCard({ thread }) {
  /**
   * React Router's navigation hook — used to programmatically navigate
   * to the thread detail page when the card is clicked.
   *
   * INTERVIEW TIP: `useNavigate` replaces the older `useHistory` from
   * React Router v5. It returns a function, not an object.
   */
  const navigate = useNavigate();

  /**
   * Auth context provides the current session (JWT tokens).
   * We destructure only `session` because this component just needs to
   * check if the user is authenticated and send authorized API requests.
   *
   * INTERVIEW TIP: Context is React's built-in dependency injection.
   * `useAuth()` is a custom hook wrapping `useContext(AuthContext)`.
   */
  const { session } = useAuth();

  /*
   * ── Local State ──────────────────────────────────────────────────
   *
   * INTERVIEW TIP: Each `useState` call creates an independent piece of
   * state. React batches state updates within event handlers, so calling
   * multiple setters in one handler triggers only ONE re-render.
   *
   * We initialize vote/reaction state from the thread prop. This is a
   * form of "derived initial state" — the prop seeds the state once,
   * then local state diverges as the user interacts (optimistic UI).
   */

  /** @type {[number, Function]} Net vote score, updated optimistically after API call */
  const [voteScore, setVoteScore] = useState(thread.vote_score ?? 0);

  /** @type {[number, Function]} Current user's vote: -1 (downvote), 0 (none), 1 (upvote) */
  const [userVote, setUserVote] = useState(thread.user_vote ?? 0);

  /** @type {[Array, Function]} Reaction counts array: [{emoji: "👍", count: 3}, ...] */
  const [reactions, setReactions] = useState(thread.reactions ?? []);

  /** @type {[boolean, Function]} Controls visibility of the emoji picker dropdown */
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);

  /** @type {[boolean, Function]} Controls visibility of the inline report form */
  const [showReportForm, setShowReportForm] = useState(false);

  /** @type {[string, Function]} Controlled input value for the report reason textarea */
  const [reportReason, setReportReason] = useState('');

  /** @type {[string, Function]} Feedback message shown after submitting a report */
  const [reportMessage, setReportMessage] = useState('');

  /** @type {[boolean, Function]} Controls visibility of the LoginPrompt for guests */
  const [showLoginPrompt, setShowLoginPrompt] = useState(false);

  /**
   * Handles upvote/downvote on the thread.
   *
   * PATTERN: Authentication guard + optimistic update.
   * 1. Check if user is logged in — if not, show LoginPrompt instead.
   * 2. Send POST to API with the vote value (1 or -1).
   * 3. Update local state with the server's response (authoritative score).
   *
   * INTERVIEW TIP: `event.stopPropagation()` is critical here. Without it,
   * clicking the vote button would also trigger the card's onClick, navigating
   * away from the feed. This is the "nested clickable elements" problem.
   *
   * @param {React.SyntheticEvent} event - The click event
   * @param {number} value - Vote value: 1 for upvote, -1 for downvote
   * @returns {Promise<void>}
   */
  async function handleVote(event, value) {
    // Prevent the click from bubbling to the card's onClick handler
    event.stopPropagation();

    // Auth guard: show login prompt for unauthenticated users
    if (!session?.access_token) {
      setShowLoginPrompt(true);
      return;
    }

    try {
      // POST to the vote endpoint; server returns the updated score and user's vote
      const result = await apiRequest(`/threads/${thread.id}/vote`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ value }),
      });
      // Update state with server-authoritative values (not optimistic guess)
      setVoteScore(result.vote_score);
      setUserVote(result.value);
    } catch {
      /* Silently ignore vote errors — the UI stays at its current state */
    }
  }

  /**
   * Handles adding/toggling a reaction emoji on the thread.
   *
   * PATTERN: Same auth-guard + API call pattern as handleVote.
   * The server returns the full updated reaction counts array, which
   * replaces the local state entirely (server is source of truth).
   *
   * @param {React.SyntheticEvent} event - The click event
   * @param {string} emoji - The emoji character to react with
   * @returns {Promise<void>}
   */
  async function handleReaction(event, emoji) {
    event.stopPropagation();
    if (!session?.access_token) {
      setShowLoginPrompt(true);
      return;
    }

    try {
      // Server returns the complete updated reactions array
      const counts = await apiRequest(`/threads/${thread.id}/react`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ emoji }),
      });
      setReactions(counts);
      setShowEmojiPicker(false); // Close the picker after selecting an emoji
    } catch {
      /* ignore */
    }
  }

  /**
   * Handles submitting a content report for this thread.
   *
   * PATTERN: Form submission with controlled input.
   * - `event.preventDefault()` stops the native form submission (page reload).
   * - `event.stopPropagation()` prevents the card click handler from firing.
   * - After success, the form is cleared and hidden, and a confirmation is shown.
   *
   * INTERVIEW TIP: Always call `preventDefault()` on form submit events
   * in SPAs. Without it, the browser will reload the page.
   *
   * @param {React.SyntheticEvent} event - The form submit event
   * @returns {Promise<void>}
   */
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
      setReportReason('');       // Clear the input
      setShowReportForm(false);  // Hide the form
    } catch (error) {
      setReportMessage(error.message);
    }
  }

  /*
   * ── JSX Render ───────────────────────────────────────────────────
   *
   * INTERVIEW TIP: The entire card is wrapped in a clickable <div> with
   * role="button" and tabIndex={0} for accessibility. This pattern makes
   * a non-interactive element behave like a button for assistive technology.
   *
   * Notice how `onKeyDown` handles both Enter and Space — this matches
   * the native <button> behavior that keyboard users expect.
   */
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
      {/*
        ── Vote Column ─────────────────────────────────────────────
        Reddit-style vertical vote controls: up arrow, score, down arrow.

        INTERVIEW TIP: The vote column has its own `onClick={e => e.stopPropagation()}`
        to create a "dead zone" — clicks anywhere in this column won't navigate.
        Individual vote buttons ALSO stop propagation for extra safety.

        The ternary in the className conditionally adds 'upvoted'/'downvoted'
        classes for visual feedback (color change on the active vote arrow).
      */}
      <div className="vote-column" onClick={(e) => e.stopPropagation()}>
        <button
          className={`vote-btn ${userVote === 1 ? 'upvoted' : ''}`}
          type="button"
          title="Upvote"
          onClick={(e) => handleVote(e, 1)}
        >
          &#x25B2;{/* Unicode up-pointing triangle ▲ */}
        </button>
        {/*
          Score display with conditional color classes.
          INTERVIEW TIP: Nested ternary — `positive` if > 0, `negative` if < 0, '' if 0.
          While nested ternaries can hurt readability, this one is simple enough.
        */}
        <span className={`vote-score ${voteScore > 0 ? 'positive' : voteScore < 0 ? 'negative' : ''}`}>{voteScore}</span>
        <button
          className={`vote-btn ${userVote === -1 ? 'downvoted' : ''}`}
          type="button"
          title="Downvote"
          onClick={(e) => handleVote(e, -1)}
        >
          &#x25BC;{/* Unicode down-pointing triangle ▼ */}
        </button>
      </div>

      {/* ── Main Content Area ──────────────────────────────────────── */}
      <div className="thread-card-body">
        <div>
          {/* Category slug displayed as subreddit-style "r/category" */}
          <p className="thread-card-community">r/{thread.category.slug}</p>

          {/* Thread title with optional status badges */}
          <div className="thread-card-title">
            {thread.title}
            {/*
              INTERVIEW TIP: Conditional rendering with `&&` short-circuit.
              If `thread.is_pinned` is falsy, React renders nothing.
              This is the idiomatic way to conditionally include JSX elements.
            */}
            {thread.is_pinned && <span className="thread-pill">Pinned</span>}
            {thread.is_locked && (
              <span className="thread-pill thread-pill-muted">Locked</span>
            )}
          </div>

          {/* Body preview — truncated to 140 characters for the feed card */}
          <p className="thread-card-preview">{thread.body.slice(0, 140)}</p>

          {/* File attachments (images, documents) rendered by AttachmentList */}
          <AttachmentList attachments={thread.attachments} />

          {/*
            Tag pills row — only rendered if tags exist.
            INTERVIEW TIP: The `&&` guard checks both existence and length.
            `thread.tags && thread.tags.length > 0` prevents rendering an
            empty container div, which would add unnecessary DOM nodes.

            Each tag pill stops propagation to prevent card navigation
            when tags become clickable (future feature).
          */}
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

        {/*
          ── Reactions Row ──────────────────────────────────────────
          Shows existing reaction chips (e.g., "👍 3"). Clicking a chip
          toggles that reaction for the current user.

          INTERVIEW TIP: Each reaction chip is a <button>, not a <span>.
          Interactive elements should use semantic HTML elements that are
          natively keyboard-accessible and announced by screen readers.
        */}
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

        {/*
          ── Metadata Row ───────────────────────────────────────────
          Author identity, relative timestamp, reply count, and action buttons.
        */}
        <div className="thread-card-meta">
          {/* UserIdentity renders the author's avatar, name, and role badge */}
          <UserIdentity user={thread.author} compact />

          {/*
            Relative time display (e.g., "2h ago") with full ISO timestamp as tooltip.
            INTERVIEW TIP: The `title` attribute shows the exact datetime on hover,
            giving users precision when they need it while keeping the UI clean.
          */}
          <span className="timestamp" title={thread.created_at}>{formatTimeAgo(thread.created_at)}</span>
          <span>{thread.reply_count} replies</span>

          {/* Action buttons: emoji picker toggle + report toggle */}
          <div className="thread-card-actions" onClick={(e) => e.stopPropagation()}>
            <button
              className="thread-action-btn"
              type="button"
              title="Add reaction"
              onClick={(e) => { e.stopPropagation(); setShowEmojiPicker((c) => !c); }}
            >
              +&#x263A;{/* Smiley face ☺ with plus sign */}
            </button>
            {/*
              Report button only visible to authenticated users.
              INTERVIEW TIP: `session?.access_token` uses optional chaining.
              If session is null/undefined, the expression short-circuits to
              undefined (falsy), so the button won't render for guests.
            */}
            {session?.access_token && (
              <button
                className="thread-action-btn"
                type="button"
                title="Report"
                onClick={(e) => { e.stopPropagation(); setShowReportForm((c) => !c); }}
              >
                &#x26A0;{/* Warning sign ⚠ */}
              </button>
            )}
          </div>
        </div>

        {/*
          ── Emoji Picker Dropdown ──────────────────────────────────
          A simple row of emoji buttons, toggled by the "+☺" action button.

          INTERVIEW TIP: This is a controlled toggle pattern. The dropdown
          visibility is driven by `showEmojiPicker` state. Clicking an emoji
          calls `handleReaction`, which also closes the picker via
          `setShowEmojiPicker(false)`.
        */}
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

        {/*
          ── Inline Report Form ─────────────────────────────────────
          A controlled form for submitting content reports.

          INTERVIEW TIP: This is a "controlled form" — the input value is
          driven by `reportReason` state, and `onChange` updates that state.
          The form's `onSubmit` handler calls `handleReport`, which sends
          the API request and manages success/error feedback.
        */}
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
        {/* Report feedback message (success or error) */}
        {reportMessage && <p className="muted-copy">{reportMessage}</p>}

        {/*
          ── Login Prompt ───────────────────────────────────────────
          Shown when a guest user attempts a protected action (vote, react).
          The LoginPrompt component renders a dismissible banner with a
          "Log In" button that navigates to /login.

          INTERVIEW TIP: This is the "progressive disclosure" UX pattern.
          Instead of hiding vote/react buttons from guests (which would confuse
          them), we show the buttons but surface a helpful prompt on interaction.
          This teaches guests about the feature while guiding them to sign up.
        */}
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
