/**
 * @file UserActionModal.jsx
 * @description Modal dialog for user actions — message, friend request, report.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Modal Pattern (Backdrop + Card)** — The modal renders a full-screen
 *    semi-transparent backdrop div. Clicking the backdrop closes the modal.
 *    The modal card itself calls `e.stopPropagation()` on its onClick to
 *    prevent clicks inside the card from closing the modal. This is the
 *    STANDARD React modal implementation pattern.
 *
 * 2. **useEffect for Data Fetching** — When the modal opens, a `useEffect`
 *    fetches fresh user data from the API. This ensures the modal shows
 *    up-to-date info (e.g., current friendship status, online status)
 *    rather than stale data from the parent component's last render.
 *
 * 3. **Conditional Actions Based on Auth State** — The modal shows different
 *    actions depending on: (a) whether the viewer is authenticated,
 *    (b) whether the viewer is looking at their own profile, and
 *    (c) the current friendship status with the target user.
 *
 * 4. **Derived State with useMemo** — `avatarSrc` is memoized to avoid
 *    recomputing the URL resolution on every render.
 *
 * 5. **State Machine for Friendship** — The `friendStatus` value
 *    ("none" | "friends" | "outgoing_pending" | "incoming_pending") drives
 *    both the button label and disabled state. This is a simple state
 *    machine pattern without a formal library.
 *
 * @see {@link ./UserIdentity.jsx} The component that triggers this modal
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';

/**
 * UserActionModal — Full-screen modal for interacting with another user.
 *
 * LAYOUT:
 * ┌─────────────────────────────────────────────┐
 * │  (semi-transparent backdrop — click to close)│
 * │  ┌─────────────────────────────────────┐     │
 * │  │ @username                    [Close] │     │
 * │  │ [Avatar]  @username                  │     │
 * │  │           role · Verified             │     │
 * │  │           "User's bio text"           │     │
 * │  │           Status: friends             │     │
 * │  │                                       │     │
 * │  │ [Message] [Add friend] [Report]       │     │
 * │  │                                       │     │
 * │  │ "Friend request sent."  (feedback)    │     │
 * │  └─────────────────────────────────────┘     │
 * └─────────────────────────────────────────────┘
 *
 * @param {Object} props
 * @param {Object}   props.user      - Initial user data (may be stale; fresh data is fetched on open)
 * @param {boolean}  props.isOpen    - Whether the modal is currently visible
 * @param {Function} props.onClose   - Callback to close the modal
 * @param {Function} [props.onRefresh] - Optional callback after successful action (e.g., parent re-fetches user list)
 * @returns {JSX.Element|null} The modal dialog, or null when closed / no user
 */
