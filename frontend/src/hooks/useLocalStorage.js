/**
 * @fileoverview useLocalStorage — Custom hook for syncing React state with localStorage.
 *
 * This hook provides a `useState`-like API that automatically persists its value
 * to `window.localStorage`. The value is serialized as JSON when writing and
 * deserialized when reading.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Lazy initialization**: The `useState` initializer is a function (lazy init)
 *     that reads from localStorage only once on mount. This avoids reading from
 *     localStorage on every render, which would be a performance issue since
 *     localStorage is a synchronous, blocking API.
 *   - **Serialization**: Values are stored as JSON strings. This means the hook
 *     works with any JSON-serializable value (objects, arrays, strings, numbers,
 *     booleans), but NOT with functions, Symbols, or circular references.
 *   - **Error resilience**: The try/catch in the initializer handles cases where
 *     localStorage contains invalid JSON (e.g., corrupted data or data written by
 *     a different version of the app). In that case, it falls back to `initialValue`.
 *   - **Sync on key change**: The `useEffect` dependency on `[key, value]` ensures
 *     that if the key changes (rare but possible in dynamic scenarios), the new
 *     key gets the current value written to it.
 *   - **Cross-tab sync**: Note that this hook does NOT listen for `storage` events,
 *     so changes made in another browser tab won't be reflected. For cross-tab
 *     sync, you would add a `window.addEventListener('storage', ...)` listener.
 *
 * Usage:
 *   const [theme, setTheme] = useLocalStorage('theme', 'dark');
 *
 * @module hooks/useLocalStorage
 */

import { useEffect, useState } from 'react';

/**
 * Custom hook that syncs a state value with localStorage.
 *
 * @template T
 * @param {string} key - The localStorage key to read/write.
 * @param {T} initialValue - Default value if the key doesn't exist in localStorage.
 * @returns {[T, React.Dispatch<React.SetStateAction<T>>]} A tuple of [value, setValue],
 *   identical to the useState API.
 *
 * @example
 * const [theme, setTheme] = useLocalStorage('theme', 'dark');
 * setTheme('light'); // Updates state AND writes to localStorage
 */
export function useLocalStorage(key, initialValue) {
  /**
   * Lazy initialization function for useState.
   * Reads from localStorage once on mount. If the key doesn't exist or the
   * stored value is invalid JSON, falls back to the provided initialValue.
   */
  const [value, setValue] = useState(() => {
    try {
      const storedValue = window.localStorage.getItem(key);
      return storedValue ? JSON.parse(storedValue) : initialValue;
    } catch (error) {
      // Invalid JSON in localStorage — fall back to default
      return initialValue;
    }
  });

  /**
   * Persist the value to localStorage whenever it changes.
   * Also re-writes if the key changes (ensures the correct key is always in sync).
   */
  useEffect(() => {
    window.localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);

  return [value, setValue];
}
