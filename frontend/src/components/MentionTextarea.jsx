import { useState, useRef, useEffect, useCallback } from 'react';
import { apiRequest, getHeaders, assetUrl } from '../lib/api';

/**
 * Textarea with @mention autocomplete.
 * Renders a dropdown of matching users when the user types @<query>.
 *
 * Props:
 *   value       - current textarea value
 *   onChange     - (newValue) => void
 *   token       - JWT access token for user search
 *   placeholder - textarea placeholder
 *   disabled    - whether the textarea is disabled
 *   className   - extra class names for the textarea
 */
function MentionTextarea({ value, onChange, token, placeholder, disabled, className, onKeyDown: externalKeyDown, rows }) {
  const [suggestions, setSuggestions] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionStart, setMentionStart] = useState(-1);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const textareaRef = useRef(null);
  const dropdownRef = useRef(null);
  const debounceTimer = useRef(null);

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
        setSuggestions(data.slice(0, 8));
      } catch {
        setSuggestions([]);
      }
    },
    [token]
  );

  function detectMention(text, cursorPos) {
    // Walk backwards from cursor to find an @ that starts a mention
    const beforeCursor = text.slice(0, cursorPos);
    const match = beforeCursor.match(/@([A-Za-z0-9_]{0,50})$/);
    if (match) {
      return {
        query: match[1],
        start: cursorPos - match[0].length,
      };
    }
    return null;
  }

  function handleChange(e) {
    const newValue = e.target.value;
    const cursorPos = e.target.selectionStart;
    onChange(newValue);

    const mention = detectMention(newValue, cursorPos);
    if (mention && mention.query.length >= 1) {
      setMentionQuery(mention.query);
      setMentionStart(mention.start);
      setSelectedIndex(0);

      // Debounce the API call
      clearTimeout(debounceTimer.current);
      debounceTimer.current = setTimeout(() => {
        searchUsers(mention.query);
        setShowDropdown(true);
      }, 200);
    } else {
      setShowDropdown(false);
      setSuggestions([]);
      setMentionQuery('');
    }
  }

  function insertMention(username) {
    const before = value.slice(0, mentionStart);
    const after = value.slice(mentionStart + mentionQuery.length + 1); // +1 for the @
    const newValue = `${before}@${username} ${after}`;
    onChange(newValue);
    setShowDropdown(false);
    setSuggestions([]);

    // Focus back and set cursor position
    setTimeout(() => {
      if (textareaRef.current) {
        const cursorPos = mentionStart + username.length + 2; // @username + space
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(cursorPos, cursorPos);
      }
    }, 0);
  }

  function handleKeyDown(e) {
    if (showDropdown && suggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % suggestions.length);
        return;
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + suggestions.length) % suggestions.length);
        return;
      } else if (e.key === 'Enter') {
        e.preventDefault();
        insertMention(suggestions[selectedIndex].username);
        return;
      } else if (e.key === 'Escape') {
        setShowDropdown(false);
        return;
      }
    }
    // Pass through to external handler if not consumed by mention logic
    if (externalKeyDown) externalKeyDown(e);
  }

  // Close dropdown when clicking outside
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
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Cleanup debounce timer
  useEffect(() => {
    return () => clearTimeout(debounceTimer.current);
  }, []);

  return (
    <div className="mention-textarea-wrapper">
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
      {showDropdown && suggestions.length > 0 && (
        <div className="mention-dropdown" ref={dropdownRef}>
          {suggestions.map((user, idx) => (
            <button
              key={user.id}
              className={`mention-dropdown-item ${idx === selectedIndex ? 'mention-dropdown-item-active' : ''}`}
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                insertMention(user.username);
              }}
              onMouseEnter={() => setSelectedIndex(idx)}
            >
              <div className="mention-dropdown-avatar">
                {user.avatar_url ? (
                  <img
                    src={
                      user.avatar_url.startsWith('http')
                        ? user.avatar_url
                        : assetUrl(user.avatar_url)
                    }
                    alt={user.username}
                  />
                ) : (
                  <span>{user.username.charAt(0).toUpperCase()}</span>
                )}
              </div>
              <div className="mention-dropdown-info">
                <span className="mention-dropdown-username">@{user.username}</span>
                <span className="mention-dropdown-role">{user.role}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default MentionTextarea;
