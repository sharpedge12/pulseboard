/**
 * @file main.jsx — Application entry point. Mounts the React tree into the DOM.
 *
 * **Interview topics: ReactDOM.createRoot, StrictMode, provider nesting order.**
 *
 * ### What happens here:
 * 1. `ReactDOM.createRoot()` creates a React 18 concurrent root on the
 *    `<div id="root">` element in `index.html`.
 * 2. `.render()` mounts the entire component tree into that root.
 * 3. The tree is wrapped in several "providers" that supply global state
 *    to all descendants via React Context.
 *
 * ### Provider nesting order matters!
 * Providers are like layers of an onion — inner providers can consume
 * outer providers, but not the reverse.  The order here is:
 *
 * ```
 * StrictMode          — development checks (outermost)
 *   ThemeProvider      — dark/light theme state
 *     AuthProvider     — authentication tokens + user profile
 *       BrowserRouter  — URL-based routing (innermost provider)
 *         App          — route definitions + page components
 * ```
 *
 * - `ThemeProvider` is outermost because the theme wrapper `<div data-theme>`
 *   must surround everything, including the auth UI.
 * - `AuthProvider` is inside `ThemeProvider` so auth-related components
 *   (login page, profile) inherit the correct theme.
 * - `BrowserRouter` is inside `AuthProvider` so route components can call
 *   `useAuth()` to check authentication status.
 *
 * ### React.StrictMode
 * In development, StrictMode intentionally double-invokes certain lifecycle
 * methods (effects, state initialisers) to help detect impure functions and
 * missing cleanup.  It also warns about deprecated APIs.  StrictMode has no
 * effect in production builds — it's purely a development safety net.
 *
 * @module main
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import './styles/global.css';

/*
 * `document.getElementById('root')` finds the empty <div id="root"> in
 * index.html.  `createRoot` (React 18+) enables concurrent features like
 * automatic batching and transitions.  The older `ReactDOM.render()` API
 * is legacy mode and doesn't support these features.
 */
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider>
      <AuthProvider>
        {/*
         * BrowserRouter uses the HTML5 History API (`pushState`,
         * `popstate`) to keep the URL in sync with the UI without full
         * page reloads.  Every `<Link>`, `<NavLink>`, and `useNavigate()`
         * call inside the tree will interact with this router instance.
         *
         * Alternative routers:
         * - HashRouter  — uses `#/path` URLs (no server config needed)
         * - MemoryRouter — keeps history in memory (useful for tests)
         */}
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>
);
