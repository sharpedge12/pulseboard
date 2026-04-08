/**
 * @file ThemeContext.jsx — Dark/light theme state for the PulseBoard SPA.
 *
 * **Interview topic: CSS theming via data attributes + React Context.**
 *
 * This module is a compact example of the Provider pattern applied to UI
 * theming.  It works in concert with CSS custom properties (design tokens)
 * defined in `global.css`:
 *
 * ```css
 * :root { --color-bg: #030303; }                  // dark (default)
 * [data-theme="light"] { --color-bg: #dae0e6; }   // light override
 * ```
 *
 * ### How it works:
 * 1. The user's theme preference is persisted to `localStorage` via the
 *    custom `useLocalStorage` hook, so it survives browser restarts.
 * 2. `ThemeProvider` renders a `<div data-theme="dark|light">` wrapper
 *    around the entire app.  CSS attribute selectors automatically apply
 *    the correct design tokens based on this attribute.
 * 3. Any component can call `toggleTheme()` (from `useTheme()`) to flip
 *    between dark and light modes — no prop-drilling required.
 *
 * ### Why not just use a CSS class?
 * `data-theme` is a *data attribute*, which is semantically more
 * appropriate than a class for representing application state (as opposed
 * to styling hooks).  It also keeps the theme selector orthogonal to
 * component-specific class names, avoiding naming collisions.
 *
 * @module context/ThemeContext
 */
import { createContext, useContext, useMemo } from 'react';
import { useLocalStorage } from '../hooks/useLocalStorage';

/**
 * The React Context that holds theme state.
 * Initialised to `null` so the guard in `useTheme()` can detect misuse.
 *
 * @type {React.Context<ThemeContextValue | null>}
 */
const ThemeContext = createContext(null);

/**
 * ThemeProvider — wraps the app and provides theme state + toggle function.
 *
 * **Interview note — `useMemo` on the context value:**
 * Same optimisation as in `AuthContext`.  Without `useMemo`, every render
 * of `ThemeProvider` would produce a new `value` object, causing all
 * consumers of `useTheme()` to re-render even if the theme didn't change.
 * `useMemo` stabilises the reference so consumers only re-render when
 * `theme` or `setTheme` actually change.
 *
 * @param {object}          props
 * @param {React.ReactNode} props.children — the component subtree
 * @returns {JSX.Element}
 */
export function ThemeProvider({ children }) {
  /**
   * Persisted theme preference.  Defaults to `'dark'` on first visit.
   * Stored under the localStorage key `'pulseboard-theme'`.
   *
   * @type {['dark'|'light', Function]}
   */
  const [theme, setTheme] = useLocalStorage('pulseboard-theme', 'dark');

  /**
   * Memoised context value — exposed to all consumers via `useTheme()`.
   * @type {ThemeContextValue}
   */
  const value = useMemo(
    () => ({
      /** The current theme string: `'dark'` or `'light'`. */
      theme,
      /** Convenience boolean so consumers don't have to compare strings. */
      isDark: theme === 'dark',
      /**
       * Toggles between dark and light themes.
       *
       * Uses the _functional updater_ form of `setTheme` so it always reads
       * the latest value — important if `toggleTheme` is called rapidly or
       * from a stale closure.
       */
      toggleTheme() {
        setTheme((current) => (current === 'dark' ? 'light' : 'dark'));
      },
      /** Raw setter — allows explicitly setting `'dark'` or `'light'`. */
      setTheme,
    }),
    [theme, setTheme]
  );

  return (
    <ThemeContext.Provider value={value}>
      {/*
       * The `data-theme` attribute on this wrapper div is the bridge
       * between React state and CSS.  The entire subtree inherits the
       * attribute, so any CSS rule like `[data-theme="light"] .card {}`
       * will automatically take effect whenever the theme state changes.
       */}
      <div data-theme={theme}>{children}</div>
    </ThemeContext.Provider>
  );
}

/**
 * useTheme — custom hook to consume theme context.
 *
 * Throws if used outside of `<ThemeProvider>` — same guard pattern as
 * `useAuth()`.  This is considered a best practice for all custom context
 * hooks.
 *
 * @returns {ThemeContextValue}
 * @throws {Error} If called outside of a `<ThemeProvider>`.
 *
 * @example
 * function ThemeToggle() {
 *   const { isDark, toggleTheme } = useTheme();
 *   return <button onClick={toggleTheme}>{isDark ? 'Light' : 'Dark'}</button>;
 * }
 */
export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider');
  }
  return context;
}

/**
 * @typedef {object} ThemeContextValue
 * @property {'dark'|'light'} theme       - Current theme name
 * @property {boolean}        isDark      - True when theme is 'dark'
 * @property {Function}       toggleTheme - Flips between dark and light
 * @property {Function}       setTheme    - Explicit setter for theme
 */
