/**
 * Shared time formatting utilities for PulseBoard.
 */

/**
 * Format a date string into a human-friendly relative time.
 * e.g. "just now", "5m ago", "3h ago", "2d ago", or a full date.
 *
 * @param {string} dateString - ISO date string from the API
 * @returns {string} Human-readable relative time
 */
export function formatTimeAgo(dateString) {
  if (!dateString) return '';
  const now = new Date();
  const date = new Date(dateString);
  const seconds = Math.floor((now - date) / 1000);

  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
}

/**
 * Format a date string into a short date (e.g. "Mar 15, 2025").
 *
 * @param {string} dateString - ISO date string from the API
 * @returns {string} Formatted date
 */
export function formatDate(dateString) {
  if (!dateString) return '';
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

/**
 * Format a date string into time only (e.g. "2:30 PM").
 *
 * @param {string} dateString - ISO date string from the API
 * @returns {string} Formatted time
 */
export function formatTime(dateString) {
  if (!dateString) return '';
  return new Date(dateString).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * Determine if a user is "online" based on last_seen timestamp.
 * Online = last seen within the last 5 minutes.
 *
 * @param {string|null} lastSeen - ISO date string or null
 * @returns {boolean}
 */
export function isUserOnline(lastSeen) {
  if (!lastSeen) return false;
  const fiveMinutesAgo = Date.now() - 5 * 60 * 1000;
  return new Date(lastSeen).getTime() > fiveMinutesAgo;
}

/**
 * Format last seen into a human-friendly string.
 * e.g. "Online", "5m ago", "2d ago"
 *
 * @param {string|null} lastSeen - ISO date string or null
 * @returns {string}
 */
export function formatLastSeen(lastSeen) {
  if (!lastSeen) return 'Never';
  if (isUserOnline(lastSeen)) return 'Online';
  return formatTimeAgo(lastSeen);
}
