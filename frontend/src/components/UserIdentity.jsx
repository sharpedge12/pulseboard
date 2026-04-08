/**
 * @file UserIdentity.jsx
 * @description Reusable user identity display component — avatar, username, role, online status.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Reusable Components** — This component is used everywhere users are
 *    displayed: thread cards, post authors, chat participants, admin panels.
 *    It encapsulates all the logic for rendering a user's visual identity
 *    in one place (DRY principle — Don't Repeat Yourself).
 *
 * 2. **useMemo for Derived Values** — The `initials` fallback is computed
 *    via `useMemo` to avoid recalculating on every render. While the
 *    computation is trivial here, the pattern is important to demonstrate.
 *
 * 3. **Conditional Avatar Rendering** — Three cases: (a) Pulse bot gets a
 *    special SVG, (b) users with uploaded avatars get their image URL
 *    resolved correctly (local vs OAuth external URLs), (c) users without
 *    avatars get a letter-based fallback. This is a common "avatar strategy"
 *    pattern in production apps.
 *
 * 4. **Composition over Inheritance** — Rather than building the modal logic
 *    into this component, it renders `UserActionModal` as a sibling and
 *    controls it via `isModalOpen` state. This keeps each component focused
 *    on one responsibility (Single Responsibility Principle).
 *
 * 5. **Fragment Wrapper** — Returns `<>...</>` (React Fragment) because the
 *    component renders two sibling elements (the identity div + the modal).
 *    Fragments avoid adding extra DOM nodes.
 *
 * @see {@link ./UserActionModal.jsx} The modal rendered when clicking a user
 */

import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { isUserOnline } from '../lib/timeUtils';
import { assetUrl } from '../lib/api';
import UserActionModal from './UserActionModal';

/**
 * UserIdentity — Displays a user's avatar, username, role badge, and online status.
 *
 * Clicking the avatar/name opens a UserActionModal with message/friend/report actions.
 * In non-compact mode, a "View profile" link is also shown.
 *
 * VISUAL LAYOUT:
 * ┌─────────────────────────────────────┐
 * │ [Avatar●] @username · role          │
 * │           View profile  (if !compact)│
 * └─────────────────────────────────────┘
 *   ● = green online indicator dot
 *
 * @param {Object} props
 * @param {Object}   props.user      - User object from the API with fields:
 *   @param {number}  props.user.id         - Unique user ID
 *   @param {string}  props.user.username   - Display username
 *   @param {string}  props.user.role       - "admin", "moderator", or "member"
 *   @param {string}  [props.user.avatar_url] - Avatar image URL (may be null)
 *   @param {boolean} [props.user.is_online]  - Server-computed online status
 *   @param {string}  [props.user.last_seen]  - ISO timestamp of last activity
 * @param {boolean}  [props.compact=false]  - If true, hides "View profile" link (used in feed cards)
 * @param {boolean}  [props.showRole=true]  - If true, shows the role badge after username
 * @param {Function} [props.onRefresh]      - Optional callback invoked after modal actions (e.g., friend request)
 * @returns {JSX.Element|null} The rendered user identity, or null if no user is provided
 */
