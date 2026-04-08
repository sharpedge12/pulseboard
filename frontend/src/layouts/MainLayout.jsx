/**
 * @file MainLayout.jsx — Top-level page shell with navbar, nav row, and
 *       content area for the PulseBoard SPA.
 *
 * **Interview topics: Layout routes, <Outlet />, NavLink, responsive design.**
 *
 * ### What is a "layout route"?
 * In React Router v6, a **layout route** is a `<Route>` that has an
 * `element` but no `path` of its own (or a path that serves as a prefix).
 * Its child routes render _inside_ the layout's `<Outlet />`.  This lets
 * you define shared chrome (navbar, sidebar, footer) once and swap only
 * the main content area as the user navigates.
 *
 * In App.jsx you'll see:
 * ```jsx
 * <Route element={<MainLayout />}>
 *   <Route path="/" element={<HomePage />} />
 *   <Route path="/chat" element={<ChatPage />} />
 * </Route>
 * ```
 * `<MainLayout />` renders the navbar + nav-row, and `<Outlet />` renders
 * whichever child route matches the current URL.
 *
 * ### Component responsibilities:
 * - **Navbar**: brand logo, search bar, notification bell, theme toggle,
 *   user avatar / login button.
 * - **Nav row**: horizontal links to main sections + up to 5 community
 *   shortcut links (populated from the API and kept fresh via WebSocket).
 * - **Content area**: `<Outlet />` — replaced by the active child route.
 * - **Notification drawer**: a slide-out panel toggled by the bell icon.
 *
 * @module layouts/MainLayout
 */
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { useNotifications } from '../hooks/useNotifications';
import { useGlobalUpdates } from '../hooks/useGlobalUpdates';
import { apiRequest, assetUrl } from '../lib/api';
import NotificationCenter from '../components/NotificationCenter';

/**
 * MainLayout — the persistent page shell rendered on every route.
 *
 * **Interview note — why is this a layout component, not just App?**
 * Separating layout from route definitions follows the Single Responsibility
 * Principle.  `App.jsx` defines _what_ renders at each URL; `MainLayout`
 * defines _how_ the page is visually structured.  This makes it easy to
 * add alternative layouts (e.g. a full-screen layout for a presentation
 * mode) without touching the routing logic.
 *
 * @returns {JSX.Element}
 */
