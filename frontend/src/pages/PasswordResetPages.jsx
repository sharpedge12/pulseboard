import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { apiRequest, getHeaders } from '../lib/api';

function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError('');
    setMessage('');

    try {
      const data = await apiRequest('/auth/forgot-password', {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({ email }),
      });
      setMessage(data.message);
      setSubmitted(true);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return (
    <section className="page-grid auth-centered">
      <div className="panel stack-gap">
        <div className="panel-header">
          <h3>Forgot Password</h3>
        </div>

        {!submitted ? (
          <form className="stack-gap" onSubmit={handleSubmit}>
            <p className="muted-copy">
              Enter your email address and we will send you a link to reset your password.
            </p>
            <input
              className="input"
              type="email"
              placeholder="Email address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <button className="action-button" type="submit">
              Send reset link
            </button>
          </form>
        ) : (
          <p className="success-copy">{message}</p>
        )}

        {error && <p className="error-copy">{error}</p>}
      </div>
    </section>
  );
}

function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function handleSubmit(event) {
    event.preventDefault();
    setError('');
    setMessage('');

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    if (!token) {
      setError('Missing reset token. Please use the link from your email.');
      return;
    }

    try {
      const data = await apiRequest('/auth/reset-password', {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({ token, new_password: newPassword }),
      });
      setMessage(data.message);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return (
    <section className="page-grid auth-centered">
      <div className="panel stack-gap">
        <div className="panel-header">
          <h3>Reset Password</h3>
        </div>

        <form className="stack-gap" onSubmit={handleSubmit}>
          <input
            className="input"
            type="password"
            placeholder="New password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={8}
          />
          <input
            className="input"
            type="password"
            placeholder="Confirm new password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
          />
          <button className="action-button" type="submit">
            Reset password
          </button>
        </form>

        {error && <p className="error-copy">{error}</p>}
        {message && <p className="success-copy">{message}</p>}
      </div>
    </section>
  );
}

export { ForgotPasswordPage, ResetPasswordPage };
