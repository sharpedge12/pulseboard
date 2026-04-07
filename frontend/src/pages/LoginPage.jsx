import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';

function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { setSession } = useAuth();
  const [mode, setMode] = useState('login');
  const [form, setForm] = useState({ email: '', username: '', password: '' });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const provider = params.get('provider');
    const code = params.get('code');

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
        setSession(data);
        setMessage(`Signed in with ${provider}.`);
        navigate('/profile');
      } catch (requestError) {
        setError(requestError.message);
      }
    }

    exchangeOAuthCode();
  }, [location.search, navigate, setSession]);

  async function handleOAuthStart(provider) {
    try {
      const data = await apiRequest(`/auth/oauth/${provider}/login`);
      window.location.href = data.authorization_url;
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError('');
    setMessage('');

    try {
      const path = mode === 'login' ? '/auth/login' : '/auth/register';
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
        // Registration no longer returns tokens — just a message
        setMessage(data.message);
        setMode('login');
      } else {
        setSession(data);
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

        <form className="stack-gap" onSubmit={handleSubmit}>
          <input
            className="input"
            type="email"
            placeholder="Email address"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
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
          {mode === 'login' && (
            <Link to="/forgot-password" className="muted-copy" style={{ textAlign: 'center', display: 'block' }}>
              Forgot your password?
            </Link>
          )}
        </form>

        {error && <p className="error-copy">{error}</p>}
        {message && <p className="success-copy">{message}</p>}

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
