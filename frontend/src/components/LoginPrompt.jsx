import { useNavigate } from 'react-router-dom';

/**
 * Inline banner prompting unauthenticated users to log in.
 * Renders a dismissable bar with a link to /login.
 *
 * Props:
 *   message  - optional custom message (default: generic prompt)
 *   onClose  - () => void  (dismiss the prompt)
 */
function LoginPrompt({ message, onClose }) {
  const navigate = useNavigate();

  return (
    <div className="login-prompt">
      <span>{message || 'You need to log in to do that.'}</span>
      <button
        className="login-prompt-btn"
        type="button"
        onClick={() => navigate('/login')}
      >
        Log In
      </button>
      {onClose && (
        <button
          className="login-prompt-close"
          type="button"
          onClick={onClose}
          title="Dismiss"
        >
          &#x2715;
        </button>
      )}
    </div>
  );
}

export default LoginPrompt;
