import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

/**
 * Route guard that redirects unauthenticated users to /login.
 * Optionally restricts access by role (e.g. 'staff' for admin + moderator).
 *
 * Usage:
 *   <Route element={<ProtectedRoute />}>
 *     <Route path="/dashboard" element={<DashboardPage />} />
 *   </Route>
 *
 *   <Route element={<ProtectedRoute requiredRole="staff" />}>
 *     <Route path="/admin" element={<AdminPage />} />
 *   </Route>
 */

function ProtectedRoute({ requiredRole }) {
  const { isAuthenticated, isLoadingProfile, profile } = useAuth();
  const location = useLocation();

  // While the profile is still loading, show nothing to avoid a flash redirect
  if (isLoadingProfile) {
    return null;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // If a role is required, wait for profile to load then check
  if (requiredRole && profile && profile.role !== requiredRole) {
    // Moderators can also access admin (staff) pages
    if (requiredRole === 'staff' && ['admin', 'moderator'].includes(profile.role)) {
      return <Outlet />;
    }
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}

export default ProtectedRoute;