function UserActionModal({ user, isOpen, onClose, onRefresh }) {
  const navigate = useNavigate();

  /**
   * Auth context provides session (for API auth) and profile (for self-detection).
   *
   * INTERVIEW TIP: We destructure both `session` and `profile` here.
   * `session` is used for API authentication headers.
   * `profile` is used to check if the viewed user is the current user (isSelf).
   */
  const { session, profile } = useAuth();

  /** Feedback message displayed after actions (success or error) */
  const [message, setMessage] = useState('');

  /**
   * Local copy of user data that gets refreshed when the modal opens.
   *
   * INTERVIEW TIP: We maintain `liveUser` separately from the `user` prop
   * because the prop might be stale (from the parent's last render). When
   * the modal opens, we fetch fresh data and store it in `liveUser`.
   * This is the "stale prop, fresh fetch" pattern.
   */
  const [liveUser, setLiveUser] = useState(user);

  /**
   * Sync liveUser when the user prop changes (e.g., parent re-renders with new user).
   *
   * INTERVIEW TIP: This useEffect ensures that if the parent passes a
   * DIFFERENT user (e.g., clicking a different username), liveUser updates
   * immediately. Without this, the modal would show the old user until
   * the fresh fetch completes.
   */
  useEffect(() => {
    setLiveUser(user);
  }, [user]);

  /**
   * Fetch fresh user data when the modal opens.
   *
   * INTERVIEW TIP: This is the "fetch on mount/open" pattern. The effect
   * runs whenever `isOpen`, `user.id`, or `session` changes. The guard
   * clause (`if (!isOpen || ...)`) prevents unnecessary API calls when
   * the modal is closed.
   *
   * Note the `async function` inside useEffect — you cannot make the
   * useEffect callback itself async (React expects it to return void or
   * a cleanup function), so we define an inner async function and call it.
   */
  useEffect(() => {
    async function loadFreshUser() {
      if (!isOpen || !user?.id || !session?.access_token) {
        return;
      }
      try {
        const data = await apiRequest(`/users/${user.id}`, {
          headers: getHeaders(session.access_token),
        });
        setLiveUser(data);
      } catch {
        // If the fetch fails, fall back to the prop data
        setLiveUser(user);
      }
    }

    loadFreshUser();
  }, [isOpen, user?.id, session?.access_token]);

  /**
   * The "active" user object — prefer liveUser (fresh fetch), fall back to prop.
   *
   * INTERVIEW TIP: This defensive fallback pattern ensures the component
   * always has user data to render, even if the API call fails or hasn't
   * completed yet.
   */
  const activeUser = liveUser || user;

  /**
   * Memoized avatar source URL.
   *
   * Handles the same three cases as UserIdentity:
   * - External URL (OAuth) — use as-is
   * - Relative path (local upload) — resolve via assetUrl()
   * - No avatar — null (triggers initials fallback)
   *
   * INTERVIEW TIP: `useMemo` here prevents re-running the URL logic on
   * every render. The dependency is `activeUser?.avatar_url` — the memo
   * only recomputes when the avatar URL actually changes.
   *
   * @type {string|null}
   */
  const avatarSrc = useMemo(() => {
    if (!activeUser?.avatar_url) {
      return null;
    }
    return activeUser.avatar_url.startsWith('http')
      ? activeUser.avatar_url
      : assetUrl(activeUser.avatar_url);
  }, [activeUser?.avatar_url]);

  /*
   * Early return — render nothing if the modal is closed or there's no user.
   *
   * INTERVIEW TIP: This is placed AFTER all hooks (useState, useEffect, useMemo).
   * React's "Rules of Hooks" require that hooks are always called in the same
   * order, regardless of conditions. You CANNOT put a return statement before
   * a hook call. This is why early returns in React components must come after
   * all hook declarations.
   */
  if (!isOpen || !activeUser) {
    return null;
  }

  /**
   * Whether the modal is showing the current user's own profile.
   * When true, action buttons (message, friend, report) are hidden.
   * @type {boolean}
   */
  const isSelf = profile?.id === activeUser.id;

  /**
   * Re-fetch user data after an action (friend request, report, etc.).
   * Also calls the parent's onRefresh callback if provided.
   *
   * INTERVIEW TIP: This pattern keeps the modal's data fresh after
   * mutations. After sending a friend request, we re-fetch the user
   * to get the updated `friendship_status`. Optional chaining `?.()` on
   * `onRefresh` safely calls it only if it exists.
   *
   * @returns {Promise<void>}
   */
  async function refreshUserState() {
    if (!session?.access_token) {
      return;
    }
    const data = await apiRequest(`/users/${activeUser.id}`, {
      headers: getHeaders(session.access_token),
    });
    setLiveUser(data);
    await onRefresh?.();
  }

  /**
   * Send or accept a friend request.
   *
   * The server handles the logic:
   * - If no request exists: creates a new outgoing request
   * - If an incoming request exists: accepts it
   * - If already friends: returns an error message
   *
   * @returns {Promise<void>}
   */
  async function handleFriend() {
    try {
      const data = await apiRequest(`/users/${activeUser.id}/friend`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
      });
      setMessage(data.message);
      // Refresh to update friendship_status (e.g., "none" -> "outgoing_pending")
      await refreshUserState();
    } catch (error) {
      setMessage(error.message);
    }
  }

  /**
   * Open a direct message chat room with this user.
   *
   * The server either creates a new DM room or returns the existing one.
   * After getting the room ID, we close the modal and navigate to the chat.
   *
   * INTERVIEW TIP: `encodeURIComponent` is used on the username because
   * usernames could theoretically contain characters that need URL encoding.
   * Always encode dynamic path segments to prevent broken URLs.
   *
   * @returns {Promise<void>}
   */
  async function handleMessage() {
    try {
      const data = await apiRequest(
        `/chat/direct/${encodeURIComponent(activeUser.username)}`,
        {
          method: 'POST',
          headers: getHeaders(session.access_token),
        }
      );
      onClose(); // Close the modal before navigating
      navigate(`/chat?room=${data.id}`);
    } catch (error) {
      setMessage(error.message);
    }
  }

  /**
   * Report this user to moderators.
   *
   * Sends a content report with a pre-filled reason string.
   * The reason includes the username for context in the mod dashboard.
   *
   * @returns {Promise<void>}
   */
  async function handleReport() {
    try {
      const data = await apiRequest(`/users/${activeUser.id}/report`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({
          reason: `Reported @${activeUser.username} from profile modal`,
        }),
      });
      setMessage(data.message);
    } catch (error) {
      setMessage(error.message);
    }
  }

  /**
   * Friendship status drives the friend button's label and disabled state.
   *
   * INTERVIEW TIP: This is a simple "state machine" for the friend button.
   * The server returns one of: "none", "friends", "outgoing_pending",
   * "incoming_pending". Each state maps to a different label and behavior:
   *
   *   "none"             -> "Add friend"     (enabled, sends request)
   *   "friends"          -> "Friends"         (disabled, already friends)
   *   "outgoing_pending" -> "Request sent"    (disabled, waiting for response)
   *   "incoming_pending" -> "Accept request"  (enabled, accepts the request)
   */
  const friendStatus = activeUser.friendship_status || 'none';

  const friendLabel =
    friendStatus === 'friends'
      ? 'Friends'
      : friendStatus === 'outgoing_pending'
        ? 'Request sent'
        : friendStatus === 'incoming_pending'
          ? 'Accept request'
          : 'Add friend';

  return (
    /*
     * ── Modal Backdrop ────────────────────────────────────────────
     *
     * INTERVIEW TIP: The backdrop pattern for modals:
     * 1. A full-screen overlay div covers the entire viewport.
     * 2. Clicking the overlay (backdrop) triggers onClose.
     * 3. The inner card calls `e.stopPropagation()` to prevent
     *    clicks inside the card from closing the modal.
     *
     * This is the simplest modal implementation. Libraries like
     * React Portal (`createPortal`) can render the modal outside
     * the component tree to avoid z-index and overflow issues.
     */
    <div className="modal-backdrop" onClick={onClose}>
      {/*
        Modal card — stopPropagation prevents backdrop close on inner clicks.

        INTERVIEW TIP: This is the critical line. Without stopPropagation,
        any click inside the modal card would bubble up to the backdrop's
        onClick handler and close the modal immediately.
      */}
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        {/* Header: username + close button */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-4)' }}>
          <h3>@{activeUser.username}</h3>
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
        </div>

        {/* User info section: avatar + metadata */}
        <div style={{ display: 'flex', gap: 'var(--space-4)', alignItems: 'center', marginBottom: 'var(--space-4)' }}>
          {avatarSrc ? (
            <img
              className="profile-avatar-preview"
              src={avatarSrc}
              alt={activeUser.username}
            />
          ) : (
            // Initials fallback — 2-letter badge when no avatar is available
            <div className="profile-badge">
              {activeUser.username.slice(0, 2).toUpperCase()}
            </div>
          )}
          <div>
            <strong>@{activeUser.username}</strong>
            <p className="muted-copy">
              {activeUser.role} &middot;{' '}
              {activeUser.is_verified ? 'Verified' : 'Unverified'}
            </p>
            <p className="muted-copy">
              {activeUser.bio || 'No bio yet.'}
            </p>
            {/*
              Friendship status — only shown for other users (not self).
              Displays: "Status: friends", "Status: outgoing pending", etc.
            */}
            {!isSelf && (
              <p className="muted-copy">
                Status: {friendStatus.replace('_', ' ')}
              </p>
            )}
          </div>
        </div>

        {/*
          ── Action Buttons (for other users) ──────────────────────
          Only rendered when viewing someone else's profile (!isSelf).

          Three actions:
          1. Message — opens/creates a DM chat room
          2. Friend  — sends/accepts friend request (disabled when already friends or pending)
          3. Report  — files a content report with moderators

          INTERVIEW TIP: The `disabled` prop on the friend button uses a
          boolean expression. When `friendStatus` is "friends" or
          "outgoing_pending", the button is disabled (greyed out, not clickable).
          This prevents duplicate friend requests.
        */}
        {!isSelf && (
          <div className="edit-inline-actions">
            <button
              className="action-button"
              type="button"
              onClick={handleMessage}
            >
              Message
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={handleFriend}
              disabled={
                friendStatus === 'friends' ||
                friendStatus === 'outgoing_pending'
              }
            >
              {friendLabel}
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={handleReport}
            >
              Report
            </button>
          </div>
        )}

        {/*
          ── Self-View Actions ──────────────────────────────────────
          When the user clicks their own avatar, show a "Go to my profile"
          button instead of message/friend/report actions.
        */}
        {isSelf && (
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              onClose();
              navigate('/profile');
            }}
          >
            Go to my profile
          </button>
        )}

        {/* Feedback message — shown after any action (success or error) */}
        {message && <p className="success-copy">{message}</p>}
      </div>
    </div>
  );
}

export default UserActionModal;