function UserIdentity({ user, compact = false, showRole = true, onRefresh }) {
  const navigate = useNavigate();

  /**
   * Controls whether the UserActionModal is open.
   *
   * INTERVIEW TIP: This is the "lifting state up" boundary. The modal's
   * open/close state lives HERE (the parent), not inside UserActionModal.
   * The parent decides WHEN to show the modal; the modal decides WHAT to show.
   */
  const [isModalOpen, setIsModalOpen] = useState(false);

  /**
   * Compute a 2-letter initial fallback for the avatar.
   *
   * INTERVIEW TIP: `useMemo` caches the result and only recomputes when
   * `user?.username` changes. The dependency array `[user?.username]` uses
   * optional chaining — if `user` is null, the dependency is `undefined`,
   * and useMemo won't crash. The fallback 'DU' stands for "Default User".
   *
   * @type {string} Two uppercase letters (e.g., "JD" for "john_doe")
   */
  const initials = useMemo(
    () => (user?.username ? user.username.slice(0, 2).toUpperCase() : 'DU'),
    [user?.username]
  );

  /**
   * Determine online status from either the server-computed `is_online` flag
   * or by checking `last_seen` against a 5-minute threshold on the client.
   *
   * INTERVIEW TIP: The `||` operator provides a client-side fallback.
   * The server sets `is_online` in `get_current_user()`, but if the field
   * is missing (e.g., from a cached response), we fall back to computing
   * it locally from `last_seen`. This is a defensive programming pattern.
   *
   * @type {boolean}
   */
  const online = user?.is_online || isUserOnline(user?.last_seen);

  /*
   * Early return (guard clause) — if no user object is provided, render nothing.
   *
   * INTERVIEW TIP: Guard clauses at the top of a component simplify the
   * rest of the function by eliminating null checks everywhere. Returning
   * `null` from a React component renders nothing to the DOM.
   */
  if (!user) {
    return null;
  }

  /**
   * Determine the avatar image source URL.
   *
   * Three cases to handle:
   * 1. Pulse bot  — always uses the local `/pulse-avatar.svg` static asset
   * 2. OAuth user — avatar_url starts with "http" (external URL from Google/GitHub)
   * 3. Local user — avatar_url is a relative path, resolved via `assetUrl()` helper
   * 4. No avatar  — `avatarSrc` is null, triggers the initials fallback
   *
   * INTERVIEW TIP: The `startsWith('http')` check prevents calling
   * `assetUrl()` on OAuth avatar URLs (which would prepend the local
   * server origin and break the URL). This was a real bug that was fixed.
   *
   * @type {string|null}
   */
  const isPulseBot = user?.username === 'pulse';
  const avatarSrc = isPulseBot
    ? '/pulse-avatar.svg'
    : user.avatar_url
      ? user.avatar_url.startsWith('http')
        ? user.avatar_url       // External OAuth avatar — use as-is
        : assetUrl(user.avatar_url) // Local upload — prefix with API base URL
      : null;                    // No avatar — will render initials fallback

  return (
    <>
      {/*
        INTERVIEW TIP: React Fragment (<>...</>) wraps two sibling elements
        (the identity display + the modal) without adding an extra DOM node.
        This is necessary because React components must return a single root
        element, and a wrapper <div> would break flex/grid layouts.
      */}
      <div
        className={
          compact ? 'user-identity user-identity-compact' : 'user-identity'
        }
      >
        {/*
          The user identity is wrapped in a <button> for accessibility.
          Using a <button> instead of a clickable <div> means:
          - It's focusable by default (Tab key)
          - Screen readers announce it as interactive
          - Enter/Space key presses trigger onClick
          Inline styles reset the button's default browser styling.
        */}
        <button
          className="user-identity"
          type="button"
          onClick={() => setIsModalOpen(true)}
          style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0 }}
        >
          {/* Avatar with online indicator overlay */}
          <div className="user-avatar" style={{ position: 'relative' }}>
            {avatarSrc ? (
              // Render the avatar image (bot SVG, OAuth URL, or local upload)
              <img src={avatarSrc} alt={user.username} />
            ) : (
              // Fallback: 2-letter initials in a colored circle
              <span className="user-avatar-fallback">{initials}</span>
            )}
            {/*
              Green dot online indicator — only rendered when user is online.
              Positioned absolutely relative to the avatar container.

              INTERVIEW TIP: This is CSS-driven conditional rendering.
              The `<span className="online-indicator" />` is a tiny green
              circle positioned at the bottom-right of the avatar via CSS.
            */}
            {online && <span className="online-indicator" />}
          </div>
          <span>
            {/* Username with @ prefix */}
            <span className="user-identity-name">@{user.username}</span>
            {/*
              Role badge (admin/moderator/member) shown after the username.
              The `showRole` prop allows callers to hide this in contexts
              where the role is redundant (e.g., inside an admin panel).
            */}
            {showRole && (
              <span className="user-identity-role"> &middot; {user.role}</span>
            )}
          </span>
        </button>

        {/*
          "View profile" link — only shown in non-compact mode.
          Navigates to the user's full profile page.

          INTERVIEW TIP: The `compact` prop controls this via conditional
          rendering. Feed cards use `compact` to save space; full post views
          show the profile link. This is the "configuration via props" pattern.
        */}
        {!compact && (
          <button
            className="user-identity-link"
            type="button"
            onClick={() => navigate(`/profile/${user.id}`)}
            style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0 }}
          >
            View profile
          </button>
        )}
      </div>

      {/*
        UserActionModal — rendered as a sibling, not a child of the identity div.
        It's always in the DOM but only visible when `isModalOpen` is true
        (the modal itself checks `isOpen` and returns null when false).

        INTERVIEW TIP: This is the "always-mounted, conditionally-visible"
        modal pattern. An alternative is conditional mounting:
          {isModalOpen && <UserActionModal ... />}
        Both work, but always-mounted allows the modal to manage its own
        enter/exit animations and cleanup via useEffect.
      */}
      <UserActionModal
        user={user}
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onRefresh={onRefresh}
      />
    </>
  );
}

export default UserIdentity;
