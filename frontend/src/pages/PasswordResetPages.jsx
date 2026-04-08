/**
 * @fileoverview PasswordResetPages — Two-step password reset flow.
 *
 * This module exports two components for the password reset flow:
 *   1. **ForgotPasswordPage** (`/forgot-password`): User enters their email address
 *      and the backend sends a reset link via email.
 *   2. **ResetPasswordPage** (`/reset-password?token=...`): User arrives from the
 *      email link, enters a new password (with confirmation), and the backend
 *      validates the token and updates the password.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Two-phase stateless flow**: The password reset uses a one-time token sent
 *     via email. The backend generates a random token, stores it with an expiry,
 *     and emails a link. When the user submits the new password with the token,
 *     the backend validates and consumes the token. This is stateless from the
 *     frontend's perspective — no session is needed.
 *   - **Token from URL query param**: The reset token is passed as a `?token=...`
 *     query parameter. `useSearchParams` extracts it. If missing, the form shows
 *     an error. This enables the email link to directly open the reset form.
 *   - **Client-side password confirmation**: The form checks that both password
 *     fields match before sending the request. This is a UX improvement — the
 *     backend also validates password requirements independently.
 *   - **Submitted state**: ForgotPasswordPage tracks a `submitted` boolean to
 *     replace the form with a success message after submission. This prevents
 *     double-submission and provides clear feedback.
 *   - **Named exports**: Both components are exported as named exports from a
 *     single module, since they are closely related and share the same styling.
 *
 * @module pages/PasswordResetPages
 */

import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { apiRequest, getHeaders } from '../lib/api';

/**
 * ForgotPasswordPage — email input form that triggers a password reset email.
 *
 * Route: `/forgot-password`
 * After submission: shows a success message and hides the form.
 *
 * @returns {JSX.Element}
 */
function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');   // Success message from the backend
  const [error, setError] = useState('');        // Error message
  const [submitted, setSubmitted] = useState(false); // Tracks whether the form has been submitted

  /**
   * Sends the password reset request to the backend.
   * On success, shows the backend's message and hides the form.
   *
   * @param {Event} event - The form submit event.
   */
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
      setSubmitted(true); // Hide the form, show the message
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

/**
 * ResetPasswordPage — new password form that consumes a reset token.
 *
 * Route: `/reset-password?token=...`
 * The token is extracted from the URL query string.
 *
 * @returns {JSX.Element}
 */
function ResetPasswordPage() {
  /** Extract the reset token from the URL query string. */
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  /**
   * Validates passwords match, then sends the reset request to the backend.
   * The backend validates the token, checks it hasn't expired, and updates
   * the user's password.
   *
   * @param {Event} event - The form submit event.
   */
  async function handleSubmit(event) {
    event.preventDefault();
    setError('');
    setMessage('');

    // Client-side validation: passwords must match
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    // Guard: ensure the token is present (should come from the email link)
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
