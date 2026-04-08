/**
 * @file AuthContext.jsx — Global authentication state for the PulseBoard SPA.
 *
 * **Interview topic: React Context API for global state management.**
 *
 * This module implements the "Provider pattern" — one of the most important
 * architectural patterns in React applications.  It solves the "prop-drilling"
 * problem where authentication data (tokens, user profile, login/logout
 * helpers) would otherwise need to be passed through many intermediate
 * components that don't use the data themselves.
 *
 * ### How the pattern works (three-part recipe):
 * 1. **createContext()**     — creates a "mailbox" that any descendant can read.
 * 2. **AuthProvider**        — the component that _puts data into_ the mailbox.
 * 3. **useAuth() hook**      — a convenience hook that _reads from_ the mailbox.
 *
 * ### Token lifecycle managed here:
 * - On login, the caller stores a `session` object (with `access_token` and
 *   `refresh_token`) into localStorage via a custom `useLocalStorage` hook.
 * - A `useEffect` fires whenever `session` changes and fetches the user's
 *   profile from `/users/me`.  If the token is expired/invalid, the session
 *   is cleared automatically.
 * - `refreshProfile` can also be called imperatively (e.g. after a profile
 *   edit) to re-fetch the latest user data without a full page reload.
 *
 * ### Key React concepts demonstrated:
 * - `useCallback`  — stabilises function identity across re-renders (prevents
 *                     unnecessary effect re-runs and child re-renders).
 * - `useMemo`      — memoises the context value object so consumers only
 *                     re-render when one of its fields actually changes.
 * - `useEffect` cleanup (`ignore` flag) — prevents stale async responses from
 *   overwriting state after the component unmounts or re-renders, a common
 *   source of "Can't perform a React state update on an unmounted component"
 *   warnings.
 *
 * @module context/AuthContext
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { apiRequest, getHeaders } from '../lib/api';
import { useLocalStorage } from '../hooks/useLocalStorage';

/**
 * The React Context object that holds the authentication state.
 *
 * We initialise it with `null` so that if any component tries to consume it
 * _outside_ of an `<AuthProvider>`, the `useAuth()` guard below will throw a
 * helpful error instead of silently returning `undefined`.
 *
 * @type {React.Context<AuthContextValue | null>}
 */
const AuthContext = createContext(null);

/**
 * AuthProvider — wraps the component tree and supplies auth state to all
 * descendants via React Context.
 *
 * **Interview note:** Provider components should be placed as high in the tree
 * as necessary but no higher.  Here it wraps the entire app (see main.jsx)
 * because virtually every page needs to know if the user is logged in.
 *
 * @param {object}          props
 * @param {React.ReactNode} props.children — the rest of the component tree
 * @returns {JSX.Element}
 */
