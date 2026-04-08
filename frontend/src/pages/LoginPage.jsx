/**
 * @fileoverview LoginPage — Authentication page with login, registration, and OAuth.
 *
 * This page handles three authentication flows:
 *   1. **Email/password login**: User enters email and password, receives JWT tokens.
 *   2. **Email/password registration**: User creates an account (must verify email
 *      before logging in). After registration, switches to login mode automatically.
 *   3. **OAuth (Google/GitHub)**: Initiates an OAuth flow by redirecting to the
 *      provider's authorization URL. After the user grants access, the provider
 *      redirects back to this page with `?provider=...&code=...` query params,
 *      which are exchanged for JWT tokens via the backend.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **OAuth redirect flow**: The OAuth process spans two page loads:
 *       a. User clicks "Continue with Google" -> `handleOAuthStart()` fetches the
 *          authorization URL from the backend and redirects the browser.
 *       b. After granting access, the provider redirects back with `?provider=google&code=...`.
 *       c. The `useEffect` detects these query params and calls `/auth/oauth/exchange`
 *          to trade the authorization code for JWT tokens.
 *     This is the standard OAuth 2.0 Authorization Code flow.
 *   - **Mode toggle (login/register)**: A single form handles both modes. The
 *     `mode` state switches between 'login' and 'register', conditionally showing
 *     the username field and changing the API endpoint and button text.
 *   - **Post-registration flow**: Registration returns a message (not tokens),
 *     because the user must verify their email first. The component switches to
 *     login mode and displays the server's message.
 *   - **Session management**: `setSession(data)` stores JWT tokens in the global
 *     AuthContext, which persists them to localStorage and updates all components
 *     that depend on authentication state.
 *
 * @module pages/LoginPage
 */

import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';

/**
 * LoginPage component — renders the auth form with login/register toggle and OAuth buttons.
 *
 * @returns {JSX.Element}
 */
function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { setSession } = useAuth();

  // ── Form state ──
  const [mode, setMode] = useState('login');     // 'login' or 'register'
  const [form, setForm] = useState({ email: '', username: '', password: '' });
  const [error, setError] = useState('');         // Error message from API
  const [message, setMessage] = useState('');     // Success/info message

  /**
   * OAuth callback handler.
   *
   * When the OAuth provider redirects back to this page with `?provider=...&code=...`,
   * this effect exchanges the authorization code for JWT tokens via the backend.
   *
   * Dependencies: location.search (triggers when the URL query params change),
   * navigate (for redirecting after successful auth), setSession (to store tokens).
   */
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const provider = params.get('provider');
    const code = params.get('code');

    // Only run if we have both provider and code (OAuth callback)
    if (!provider || !code) {
      return;
    }

    async function exchangeOAuthCode() {
      try {
        const data = await apiRequest('/auth/oauth/exchange', {
          method: 'POST',
          headers: getHeaders(),
          body: JSON.stringify({ provider, code, state: params.get('state') }),
        });
        setSession(data); // Store JWT tokens in AuthContext
        setMessage(`Signed in with ${provider}.`);
        navigate('/profile');
      } catch (requestError) {
        setError(requestError.message);
      }
    }

    exchangeOAuthCode();
  }, [location.search, navigate, setSession]);

  /**
   * Initiates an OAuth flow by fetching the provider's authorization URL
   * from the backend and redirecting the browser to it.
   *
   * @param {'google'|'github'} provider - The OAuth provider to use.
   */
  async function handleOAuthStart(provider) {
    try {
      const data = await apiRequest(`/auth/oauth/${provider}/login`);
      // Full page redirect to the OAuth provider's authorization page
      window.location.href = data.authorization_url;
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  /**
   * Handles email/password login or registration form submission.
   *
   * Login: POST to /auth/login, receives JWT tokens, redirects to home.
   * Register: POST to /auth/register, receives a message (must verify email),
   *           then auto-switches to login mode.
   *
   * @param {Event} event - The form submit event.
   */
  async function handleSubmit(event) {
    event.preventDefault();
    setError('');
    setMessage('');

    try {
      const path = mode === 'login' ? '/auth/login' : '/auth/register';
      // Login only sends email+password; register also sends username
      const payload =
        mode === 'login'
          ? { email: form.email, password: form.password }
          : form;
      const data = await apiRequest(path, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      });

      if (mode === 'register') {
        // Registration returns a message, not tokens (email verification required)
        setMessage(data.message);
        setMode('login'); // Switch to login mode so user can log in after verifying
      } else {
        setSession(data); // Store JWT tokens
        setMessage('Signed in successfully.');
        navigate('/');
      }
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return (
    <section className="page-grid auth-centered">
      <div className="panel stack-gap">
        <h3>{mode === 'login' ? 'Sign in' : 'Create account'}</h3>

        {/* Mode toggle pills: Login / Register */}
        <div className="pill-row">
          <button
            className={mode === 'login' ? 'pill pill-active' : 'pill'}
            type="button"
            onClick={() => setMode('login')}
          >
            Login
          </button>
          <button
            className={mode === 'register' ? 'pill pill-active' : 'pill'}
            type="button"
            onClick={() => setMode('register')}
          >
            Register
          </button>
        </div>

        {/* Email/password form */}
        <form className="stack-gap" onSubmit={handleSubmit}>
          <input
            className="input"
            type="email"
            placeholder="Email address"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
          {/* Username field — only shown in register mode */}
          {mode === 'register' && (
            <input
              className="input"
              type="text"
              placeholder="Username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          )}
          <input
            className="input"
            type="password"
            placeholder="Password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
          <button className="action-button" type="submit">
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
          {/* Forgot password link — only shown in login mode */}
          {mode === 'login' && (
            <Link to="/forgot-password" className="muted-copy" style={{ textAlign: 'center', display: 'block' }}>
              Forgot your password?
            </Link>
          )}
        </form>

        {error && <p className="error-copy">{error}</p>}
        {message && <p className="success-copy">{message}</p>}

        {/* OAuth section — separated by a divider line */}
        <div
          style={{
            borderTop: '1px solid var(--color-border-muted)',
            paddingTop: 'var(--space-4)',
            marginTop: 'var(--space-2)',
          }}
        >
          <p className="muted-copy" style={{ marginBottom: 'var(--space-3)' }}>
            Or continue with
          </p>
          <div className="stack-gap">
            <button
              className="secondary-button"
              type="button"
              onClick={() => handleOAuthStart('google')}
            >
              Continue with Google
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => handleOAuthStart('github')}
            >
              Continue with GitHub
            </button>
          </div>
        </div>

        <p className="muted-copy">
          After creating an account you must verify your email before you can
          log in. Check your inbox for the verification link.
        </p>
      </div>
    </section>
  );
}

export default LoginPage;
