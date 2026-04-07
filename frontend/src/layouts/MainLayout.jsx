import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { useNotifications } from '../hooks/useNotifications';
import { useGlobalUpdates } from '../hooks/useGlobalUpdates';
import { apiRequest, assetUrl } from '../lib/api';
import NotificationCenter from '../components/NotificationCenter';

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
  } = useNotifications(session?.access_token);

  const [categories, setCategories] = useState([]);

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
      {/* ── Top Navbar ── */}
      <nav className="navbar">
        <NavLink to="/" className="navbar-brand">
          <img src="/logo.svg" alt="" className="navbar-brand-logo" />
          <span>pulseboard</span>
        </NavLink>

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

        <div className="navbar-actions">
          {isAuthenticated && !isLoadingProfile && (
            <>
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

          {isAuthenticated && !isLoadingProfile ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <NavLink to="/profile" className="navbar-user" title="Profile">
                <span className="navbar-user-avatar">
                  {profile?.avatar_url ? (
                    <img src={assetUrl(profile.avatar_url)} alt="" />
                  ) : (
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

      {/* ── Horizontal Nav Row ── */}
      <div className="nav-row">
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

      {/* ── Main Content ── */}
      <main className="content">
        <Outlet />
      </main>

      {/* ── Notification Drawer ── */}
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
