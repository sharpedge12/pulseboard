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

function App() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        {/* Public routes */}
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/threads/:threadId" element={<ThreadPage />} />
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/profile/lookup/:username" element={<ProfileLookupPage />} />
        <Route path="/profile/:userId" element={<ProfilePage />} />

        {/* Authenticated routes */}
        <Route element={<ProtectedRoute />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/profile" element={<ProfilePage />} />
        </Route>

        {/* Staff-only route (admin + moderator) */}
        <Route element={<ProtectedRoute requiredRole="staff" />}>
          <Route path="/admin" element={<AdminPage />} />
        </Route>
      </Route>
    </Routes>
  );
}

export default App;
