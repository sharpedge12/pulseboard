/**
 * @fileoverview VerifyEmailPage — Email verification handler.
 *
 * This page is visited when a user clicks the verification link in their email.
 * It extracts the verification token from the URL query string, sends it to the
 * backend for validation, and shows the result.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **One-shot effect**: The `useEffect` runs once when the component mounts
 *     (dependency: `searchParams`). It extracts the token and immediately sends
 *     the verification request. There's no user interaction needed — the page
 *     auto-verifies on load.
 *   - **Three-state UI**: The component has three visual states:
 *       1. `verifying` — shows a loading message while the API call is in flight.
 *       2. `success` — shows a success message with a "Go to Login" button.
 *       3. `error` — shows an error message (invalid/expired token) with a
 *          "Back to Login" button.
 *   - **Token consumption**: The backend marks the token as used after successful
 *     verification, so clicking the link again will show an error. This is standard
 *     security practice for one-time-use tokens.
 *
 * @module pages/VerifyEmailPage
 */

import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { apiRequest, getHeaders } from '../lib/api';

/**
 * VerifyEmailPage component — auto-verifies the email on mount.
 *
 * Route: `/verify-email?token=...`
 *
 * @returns {JSX.Element}
 */
function VerifyEmailPage() {
  /** Extract the verification token from the URL query string. */
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // ── UI state ──
  const [status, setStatus] = useState('verifying');  // 'verifying' | 'success' | 'error'
  const [message, setMessage] = useState('Verifying your email...');

  /**
   * Sends the verification token to the backend on mount.
   * The backend validates the token, marks the user as verified, and returns
   * a success message.
   *
   * If no token is present in the URL, immediately shows an error.
   */
  useEffect(() => {
    const token = searchParams.get('token');
    if (!token) {
      setStatus('error');
      setMessage('No verification token provided.');
      return;
    }

    async function verify() {
      try {
        const data = await apiRequest('/auth/verify-email', {
          method: 'POST',
          headers: getHeaders(),
          body: JSON.stringify({ token }),
        });
        setStatus('success');
        setMessage(data.message || 'Email verified successfully! You can now log in.');
      } catch (err) {
        setStatus('error');
        setMessage(err.message || 'Verification failed.');
      }
    }

    verify();
  }, [searchParams]);

  return (
    <section className="page-grid auth-centered">
      <div className="panel stack-gap">
        <div className="panel-header">
          <h3>Email Verification</h3>
        </div>

        {/* Verifying state — loading indicator */}
        {status === 'verifying' && (
          <p className="muted-copy">{message}</p>
        )}

        {/* Success state — verified, navigate to login */}
        {status === 'success' && (
          <>
            <p className="success-copy">{message}</p>
            <button
              className="action-button"
              type="button"
              onClick={() => navigate('/login')}
            >
              Go to Login
            </button>
          </>
        )}

        {/* Error state — invalid or expired token */}
        {status === 'error' && (
          <>
            <p className="error-copy">{message}</p>
            <button
              className="secondary-button"
              type="button"
              onClick={() => navigate('/login')}
            >
              Back to Login
            </button>
          </>
        )}
      </div>
    </section>
  );
}

export default VerifyEmailPage;
