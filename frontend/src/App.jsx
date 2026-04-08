/**
 * @file App.jsx — Route definitions for the PulseBoard SPA.
 *
 * **Interview topics: React Router v6 patterns — layout routes, nested
 * routes, protected routes, and dynamic segments.**
 *
 * ### Route architecture overview:
 * ```
 * <MainLayout>              ← layout route (always rendered, provides navbar)
 * ├── /                     ← HomePage           (public)
 * ├── /login                ← LoginPage          (public)
 * ├── /forgot-password      ← ForgotPasswordPage (public)
 * ├── /reset-password       ← ResetPasswordPage  (public)
 * ├── /verify-email         ← VerifyEmailPage    (public)
 * ├── /threads/:threadId    ← ThreadPage         (public, :threadId is dynamic)
 * ├── /people               ← PeoplePage         (public)
 * ├── /profile/lookup/:user ← ProfileLookupPage  (public)
 * ├── /profile/:userId      ← ProfilePage        (public view)
 * │
 * ├── <ProtectedRoute>      ← auth guard (redirects to /login if not logged in)
 * │   ├── /dashboard        ← DashboardPage
 * │   ├── /chat             ← ChatPage
 * │   └── /profile          ← ProfilePage (own profile, no :userId)
 * │
 * └── <ProtectedRoute requiredRole="staff">  ← role guard (admin + moderator)
 *     └── /admin            ← AdminPage
 * ```
 *
 * ### Key React Router v6 concepts:
 *
 * 1. **Layout routes** — a `<Route>` with an `element` but child routes
 *    inside it.  The element renders `<Outlet />` where children appear.
 *    Here, `<MainLayout />` is the layout route for the entire app.
 *
 * 2. **Nested routes** — child `<Route>` elements inherit their parent's
 *    path prefix.  `<Route element={<ProtectedRoute />}>` has no `path`,
 *    so its children keep their own absolute paths.
 *
 * 3. **Dynamic segments** — `:threadId` in `/threads/:threadId` captures a
 *    URL segment as a parameter.  The page component reads it with
 *    `useParams()`:  `const { threadId } = useParams();`
 *
 * 4. **Index routes** — a route with no `path` (or `index` prop) matches
 *    the parent's exact path.  We don't use `index` here because each
 *    route has an explicit `path`.
 *
 * 5. **Protected routes** — `<ProtectedRoute>` is a wrapper component that
 *    checks auth state.  If the user is not authenticated (or doesn't have
 *    the required role), it redirects to `/login` instead of rendering its
 *    child `<Outlet />`.
 *
 * @module App
 */
import { Route, Routes } from 'react-router-dom';
import MainLayout from './layouts/MainLayout';
import ProtectedRoute from './components/ProtectedRoute';
import AdminPage from './pages/AdminPage';
import ChatPage from './pages/ChatPage';
import DashboardPage from './pages/DashboardPage';
import HomePage from './pages/HomePage';
import LoginPage from './pages/LoginPage';
import { ForgotPasswordPage, ResetPasswordPage } from './pages/PasswordResetPages';
import PeoplePage from './pages/PeoplePage';
import ProfilePage from './pages/ProfilePage';
import ProfileLookupPage from './pages/ProfileLookupPage';
import ThreadPage from './pages/ThreadPage';
import VerifyEmailPage from './pages/VerifyEmailPage';

/**
 * App — the root component that defines all routes.
 *
 * **Interview note — `<Routes>` vs `<Switch>` (v5):**
 * React Router v6 replaced `<Switch>` with `<Routes>`.  The key difference
 * is that `<Routes>` automatically picks the _best_ match (most specific),
 * while `<Switch>` matched top-to-bottom and required careful ordering.
 * This means you no longer need to worry about putting `/threads/new`
 * before `/threads/:id` — v6 handles it correctly.
 *
 * @returns {JSX.Element}
 */
function App() {
  return (
    <Routes>
      {/*
       * Layout route: wraps ALL pages in MainLayout (navbar + footer).
       * Notice there's no `path` prop — it matches every URL and delegates
       * to child routes for the actual content (via <Outlet />).
       */}
      <Route element={<MainLayout />}>

        {/* ── Public routes ─────────────────────────────────────── */}
        {/* Accessible to everyone, including unauthenticated visitors. */}
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />

        {/*
         * Dynamic route: `:threadId` is a URL parameter.
         * When a user visits `/threads/42`, the ThreadPage component can
         * extract `42` via: `const { threadId } = useParams();`
         */}
        <Route path="/threads/:threadId" element={<ThreadPage />} />
        <Route path="/people" element={<PeoplePage />} />

        {/*
         * Two profile routes with different dynamic segments:
         * - `/profile/lookup/:username` — look up any user by username
         * - `/profile/:userId`          — view any user by numeric ID
         */}
        <Route path="/profile/lookup/:username" element={<ProfileLookupPage />} />
        <Route path="/profile/:userId" element={<ProfilePage />} />

        {/* ── Authenticated routes ──────────────────────────────── */}
        {/*
         * `<ProtectedRoute />` is a layout-like component that checks
         * `useAuth().isAuthenticated`.  If the user is not logged in, it
         * renders a `<Navigate to="/login" />` redirect instead of
         * `<Outlet />`.  This is the standard v6 pattern for auth guards.
         *
         * Because it's a Route with no `path`, it doesn't add any URL
         * prefix — its children keep their own absolute paths.
         */}
        <Route element={<ProtectedRoute />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/chat" element={<ChatPage />} />
          {/* Own profile (no :userId param) — only accessible when logged in */}
          <Route path="/profile" element={<ProfilePage />} />
        </Route>

        {/* ── Staff-only route (admin + moderator) ──────────────── */}
        {/*
         * `requiredRole="staff"` tells ProtectedRoute to also verify the
         * user's role.  If they're authenticated but not admin/moderator,
         * they'll be redirected rather than shown the admin panel.
         */}
        <Route element={<ProtectedRoute requiredRole="staff" />}>
          <Route path="/admin" element={<AdminPage />} />
        </Route>
      </Route>
    </Routes>
  );
}

export default App;
