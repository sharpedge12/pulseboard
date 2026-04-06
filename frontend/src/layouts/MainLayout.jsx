import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { useNotifications } from '../hooks/useNotifications';
import { useGlobalUpdates } from '../hooks/useGlobalUpdates';
import { apiRequest } from '../lib/api';
import NotificationCenter from '../components/NotificationCenter';

const NAV_ICONS = {
  '/': '\u2302',
  '/dashboard': '\u2261',
  '/chat': '\u2709',
  '/people': '\u2603',
  '/profile': '\u263A',
  '/admin': '\u2699',
};

function MainLayout() {
  const location = useLocation();
  const [isNotificationCenterOpen, setIsNotificationCenterOpen] = useState(false);
  const { session, profile, isAuthenticated, isLoadingProfile, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const {
    unreadCount,
    notifications,
    markAllRead,
    markOneRead,
    browserPermission,
    requestBrowserPermission,
  } = useNotifications(session?.access_token);

  const [categories, setCategories] = useState([]);
  const [pinnedSlugs, setPinnedSlugs] = useLocalStorage('pulseboard-pinned-communities', []);

  useEffect(() => {
    let ignore = false;
    async function loadCategories() {
      try {
        const data = await apiRequest('/categories');
        if (!ignore) setCategories(data);
      } catch {
        /* ignore */
      }
    }
    loadCategories();
    return () => { ignore = true; };
  }, []);

  // Real-time: add new communities as they are created
  const handleCategoryCreated = useCallback((category) => {
    setCategories((prev) => {
      if (prev.some((c) => c.id === category.id)) return prev;
      return [...prev, category];
    });
  }, []);

  useGlobalUpdates({ onCategoryCreated: handleCategoryCreated });

  function togglePin(slug) {
    setPinnedSlugs((current) =>
      current.includes(slug)
        ? current.filter((s) => s !== slug)
        : [...current, slug]
    );
  }

  const pinnedCategories = categories.filter((cat) => pinnedSlugs.includes(cat.slug));
  const isForumActive = location.pathname === '/' || location.pathname.startsWith('/threads');

  const navItems = [
    { to: '/', label: 'Forum' },
    ...(isAuthenticated ? [{ to: '/dashboard', label: 'Dashboard' }] : []),
    { to: '/chat', label: 'Chat' },
    { to: '/people', label: 'People' },
    { to: '/profile', label: 'Profile' },
    ...(['admin', 'moderator'].includes(profile?.role)
      ? [{ to: '/admin', label: profile?.role === 'admin' ? 'Admin' : 'Moderation' }]
      : []),
  ];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">PulseBoard</p>
          <h1>Discussion forum for teams.</h1>
        </div>

        <nav className="nav">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                isActive ? 'nav-link nav-link-active' : 'nav-link'
              }
            >
              <span>{NAV_ICONS[item.to] || '\u25CF'}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Pinned communities — show when on Forum pages */}
        {isForumActive && categories.length > 0 && (
          <div className="sidebar-pinned-section">
            <span className="card-label">Pinned Communities</span>
            {pinnedCategories.length === 0 && (
              <p className="muted-copy" style={{ fontSize: 'var(--text-xs)' }}>
                No pinned communities. Click the pin icon below to add some.
              </p>
            )}
            {pinnedCategories.map((cat) => (
              <NavLink
                key={cat.slug}
                to={`/?community=${cat.slug}`}
                className="pinned-community-link"
              >
                <span>r/{cat.slug}</span>
                <button
                  className="pin-button pin-button-active"
                  type="button"
                  title="Unpin community"
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); togglePin(cat.slug); }}
                >
                  &#x2716;
                </button>
              </NavLink>
            ))}

            <details className="pin-selector">
              <summary className="pin-selector-toggle">
                Manage pins
              </summary>
              <div className="pin-selector-list">
                {categories.map((cat) => (
                  <button
                    key={cat.slug}
                    className={`pin-selector-item ${pinnedSlugs.includes(cat.slug) ? 'pin-selector-item-active' : ''}`}
                    type="button"
                    onClick={() => togglePin(cat.slug)}
                  >
                    <span>r/{cat.slug}</span>
                    <span className="pin-icon">
                      {pinnedSlugs.includes(cat.slug) ? '\u2713' : '\u002B'}
                    </span>
                  </button>
                ))}
              </div>
            </details>
          </div>
        )}

        <button
          className="theme-toggle"
          type="button"
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          <span>{theme === 'dark' ? '\u2600' : '\u263E'}</span>
          {theme === 'dark' ? 'Light mode' : 'Dark mode'}
        </button>

        <div className="sidebar-card">
          <strong>
            {profile
              ? `${profile.username}`
              : 'Sign in to get started'}
          </strong>
          <span>
            {profile
              ? profile.role
              : 'Discussion forum for teams'}
          </span>
        </div>
      </aside>

      <main className="content">
        <header className="topbar">
          <h2>PulseBoard</h2>
          <div className="topbar-actions">
            {isAuthenticated && !isLoadingProfile && (
              <>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setIsNotificationCenterOpen((c) => !c)}
                >
                  Notifications{unreadCount > 0 ? ` (${unreadCount})` : ''}
                </button>
                {browserPermission !== 'granted' && (
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={requestBrowserPermission}
                  >
                    Enable alerts
                  </button>
                )}
              </>
            )}
            {isAuthenticated && !isLoadingProfile ? (
              <button className="action-button" type="button" onClick={logout}>
                Sign out
              </button>
            ) : !isLoadingProfile ? (
              <NavLink className="action-button" to="/login">
                Sign in
              </NavLink>
            ) : null}
          </div>
        </header>

        <Outlet />
      </main>

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
