import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { apiRequest, getHeaders } from '../lib/api';

function VerifyEmailPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState('verifying');
  const [message, setMessage] = useState('Verifying your email...');

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

        {status === 'verifying' && (
          <p className="muted-copy">{message}</p>
        )}

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