export function AuthProvider({ children }) {
  /**
   * `session` is persisted to localStorage so the user stays logged in across
   * browser tabs and page refreshes.  The custom `useLocalStorage` hook works
   * exactly like `useState` but also serialises the value to/from
   * localStorage under the key `'pulseboard-session'`.
   *
   * Shape: `{ access_token: string, refresh_token: string } | null`
   */
  const [session, setSession] = useLocalStorage('pulseboard-session', null);

  /**
   * The full user profile fetched from `/users/me`.  Kept in regular
   * `useState` (not localStorage) because it can go stale quickly and
   * should be re-fetched from the server on every app load.
   *
   * @type {[object|null, Function]}
   */
  const [profile, setProfile] = useState(null);

  /** True while the initial profile fetch (or a manual refresh) is in flight. */
  const [isLoadingProfile, setIsLoadingProfile] = useState(false);

  /**
   * Fetches the current user's profile from the API.
   *
   * **Interview note — why `useCallback`?**
   * Without `useCallback`, a new function object would be created on every
   * render.  That would cause the `useEffect` below (which lists
   * `refreshProfile` in its dependency array) to re-run every render,
   * creating an infinite loop.  `useCallback` ensures the function identity
   * is stable unless its own dependencies (`session`, `setSession`) change.
   *
   * @param {object} [activeSession] — an override session object.  Useful
   *   when you've _just_ called `setSession(newSession)` and the state
   *   update hasn't been applied yet (React state updates are async).
   *   Passing the new session directly avoids reading the stale closure value.
   * @returns {Promise<object|null>} the user profile, or null on failure
   */
  const refreshProfile = useCallback(async (activeSession) => {
    // Fall back to the current `session` state if no override was provided.
    const sess = activeSession || session;
    if (!sess?.access_token) {
      setProfile(null);
      return null;
    }

    setIsLoadingProfile(true);
    try {
      const data = await apiRequest('/users/me', {
        headers: getHeaders(sess.access_token),
      });
      setProfile(data);
      return data;
    } catch (error) {
      // Token is likely expired or invalid — clear the session
      // so the UI reflects "logged out" immediately.
      setSession(null);
      setProfile(null);
      return null;
    } finally {
      // `finally` guarantees loading state is cleared regardless of success
      // or failure — a defensive pattern you should always use with loading
      // indicators.
      setIsLoadingProfile(false);
    }
  }, [session, setSession]);

  /**
   * Side-effect: Fetch the user profile whenever the session changes.
   *
   * **Interview note — the `ignore` flag pattern:**
   * This is React's recommended way to handle async operations inside
   * `useEffect`.  If the component re-renders (or unmounts) before the
   * fetch completes, the cleanup function sets `ignore = true`, and we
   * skip the stale `setProfile` call.  Without this, a slow network
   * response from an _old_ render could overwrite data from a _newer_
   * render, causing subtle bugs.
   *
   * This pattern is so common that React 18 introduced `use()` and React
   * Query / SWR solve it automatically — but understanding the manual
   * approach is essential for interviews.
   */
  useEffect(() => {
    let ignore = false;

    async function loadProfile() {
      if (!session?.access_token) {
        setProfile(null);
        return;
      }

      try {
        const data = await refreshProfile(session);
        if (!ignore) {
          setProfile(data);
        }
      } catch (error) {
        if (!ignore) {
          setProfile(null);
        }
      }
    }

    loadProfile();

    // Cleanup: if the effect re-fires before the fetch completes, mark
    // this invocation as stale so its response is discarded.
    return () => {
      ignore = true;
    };
  }, [session]);

  /**
   * The context value object, memoised with `useMemo`.
   *
   * **Interview note — why `useMemo` for context values?**
   * Every time `AuthProvider` re-renders, a new object literal `{}` would
   * be created.  React Context uses _reference equality_ to decide whether
   * consumers need to re-render.  A new object (even with the same fields)
   * is a different reference, so every consumer would re-render on every
   * parent render — defeating the purpose of Context optimisation.
   *
   * `useMemo` returns the _same_ object reference as long as the
   * dependency array values haven't changed, preventing unnecessary
   * consumer re-renders.
   *
   * @type {AuthContextValue}
   */
  const value = useMemo(
    () => ({
      /** The raw session object containing access/refresh tokens. */
      session,
      /** Setter for the session — typically called by the login page. */
      setSession,
      /** The current user's profile data from `/users/me`. */
      profile,
      /** Direct setter for profile — used for optimistic local updates. */
      setProfile,
      /** Re-fetches the profile from the server. */
      refreshProfile,
      /** Derived boolean — `true` when an access token is present. */
      isAuthenticated: Boolean(session?.access_token),
      /** True while profile is being fetched. */
      isLoadingProfile,
      /**
       * Logs the user out by clearing both the persisted session (from
       * localStorage) and the in-memory profile.
       */
      logout() {
        setSession(null);
        setProfile(null);
      },
    }),
    [isLoadingProfile, profile, session, setSession, refreshProfile]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * useAuth — custom hook to consume the AuthContext.
 *
 * **Interview note — the guard pattern:**
 * Throwing an error when `context` is null ensures that developers get a
 * clear, actionable message ("useAuth must be used within AuthProvider")
 * instead of a mysterious "Cannot read property 'session' of null" deep
 * inside some child component.  This is a widely-adopted convention for
 * custom context hooks.
 *
 * @returns {AuthContextValue} The authentication state and helpers.
 * @throws {Error} If called outside of an `<AuthProvider>`.
 *
 * @example
 * function MyComponent() {
 *   const { isAuthenticated, profile, logout } = useAuth();
 *   if (!isAuthenticated) return <p>Please log in</p>;
 *   return <button onClick={logout}>Log out {profile.username}</button>;
 * }
 */
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }

  return context;
}

/**
 * @typedef {object} AuthContextValue
 * @property {object|null}   session          - `{ access_token, refresh_token }` or null
 * @property {Function}      setSession       - Updates the persisted session
 * @property {object|null}   profile          - User profile from `/users/me`
 * @property {Function}      setProfile       - Direct profile setter
 * @property {Function}      refreshProfile   - Re-fetches profile from API
 * @property {boolean}       isAuthenticated  - True when a token is present
 * @property {boolean}       isLoadingProfile - True during profile fetch
 * @property {Function}      logout           - Clears session and profile
 */
