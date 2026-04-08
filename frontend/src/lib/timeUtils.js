/**
 * @file timeUtils.js — Shared time and date formatting utilities for PulseBoard.
 *
 * **Interview topics: Relative time calculation, online status detection,
 * Date API, internationalisation with `toLocaleString`.**
 *
 * ### Why a dedicated time utility module?
 * Displaying times like "5m ago" or "2d ago" is needed in thread cards, post
 * timestamps, chat messages, notifications, and user profiles.  Centralising
 * the logic here avoids duplication and ensures consistent formatting across
 * the entire app.  If the format ever changes (e.g. localisation), there's
 * only one place to update.
 *
 * ### How "relative time" works:
 * 1. Parse the ISO 8601 date string from the API (e.g. "2025-04-01T12:00:00Z").
 * 2. Subtract it from `new Date()` (current time) to get elapsed milliseconds.
 * 3. Convert to the largest applicable unit (seconds → minutes → hours → days).
 * 4. Return a human-readable string like "just now", "5m ago", "3h ago".
 * 5. For dates older than 7 days, fall back to an absolute date string.
 *
 * ### Online status heuristic:
 * The backend updates a `last_seen` timestamp on every authenticated request.
 * If `last_seen` is within the last 5 minutes, we consider the user "online".
 * This is a simple polling-based approach — not as real-time as WebSocket
 * presence, but much simpler to implement and sufficient for most use cases.
 *
 * @module lib/timeUtils
 */

/**
 * Formats a date string into a human-friendly relative time string.
 *
 * The function uses a series of threshold checks (< 60s, < 60m, < 24h, < 7d)
 * to pick the most natural unit.  This "waterfall" pattern is standard for
 * relative time formatters.
 *
 * **Interview note — `Math.floor` vs `Math.round`:**
 * We use `Math.floor` to always round _down_.  Users expect "5m ago" to mean
 * "at least 5 minutes ago", not "closer to 5 minutes than 6".  Rounding up
 * could show "6m ago" when only 5 minutes and 31 seconds have passed, which
 * feels wrong.
 *
 * @param {string} dateString - ISO 8601 date string from the API
 *                              (e.g. "2025-04-01T12:00:00Z")
 * @returns {string} Human-readable relative time, e.g. "just now", "5m ago",
 *                   "3h ago", "2d ago", or a locale-formatted date for older
 *
 * @example
 * formatTimeAgo('2025-04-08T12:00:00Z'); // "5m ago" (if current time is 12:05)
 * formatTimeAgo(null);                    // ""
 */
export function formatTimeAgo(dateString) {
  if (!dateString) return '';

  const now = new Date();
  const date = new Date(dateString);

  // Elapsed time in seconds (integer, rounded down)
  const seconds = Math.floor((now - date) / 1000);

  // < 1 minute → "just now" (avoids awkward "37s ago")
  if (seconds < 60) return 'just now';

  // < 1 hour → show minutes
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  // < 1 day → show hours
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  // < 1 week → show days
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;

  // Older than 7 days → absolute date via the browser's locale settings.
  // `toLocaleDateString()` without options uses the user's OS locale,
  // which automatically handles date format preferences (MM/DD vs DD/MM).
  return date.toLocaleDateString();
}

/**
 * Formats a date string into a short absolute date.
 *
 * Uses `Intl.DateTimeFormat` (via `toLocaleDateString`) with explicit
 * options for consistent output across browsers and locales.
 *
 * @param {string} dateString - ISO 8601 date string from the API
 * @returns {string} Formatted date, e.g. "Mar 15, 2025", or "" if null
 *
 * @example
 * formatDate('2025-03-15T00:00:00Z'); // "Mar 15, 2025"
 */
export function formatDate(dateString) {
  if (!dateString) return '';
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short',   // "Mar" (abbreviated month name)
    day: 'numeric',   // "15" (no leading zero)
    year: 'numeric',  // "2025" (4-digit year)
  });
}

/**
 * Formats a date string into time-of-day only.
 *
 * Useful for chat messages and notifications where the date is already
 * shown elsewhere (e.g. in a date separator header).
 *
 * @param {string} dateString - ISO 8601 date string from the API
 * @returns {string} Formatted time, e.g. "2:30 PM", or "" if null
 *
 * @example
 * formatTime('2025-04-08T14:30:00Z'); // "2:30 PM" (in US locale)
 */
export function formatTime(dateString) {
  if (!dateString) return '';
  return new Date(dateString).toLocaleTimeString('en-US', {
    hour: 'numeric',   // "2" (no leading zero, 12-hour format in en-US)
    minute: '2-digit', // "30" (always 2 digits)
  });
}

/**
 * Determines if a user is currently "online" based on their `last_seen`
 * timestamp.
 *
 * **Interview note — the 5-minute threshold:**
 * This is a heuristic, not a real-time presence system.  The backend
 * updates `last_seen` on every authenticated API request (in the
 * `get_current_user` dependency).  If the user hasn't made a request in
 * 5 minutes, we assume they've gone idle/offline.  The 5-minute window
 * is a common industry convention (Slack, Discord, etc. use similar
 * thresholds, sometimes with additional "idle" states).
 *
 * @param {string|null} lastSeen - ISO 8601 date string, or null if the
 *                                 user has never been seen
 * @returns {boolean} True if `lastSeen` is within the last 5 minutes
 *
 * @example
 * isUserOnline('2025-04-08T12:03:00Z'); // true (if now is 12:05)
 * isUserOnline(null);                    // false
 */
export function isUserOnline(lastSeen) {
  if (!lastSeen) return false;

  // 5 minutes in milliseconds: 5 * 60 * 1000 = 300,000 ms
  const fiveMinutesAgo = Date.now() - 5 * 60 * 1000;

  // If last_seen is MORE RECENT than 5 minutes ago → user is online.
  return new Date(lastSeen).getTime() > fiveMinutesAgo;
}

/**
 * Formats a `last_seen` timestamp into a user-friendly status string.
 *
 * Composes `isUserOnline` and `formatTimeAgo` to produce one of:
 * - "Online"    — seen within the last 5 minutes
 * - "5m ago"    — seen recently but not within the threshold
 * - "Never"     — null `lastSeen` (user has never authenticated)
 *
 * @param {string|null} lastSeen - ISO 8601 date string, or null
 * @returns {string} "Online", a relative time string, or "Never"
 *
 * @example
 * formatLastSeen('2025-04-08T12:04:00Z'); // "Online"   (if now is 12:05)
 * formatLastSeen('2025-04-08T11:00:00Z'); // "1h ago"
 * formatLastSeen(null);                    // "Never"
 */
export function formatLastSeen(lastSeen) {
  if (!lastSeen) return 'Never';
  if (isUserOnline(lastSeen)) return 'Online';
  return formatTimeAgo(lastSeen);
}