function MainLayout() {
  /**
   * `useLocation()` gives us the current URL.  We use it to detect
   * whether the user is already on the home page — if not, clicking the
   * search bar redirects there with a `?focus=search` query param so the
   * HomePage component can auto-focus its own search input.
   */
  const location = useLocation();

  /** Controls the open/closed state of the notification slide-out panel. */
  const [isNotificationCenterOpen, setIsNotificationCenterOpen] = useState(false);

  /*
   * Consume global contexts.  Because `useAuth` and `useTheme` are custom
   * hooks that read from React Context, they trigger a re-render of
   * MainLayout whenever the auth/theme state changes — which is exactly
   * what we want (the navbar needs to update when the user logs in/out or
   * toggles the theme).
   */
  const { session, profile, isAuthenticated, isLoadingProfile, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();

  /**
   * `useNotifications` is a custom hook that opens a WebSocket to receive
   * real-time notification events.  It returns the current unread count
   * and a list of recent notifications for the drawer.
   */
  const {
    unreadCount,
    notifications,
    markAllRead,
    markOneRead,
  } = useNotifications(session?.access_token);

  /** Categories fetched from the API for the community shortcut links. */
  const [categories, setCategories] = useState([]);

  /**
   * Fetch all categories on mount for the nav-row community links.
   *
   * **Interview note — the `ignore` flag pattern:**
   * Same cleanup technique as in AuthContext.  If the component unmounts
   * before the fetch completes (e.g. fast navigation away), the cleanup
   * function sets `ignore = true` so we don't call `setCategories` on
   * an unmounted component.
   */
  useEffect(() => {
    let ignore = false;
    async function loadCategories() {
      try {
        const data = await apiRequest('/categories');
        if (!ignore) setCategories(data);
      } catch {
        /* Silently ignore — the nav row just won't show community links. */
      }
    }
    loadCategories();
    return () => { ignore = true; };
  }, []);

  /**
   * Callback to handle real-time "category created" events via WebSocket.
   *
   * **Interview note — `useCallback` for event handlers passed to hooks:**
   * `handleCategoryCreated` is passed to `useGlobalUpdates` which likely
   * registers it inside a `useEffect`.  If this function changed identity
   * on every render (without `useCallback`), that effect would tear down
   * and re-create the WebSocket listener every render.  `useCallback`
   * with `[]` deps ensures a stable reference.
   *
   * Uses the functional updater `setPrev => [...]` to avoid capturing a
   * stale `categories` closure — the updater always receives the latest
   * state value.
   *
   * @param {object} category — the newly-created category from the server
   */
  const handleCategoryCreated = useCallback((category) => {
    setCategories((prev) => {
      // Deduplicate: if this category was already added (e.g. duplicate
      // WebSocket message), return the existing array reference unchanged.
      if (prev.some((c) => c.id === category.id)) return prev;
      return [...prev, category];
    });
  }, []);

  /** Subscribe to global real-time events (new categories, etc.). */
  useGlobalUpdates({ onCategoryCreated: handleCategoryCreated });

  /**
   * Build the navigation items array dynamically based on auth state and
   * the user's role.  This is a common pattern: compute derived data from
   * state, then `.map()` it in JSX.
   *
   * - Home is always shown.
   * - Dashboard is only shown when authenticated.
   * - Admin/Mod link is only shown for staff roles.
   */
  const navItems = [
    { to: '/', label: 'Home' },
    ...(isAuthenticated ? [{ to: '/dashboard', label: 'Dashboard' }] : []),
    { to: '/chat', label: 'Chat' },
    { to: '/people', label: 'People' },
    ...(['admin', 'moderator'].includes(profile?.role)
      ? [{ to: '/admin', label: profile?.role === 'admin' ? 'Admin' : 'Mod' }]
      : []),
  ];

  return (
    <div className="shell">
      {/* ── Top Navbar ─────────────────────────────────────────── */}
      <nav className="navbar">
        {/*
         * NavLink (from React Router) is like <a> but performs client-side
         * navigation without a full page reload.  The `to="/"` prop sets
         * the destination route.
         */}
        <NavLink to="/" className="navbar-brand">
          <img src="/logo.svg" alt="" className="navbar-brand-logo" />
          <span>pulseboard</span>
        </NavLink>

        {/*
         * Search bar — when focused, if the user isn't already on the
         * home page, we navigate there with a query param so the home
         * page can auto-focus its richer search component.
         */}
        <div className="navbar-search">
          <input
            type="text"
            placeholder="Search PulseBoard"
            onFocus={() => {
              if (location.pathname !== '/') {
                window.location.href = '/?focus=search';
              }
            }}
          />
        </div>

        {/* Right-side action buttons */}
        <div className="navbar-actions">
          {/* Notification bell + theme toggle — only shown when logged in */}
          {isAuthenticated && !isLoadingProfile && (
            <>
              {/*
               * Notification bell button with unread badge.
               * The badge caps at "99+" to avoid layout overflow.
               * &#x1F514; is the Unicode bell emoji (🔔).
               */}
              <button
                className="navbar-icon-btn"
                type="button"
                onClick={() => setIsNotificationCenterOpen((c) => !c)}
                title="Notifications"
              >
                &#x1F514;
                {unreadCount > 0 && (
                  <span className="notif-badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
                )}
              </button>
              {/*
               * Theme toggle.  \u2600 = ☀ (sun), \u263E = ☾ (moon).
               * Shows the *opposite* icon so the user knows what they'll
               * switch to.
               */}
              <button
                className="theme-toggle"
                type="button"
                onClick={toggleTheme}
                title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
              >
                {theme === 'dark' ? '\u2600' : '\u263E'}
              </button>
            </>
          )}

          {/*
           * Conditional rendering: three-branch ternary.
           * 1. Authenticated  → show avatar + username + logout button
           * 2. Not loading     → show "Log In" button
           * 3. Still loading   → show nothing (avoids flash of wrong state)
           */}
          {isAuthenticated && !isLoadingProfile ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <NavLink to="/profile" className="navbar-user" title="Profile">
                <span className="navbar-user-avatar">
                  {profile?.avatar_url ? (
                    /*
                     * `assetUrl()` prepends the API host to a relative path
                     * like `/uploads/avatar.png`, producing a full URL the
                     * browser can fetch.  See lib/api.js for details.
                     */
                    <img src={assetUrl(profile.avatar_url)} alt="" />
                  ) : (
                    // Fallback: first letter of username as avatar placeholder.
                    (profile?.username || '?')[0].toUpperCase()
                  )}
                </span>
                <span className="navbar-user-name">{profile?.username}</span>
              </NavLink>
              <button className="navbar-btn" type="button" onClick={logout}>
                Log Out
              </button>
            </div>
          ) : !isLoadingProfile ? (
            <NavLink className="navbar-btn-primary navbar-btn" to="/login">
              Log In
            </NavLink>
          ) : null}

          {/* Theme toggle for unauthenticated users (shown separately
              because the authenticated block above already includes one). */}
          {!isAuthenticated && !isLoadingProfile && (
            <button
              className="theme-toggle"
              type="button"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
            >
              {theme === 'dark' ? '\u2600' : '\u263E'}
            </button>
          )}
        </div>
      </nav>

      {/* ── Horizontal Nav Row ─────────────────────────────────── */}
      <div className="nav-row">
        {/*
         * NavLink's `className` prop can accept a *function* that receives
         * `{ isActive }`.  This lets us apply an "active" CSS class only
         * when the link's `to` path matches the current URL.
         *
         * `end={item.to === '/'}` means the Home link is only "active" on
         * an exact match — without `end`, "/" would match every route
         * because all paths start with "/".
         */}
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              isActive ? 'nav-link nav-link-active' : 'nav-link'
            }
          >
            {item.label}
          </NavLink>
        ))}
        {/*
         * Show up to 5 community shortcut links in the nav row.
         * The `r/` prefix is a Reddit-style convention used by PulseBoard.
         * Navigation uses query params (`?community=slug`) so the HomePage
         * can filter threads by category.
         */}
        {categories.slice(0, 5).map((cat) => (
          <NavLink
            key={cat.slug}
            to={`/?community=${cat.slug}`}
            className="nav-link"
          >
            r/{cat.slug}
          </NavLink>
        ))}
      </div>

      {/* ── Main Content ───────────────────────────────────────── */}
      <main className="content">
        {/*
         * **Interview key concept: <Outlet />**
         *
         * `<Outlet />` is React Router v6's mechanism for nested routing.
         * It acts as a placeholder that renders whichever child `<Route>`
         * matches the current URL.  This is how a single layout component
         * can wrap many different pages without re-mounting the navbar on
         * every navigation.
         *
         * Think of it like `{children}` but controlled by the router.
         */}
        <Outlet />
      </main>

      {/* ── Notification Drawer ────────────────────────────────── */}
      {/*
       * The NotificationCenter is always mounted but only visible when
       * `isOpen` is true (it handles its own show/hide animation).  This
       * is an alternative to conditional rendering (`{isOpen && <Drawer />}`)
       * and is preferred when the component manages internal state or
       * animations that would be lost on unmount.
       */}
      <NotificationCenter
        isOpen={isNotificationCenterOpen}
        notifications={notifications}
        unreadCount={unreadCount}
        markAllRead={markAllRead}
        markOneRead={markOneRead}
        onClose={() => setIsNotificationCenterOpen(false)}
      />
    </div>
  );
}

export default MainLayout;
