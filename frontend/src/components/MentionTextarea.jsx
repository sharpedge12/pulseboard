/**
 * @file MentionTextarea.jsx
 * @description Textarea with @mention autocomplete — debounced user search,
 *              keyboard navigation, and smart Enter key delegation.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Debounced API Calls** — When the user types `@ali`, we don't fire
 *    an API request on every keystroke. Instead, we use `setTimeout` with
 *    a 200ms delay (debouncing). If the user types another character within
 *    200ms, the previous timer is cleared. This reduces API load dramatically.
 *    In production, you'd use `lodash.debounce` or a custom hook, but the
 *    raw setTimeout pattern is essential to understand.
 *
 * 2. **Keyboard Navigation in Dropdowns** — Arrow Up/Down move the selection,
 *    Enter selects, Escape closes. This is the accessibility-compliant way
 *    to build autocomplete dropdowns. The component manages `selectedIndex`
 *    state to track which suggestion is highlighted.
 *
 * 3. **Event Delegation / Key Forwarding** — The textarea's `onKeyDown` has
 *    two responsibilities: (a) handle mention dropdown keys when it's open,
 *    (b) forward all other keys to the parent's `onKeyDown` handler. This
 *    allows the parent (e.g., ChatPage) to implement "Enter to send" while
 *    the MentionTextarea handles "Enter to select mention" when the dropdown
 *    is open. This is a subtle but important pattern.
 *
 * 4. **Cursor Position Management** — After inserting a mention, the cursor
 *    must be placed after the inserted `@username ` text. This requires
 *    `setTimeout(() => textarea.setSelectionRange(...), 0)` because React
 *    may not have re-rendered the textarea with the new value yet.
 *
 * 5. **useRef for DOM Access and Timers** — Three refs are used:
 *    - `textareaRef`: direct DOM access for focus/cursor positioning
 *    - `dropdownRef`: detecting outside clicks for dropdown dismissal
 *    - `debounceTimer`: storing the setTimeout ID across renders (persists
 *      without triggering re-renders, unlike useState)
 *
 * 6. **useCallback for Stable References** — `searchUsers` is wrapped in
 *    `useCallback` so it doesn't change identity on every render. This
 *    prevents unnecessary effect re-runs if it were used in a dependency array.
 *
 * @see {@link https://developer.mozilla.org/en-US/docs/Web/API/HTMLTextAreaElement/setSelectionRange} setSelectionRange
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';

/**
 * MentionTextarea — A controlled textarea with @mention autocomplete.
 *
 * HOW IT WORKS (data flow):
 * ┌─────────────────────────────────────────────────────────┐
 * │ User types "@al"                                        │
 * │   └─> handleChange() detects "@al" via detectMention()  │
 * │       └─> debounceTimer starts (200ms)                  │
 * │           └─> searchUsers("al") fires API call          │
 * │               └─> setSuggestions([alice, alex, ...])     │
 * │                   └─> Dropdown renders with results      │
 * │                                                          │
 * │ User presses ArrowDown                                   │
 * │   └─> handleKeyDown() increments selectedIndex           │
 * │                                                          │
 * │ User presses Enter                                       │
 * │   └─> handleKeyDown() calls insertMention("alice")       │
 * │       └─> Text becomes "Hello @alice " + cursor placed   │
 * │                                                          │
 * │ User presses Enter (dropdown CLOSED)                     │
 * │   └─> handleKeyDown() delegates to externalKeyDown       │
 * │       └─> Parent's handler fires (e.g., "send message")  │
 * └─────────────────────────────────────────────────────────┘
 *
 * @param {Object} props
 * @param {string}   props.value       - Current textarea value (controlled component)
 * @param {Function} props.onChange     - Callback: (newValue: string) => void
 * @param {string}   props.token       - JWT access token for the user search API
 * @param {string}   [props.placeholder] - Textarea placeholder text
 * @param {boolean}  [props.disabled]    - Whether the textarea is disabled
 * @param {string}   [props.className]   - Additional CSS class names for the textarea
 * @param {Function} [props.onKeyDown]   - External keydown handler (receives events NOT consumed by mention logic)
 * @param {number}   [props.rows]        - Number of visible text rows
 * @returns {JSX.Element} The textarea with autocomplete dropdown
 */
