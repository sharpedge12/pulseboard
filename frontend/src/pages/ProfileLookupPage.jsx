import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';

function ProfileLookupPage() {
  const { username } = useParams();
  const navigate = useNavigate();
  const { session } = useAuth();
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    setNotFound(false);

    async function resolveProfile() {
      if (!session?.access_token || !username) {
        navigate('/login');
        return;
      }

      try {
        const data = await apiRequest(`/users/lookup/${username}`, {
          headers: getHeaders(session.access_token),
        });
        navigate(`/profile/${data.id}`);
      } catch {
        setNotFound(true);
      }
    }

    resolveProfile();
  }, [navigate, session, username]);

  if (notFound) {
    return (
      <section className="page-grid feed-layout">
        <div className="panel stack-gap" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
          <h3>User not found</h3>
          <p className="muted-copy">
            The user <strong>@{username}</strong> does not exist or may have been removed.
          </p>
          <button
            className="action-button"
            type="button"
            onClick={() => navigate(-1)}
          >
            Go back
          </button>
        </div>
      </section>
    );
  }

  return <section className="panel stack-gap"><p className="muted-copy">Opening profile...</p></section>;
}

export default ProfileLookupPage;
