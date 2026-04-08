/**
 * @fileoverview ProfileLookupPage — Username-to-profile redirect page.
 *
 * This page resolves a username to a user ID and redirects to the profile page.
 * It is used when navigating to `/u/:username` (e.g., from @mention links).
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Redirect-on-resolve pattern**: This page doesn't display any permanent
 *     content. It exists solely to resolve a username to a numeric user ID via
 *     the `/users/lookup/:username` API endpoint, then immediately redirects
 *     to `/profile/:id` using `navigate()`. This is a "loading page" pattern
 *     that handles the async resolution before routing.
 *   - **Error state**: If the username doesn't exist, the component shows an
 *     error message with a "Go back" button (using `navigate(-1)` for
 *     browser-history-aware back navigation).
 *   - **Authentication gate**: Unauthenticated users are redirected to `/login`
 *     since the lookup API requires an auth token.
 *
 * @module pages/ProfileLookupPage
 */

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';

/**
 * ProfileLookupPage component — resolves a username and redirects to the profile.
 *
 * Route: `/u/:username`
 * On success: redirects to `/profile/:id`
 * On failure: shows "User not found" message.
 *
 * @returns {JSX.Element}
 */
function ProfileLookupPage() {
  /** The username from the URL path: /u/:username */
  const { username } = useParams();
  const navigate = useNavigate();
  const { session } = useAuth();
  const [notFound, setNotFound] = useState(false);

  /**
   * Resolves the username to a user ID and navigates to their profile.
   *
   * Dependencies: navigate, session, username — re-runs when any change
   * (e.g., if the user logs in while on this page, it retries the lookup).
   */
  useEffect(() => {
    setNotFound(false); // Reset error state on re-run

    async function resolveProfile() {
      if (!session?.access_token || !username) {
        navigate('/login'); // Redirect unauthenticated users
        return;
      }

      try {
        const data = await apiRequest(`/users/lookup/${username}`, {
          headers: getHeaders(session.access_token),
        });
        // Redirect to the numeric profile page
        navigate(`/profile/${data.id}`);
      } catch {
        setNotFound(true); // Username doesn't exist
      }
    }

    resolveProfile();
  }, [navigate, session, username]);

  // Show error state if the username wasn't found
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

  // Loading state while resolving the username
  return <section className="panel stack-gap"><p className="muted-copy">Opening profile...</p></section>;
}

export default ProfileLookupPage;