function MentionTextarea({ value, onChange, token, placeholder, disabled, className, onKeyDown: externalKeyDown, rows }) {
  /*
   * ── State ────────────────────────────────────────────────────────
   *
   * INTERVIEW TIP: Notice the separation of concerns in state:
   * - `suggestions` = data from the API (search results)
   * - `showDropdown` = UI visibility flag
   * - `mentionQuery` = the current search term (what comes after @)
   * - `mentionStart` = cursor position where the @ begins (for text replacement)
   * - `selectedIndex` = which suggestion is highlighted (keyboard nav)
   */

  /** @type {[Array<Object>, Function]} User search results from the API */
  const [suggestions, setSuggestions] = useState([]);

  /** @type {[boolean, Function]} Whether the dropdown is visible */
  const [showDropdown, setShowDropdown] = useState(false);

  /** @type {[string, Function]} The text after @ being used as the search query */
  const [mentionQuery, setMentionQuery] = useState('');

  /** @type {[number, Function]} Character index in the textarea where @ starts */
  const [mentionStart, setMentionStart] = useState(-1);

  /** @type {[number, Function]} Index of the currently highlighted suggestion */
  const [selectedIndex, setSelectedIndex] = useState(0);

  /*
   * ── Refs ─────────────────────────────────────────────────────────
   *
   * INTERVIEW TIP: useRef is used for two distinct purposes here:
   *
   * 1. DOM element refs (textareaRef, dropdownRef) — Direct access to
   *    DOM nodes for imperative operations (focus, cursor positioning,
   *    hit-testing for "click outside" detection). React's declarative
   *    model can't express these operations.
   *
   * 2. Mutable value ref (debounceTimer) — Stores the setTimeout ID
   *    across renders WITHOUT triggering re-renders. If we used useState
   *    for the timer ID, updating it would cause an unnecessary re-render.
   *    useRef.current is a mutable box that persists across renders.
   */

  /** @type {React.RefObject<HTMLTextAreaElement>} Direct access to the textarea DOM element */
  const textareaRef = useRef(null);

  /** @type {React.RefObject<HTMLDivElement>} Direct access to the dropdown DOM element */
  const dropdownRef = useRef(null);

  /** @type {React.RefObject<number>} Stores the debounce setTimeout ID */
  const debounceTimer = useRef(null);

  /**
   * Searches for users matching a query string via the API.
   *
   * Wrapped in `useCallback` with `[token]` dependency — the function identity
   * only changes when the auth token changes. This is important if `searchUsers`
   * were ever used in a useEffect dependency array.
   *
   * INTERVIEW TIP: `useCallback` does NOT make the function faster. It only
   * guarantees a stable reference identity across renders. This matters for:
   * - useEffect dependencies (prevents infinite loops)
   * - React.memo'd child components (prevents unnecessary re-renders)
   * - useMemo dependencies
   *
   * @param {string} query - The search term (minimum 1 character)
   * @returns {Promise<void>}
   */
  const searchUsers = useCallback(
    async (query) => {
      if (!token || query.length < 1) {
        setSuggestions([]);
        return;
      }
      try {
        const data = await apiRequest(
          `/users/search?q=${encodeURIComponent(query)}`,
          { headers: getHeaders(token) }
        );
        // Limit to 8 suggestions to keep the dropdown manageable
        setSuggestions(data.slice(0, 8));
      } catch {
        setSuggestions([]);
      }
    },
    [token]
  );

  /**
   * Detects if the cursor is currently inside a @mention.
   *
   * ALGORITHM: Walk backwards from the cursor position to find a `@` character
   * followed by 0-50 valid username characters (alphanumeric + underscore).
   *
   * Uses a regex on the text before the cursor: `/@([A-Za-z0-9_]{0,50})$/`
   * The `$` anchor ensures the match is at the END of the text (i.e., at the cursor).
   *
   * INTERVIEW TIP: This "detect pattern at cursor" approach is common in
   * autocomplete systems. The alternative is tracking the mention state
   * explicitly (setting a flag when @ is typed, clearing it on space/backspace),
   * but regex detection is more robust because it handles edge cases like
   * pasting text, cursor movement via mouse click, etc.
   *
   * @param {string} text - The full textarea value
   * @param {number} cursorPos - The current cursor position (selectionStart)
   * @returns {Object|null} `{query, start}` if inside a mention, null otherwise
   */
  function detectMention(text, cursorPos) {
    const beforeCursor = text.slice(0, cursorPos);
    const match = beforeCursor.match(/@([A-Za-z0-9_]{0,50})$/);
    if (match) {
      return {
        query: match[1],                     // The text after @ (e.g., "ali")
        start: cursorPos - match[0].length,  // Position of the @ character
      };
    }
    return null;
  }

  /**
   * Handles textarea value changes — updates parent state and triggers mention detection.
   *
   * FLOW:
   * 1. Update the parent's controlled value via `onChange(newValue)`
   * 2. Check if the cursor is inside a @mention
   * 3. If yes: start debounced API search
   * 4. If no: close the dropdown and clear suggestions
   *
   * INTERVIEW TIP: This is a "controlled component" — the textarea's value
   * is driven by the `value` prop, and changes go through `onChange`. The
   * component doesn't store its own text; the parent owns the state.
   * This is React's recommended pattern for form inputs.
   *
   * @param {React.ChangeEvent<HTMLTextAreaElement>} e - The change event
   */
  function handleChange(e) {
    const newValue = e.target.value;
    const cursorPos = e.target.selectionStart;
    onChange(newValue);

    const mention = detectMention(newValue, cursorPos);
    if (mention && mention.query.length >= 1) {
      setMentionQuery(mention.query);
      setMentionStart(mention.start);
      setSelectedIndex(0); // Reset to first suggestion on new input

      /*
       * DEBOUNCING: Clear any pending timer, then set a new one.
       *
       * INTERVIEW TIP: This is the raw debounce pattern:
       *   clearTimeout(timer);
       *   timer = setTimeout(fn, delay);
       *
       * If the user types "a", "l", "i" quickly (within 200ms each),
       * only ONE API call fires — for "ali" — after the user pauses.
       * Without debouncing, we'd fire 3 API calls: "a", "al", "ali".
       *
       * The timer ID is stored in a ref (not state) because updating
       * it shouldn't trigger a re-render.
       */
      clearTimeout(debounceTimer.current);
      debounceTimer.current = setTimeout(() => {
        searchUsers(mention.query);
        setShowDropdown(true);
      }, 200);
    } else {
      // No mention detected — close dropdown
      setShowDropdown(false);
      setSuggestions([]);
      setMentionQuery('');
    }
  }

  /**
   * Inserts a selected username into the textarea, replacing the @query text.
   *
   * EXAMPLE: If the textarea is "Hello @ali" and the user selects "alice":
   *   Before: "Hello @ali"
   *   After:  "Hello @alice "  (note the trailing space for easy continued typing)
   *
   * After insertion, the cursor is placed after "@alice " using
   * setSelectionRange(). This requires a setTimeout(0) because React
   * needs to re-render the textarea with the new value first.
   *
   * INTERVIEW TIP: `setTimeout(() => ..., 0)` defers execution until after
   * the current call stack completes. This gives React time to commit the
   * DOM update (re-rendering the textarea with the new value) before we
   * try to set the cursor position. Without this, setSelectionRange would
   * operate on the OLD DOM state.
   *
   * @param {string} username - The selected user's username to insert
   */
  function insertMention(username) {
    const before = value.slice(0, mentionStart);              // Text before the @
    const after = value.slice(mentionStart + mentionQuery.length + 1); // Text after @query (+1 for the @ itself)
    const newValue = `${before}@${username} ${after}`;        // Construct new text
    onChange(newValue);
    setShowDropdown(false);
    setSuggestions([]);

    // Restore focus and set cursor position after React re-renders
    setTimeout(() => {
      if (textareaRef.current) {
        const cursorPos = mentionStart + username.length + 2; // @ + username + space
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(cursorPos, cursorPos);
      }
    }, 0);
  }

  /**
   * Handles keyboard events for mention dropdown navigation AND delegates
   * to the parent's onKeyDown handler when the dropdown isn't consuming the event.
   *
   * KEY BEHAVIOR MATRIX:
   * ┌────────────┬───────────────────────┬──────────────────────────┐
   * │ Key        │ Dropdown OPEN         │ Dropdown CLOSED          │
   * ├────────────┼───────────────────────┼──────────────────────────┤
   * │ ArrowDown  │ Move selection down   │ → externalKeyDown        │
   * │ ArrowUp    │ Move selection up     │ → externalKeyDown        │
   * │ Enter      │ Insert selected user  │ → externalKeyDown        │
   * │ Escape     │ Close dropdown        │ → externalKeyDown        │
   * │ Other keys │ → externalKeyDown     │ → externalKeyDown        │
   * └────────────┴───────────────────────┴──────────────────────────┘
   *
   * INTERVIEW TIP: The `return` statements after handling dropdown keys
   * are critical. They prevent the event from reaching `externalKeyDown`.
   * Without them, pressing Enter to select a mention would ALSO trigger
   * the parent's "send message" action.
   *
   * The `e.preventDefault()` calls on ArrowDown/ArrowUp prevent the
   * browser's default behavior (scrolling the textarea / moving the cursor).
   *
   * The modular arithmetic `(prev + 1) % suggestions.length` wraps the
   * selection index — going past the last item wraps to the first, and
   * vice versa. This is a common "circular navigation" pattern.
   *
   * @param {React.KeyboardEvent} e - The keyboard event
   */
  function handleKeyDown(e) {
    if (showDropdown && suggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        // Circular increment: 0 → 1 → 2 → ... → length-1 → 0
        setSelectedIndex((prev) => (prev + 1) % suggestions.length);
        return; // Consume the event — don't delegate to parent
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        // Circular decrement: 0 → length-1 → length-2 → ... → 1 → 0
        setSelectedIndex((prev) => (prev - 1 + suggestions.length) % suggestions.length);
        return;
      } else if (e.key === 'Enter') {
        e.preventDefault();
        // Insert the currently highlighted suggestion
        insertMention(suggestions[selectedIndex].username);
        return; // CRITICAL: prevents parent's "Enter to send" from firing
      } else if (e.key === 'Escape') {
        setShowDropdown(false);
        return;
      }
    }
    // If the dropdown didn't consume the event, delegate to the parent's handler.
    // This allows the parent to implement its own keyboard shortcuts (e.g., Enter to send).
    if (externalKeyDown) externalKeyDown(e);
  }

  /**
   * Close the dropdown when clicking outside both the textarea and dropdown.
   *
   * INTERVIEW TIP: This is the "click outside to close" pattern, implemented
   * with a document-level mousedown listener. We check if the click target
   * is OUTSIDE both the dropdown and textarea using `contains()`.
   *
   * Why `mousedown` instead of `click`? Because `mousedown` fires before
   * `blur`, which prevents the dropdown from disappearing before the
   * dropdown button's `onMouseDown` can fire (timing issue).
   *
   * The empty dependency array `[]` means this effect runs once on mount
   * and the returned cleanup function runs on unmount. This is the
   * "global event listener" lifecycle pattern.
   */
  useEffect(() => {
    function handleClickOutside(e) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target) &&
        textareaRef.current &&
        !textareaRef.current.contains(e.target)
      ) {
        setShowDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    // Cleanup: remove the event listener when the component unmounts
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  /**
   * Cleanup the debounce timer on unmount.
   *
   * INTERVIEW TIP: This prevents a memory leak. If the component unmounts
   * while a debounce timer is pending, the timer would fire and try to
   * call setSuggestions on an unmounted component (React would warn about
   * "state update on unmounted component"). Clearing the timer prevents this.
   */
  useEffect(() => {
    return () => clearTimeout(debounceTimer.current);
  }, []);

  return (
    <div className="mention-wrapper">
      {/*
        The textarea — a standard controlled textarea enhanced with mention detection.

        INTERVIEW TIP: `ref={textareaRef}` gives us direct DOM access for
        imperative operations (focus, cursor positioning) that React's
        declarative model can't express. The `ref` doesn't cause re-renders.
      */}
      <textarea
        ref={textareaRef}
        className={className || 'input textarea'}
        placeholder={placeholder}
        disabled={disabled}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        rows={rows}
      />

      {/*
        ── Mention Autocomplete Dropdown ────────────────────────────
        Positioned absolutely below the textarea (via CSS `.mention-dropdown`).
        Only visible when there are suggestions AND the dropdown should be shown.

        INTERVIEW TIP: The dropdown buttons use `onMouseDown` with
        `e.preventDefault()` instead of `onClick`. This is because `onClick`
        fires AFTER `blur`, and the textarea's blur event might close the
        dropdown before the click can register. `onMouseDown` fires BEFORE
        blur, so the click always works. This is a well-known gotcha in
        dropdown/autocomplete implementations.
      */}
      {showDropdown && suggestions.length > 0 && (
        <div className="mention-dropdown" ref={dropdownRef}>
          {suggestions.map((user, idx) => (
            <button
              key={user.id}
              className={`mention-item ${idx === selectedIndex ? 'active' : ''}`}
              type="button"
              onMouseDown={(e) => {
                e.preventDefault(); // Prevent textarea blur
                insertMention(user.username);
              }}
              onMouseEnter={() => setSelectedIndex(idx)} // Hover updates selection
            >
              {/* User avatar — same three-case logic as UserIdentity */}
              <div className="mention-avatar">
                {user.username === 'pulse' ? (
                  <img src="/pulse-avatar.svg" alt="pulse" />
                ) : user.avatar_url ? (
                  <img
                    src={
                      user.avatar_url.startsWith('http')
                        ? user.avatar_url
                        : assetUrl(user.avatar_url)
                    }
                    alt={user.username}
                  />
                ) : (
                  // Initials fallback (single letter)
                  <span>{user.username.charAt(0).toUpperCase()}</span>
                )}
              </div>
              <span className="mention-username">@{user.username}</span>
              <span className="mention-role">{user.role}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default MentionTextarea;
