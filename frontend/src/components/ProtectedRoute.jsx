/**
 * @file ProtectedRoute.jsx
 * @description Route guard component that restricts access to authenticated
 *              (and optionally role-authorized) users.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Route Guard / Protected Route Pattern** — This is one of the most
 *    commonly asked React Router patterns in interviews. The component
 *    wraps child routes and either renders them (via `<Outlet />`) or
 *    redirects to login. It acts as a gatekeeper in the route tree.
 *
 * 2. **Higher-Order Component (HOC) Concept** — While this isn't technically
 *    a HOC (it doesn't wrap a component function), it serves the same purpose:
 *    adding authentication behavior to existing routes without modifying them.
 *    In React Router v6, "layout routes" with `<Outlet />` replace the older
 *    HOC pattern (`withAuth(Component)`).
 *
 * 3. **Redirect with Location State** — `<Navigate to="/login" state={{ from: location }} />`
 *    passes the current location to the login page. After login, the app can
 *    read `location.state.from` and redirect the user back to where they were.
 *    This is the standard "redirect after login" UX pattern.
 *
 * 4. **Loading State Handling** — `isLoadingProfile` prevents a "flash redirect".
 *    On initial page load, the profile hasn't been fetched yet, so
 *    `isAuthenticated` is temporarily false. Without the loading check, the
 *    user would be briefly redirected to /login before the auth check completes.
 *    Returning `null` during loading shows a blank screen (or you could show
 *    a spinner).
 *
 * 5. **Role-Based Access Control (RBAC)** — The optional `requiredRole` prop
 *    enables role-based restrictions. The "staff" role is a virtual role that
 *    matches both "admin" and "moderator", demonstrating how to implement
 *    role hierarchies without a formal RBAC library.
 *
 * @example
 * // In your route configuration (App.jsx or similar):
 *
 * // Basic auth protection — any logged-in user can access:
 * <Route element={<ProtectedRoute />}>
 *   <Route path="/dashboard" element={<DashboardPage />} />
 *   <Route path="/chat" element={<ChatPage />} />
 * </Route>
 *
 * // Role-based protection — only admin and moderator:
 * <Route element={<ProtectedRoute requiredRole="staff" />}>
 *   <Route path="/admin" element={<AdminPage />} />
 * </Route>
 *
 * @see {@link https://reactrouter.com/en/main/components/outlet} React Router Outlet
 * @see {@link https://reactrouter.com/en/main/components/navigate} React Router Navigate
 */

import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

/**
 * ProtectedRoute — A layout route component that conditionally renders child
 * routes based on authentication and role requirements.
 *
 * DECISION TREE:
 * ┌──────────────────────────────────┐
 * │ Is profile still loading?         │
 * │   YES → render null (blank)       │
 * │   NO  ↓                           │
 * ├──────────────────────────────────┤
 * │ Is user authenticated?            │
 * │   NO  → <Navigate to="/login" />  │
 * │   YES ↓                           │
 * ├──────────────────────────────────┤
 * │ Is a role required?               │
 * │   NO  → <Outlet /> (render kids)  │
 * │   YES ↓                           │
 * ├──────────────────────────────────┤
 * │ Does user have the required role? │
 * │   YES → <Outlet />                │
 * │   NO  → <Navigate to="/" />       │
 * └──────────────────────────────────┘
 *
 * @param {Object} props
 * @param {string} [props.requiredRole] - Optional role requirement:
 *   - "admin"     → only admin users
 *   - "moderator" → only moderator users
 *   - "staff"     → admin OR moderator users (virtual role)
 *   - undefined   → any authenticated user
 * @returns {JSX.Element|null} `<Outlet />` (child routes), `<Navigate />` (redirect), or null (loading)
 */
function ProtectedRoute({ requiredRole }) {
  /**
   * Destructure auth state from context.
   *
   * INTERVIEW TIP: Three values are needed:
   * - `isAuthenticated`: boolean derived from session existence
   * - `isLoadingProfile`: true while the initial profile fetch is in-flight
   * - `profile`: the full user profile object (has `.role` property)
   */
  const { isAuthenticated, isLoadingProfile, profile } = useAuth();

  /**
   * Get the current location for the "redirect after login" feature.
   *
   * INTERVIEW TIP: `useLocation()` returns the current URL as an object:
   * { pathname: "/admin", search: "", hash: "", state: null, key: "..." }
   * We pass this to <Navigate state={{ from: location }}> so the login
   * page can redirect back here after successful authentication.
   */
  const location = useLocation();

  /*
   * LOADING STATE: Show nothing while the profile is being fetched.
   *
   * INTERVIEW TIP: This prevents the "flash redirect" problem.
   * On initial page load:
   *   1. App renders, isAuthenticated = false (no profile yet)
   *   2. Without this check: <Navigate to="/login"> fires immediately
   *   3. User sees a brief flash of the login page
   *   4. Profile fetch completes, isAuthenticated = true
   *   5. Login page detects auth and redirects back → flickering UX
   *
   * By returning null during loading, we show a blank screen for a
   * fraction of a second while the auth state is determined. A production
   * app might show a loading spinner here instead.
   */
  if (isLoadingProfile) {
    return null;
  }

  /*
   * AUTH CHECK: Redirect unauthenticated users to the login page.
   *
   * INTERVIEW TIP: `<Navigate>` is the declarative redirect in React
   * Router v6. It replaces the older `<Redirect>` from v5.
   *
   * Key props:
   * - `to="/login"` — the redirect destination
   * - `state={{ from: location }}` — passes the current URL to login page
   *   so it can redirect back after successful auth
   * - `replace` — replaces the current history entry (so pressing "Back"
   *   after login goes to the page BEFORE the protected route, not back
   *   to the redirect itself — prevents redirect loops)
   */
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  /*
   * ROLE CHECK: If a role is required, verify the user has permission.
   *
   * INTERVIEW TIP: The "staff" virtual role is a simple role hierarchy:
   *   staff = admin OR moderator
   *
   * This avoids duplicating route declarations for admin and moderator.
   * A more complex app might use a permission system instead:
   *   requiredPermission="manage_reports"
   * But role-based checks are simpler and sufficient for most apps.
   *
   * Note: `profile && profile.role !== requiredRole` includes a null
   * check on profile. If profile is null (shouldn't happen if
   * isAuthenticated is true, but defensive coding), we skip the role
   * check and fall through to <Outlet />.
   */
  if (requiredRole && profile && profile.role !== requiredRole) {
    // Special case: "staff" role accepts both admin and moderator
    if (requiredRole === 'staff' && ['admin', 'moderator'].includes(profile.role)) {
      return <Outlet />;
    }
    // Role mismatch — redirect to home page (not login, since they ARE logged in)
    return <Navigate to="/" replace />;
  }

  /*
   * ALL CHECKS PASSED — Render the child routes.
   *
   * INTERVIEW TIP: `<Outlet />` is React Router v6's mechanism for
   * "layout routes". When ProtectedRoute is used as a layout:
   *
   *   <Route element={<ProtectedRoute />}>
   *     <Route path="/dashboard" element={<DashboardPage />} />
   *   </Route>
   *
   * The <Outlet /> renders <DashboardPage /> (or whatever child route matches).
   * This is similar to `{children}` in regular React components, but
   * specifically for route trees. It's the v6 replacement for render props
   * and the `children` function pattern from v5.
   */
  return <Outlet />;
}

export default ProtectedRoute;
