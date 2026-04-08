/**
 * @file LoginPrompt.jsx
 * @description Inline dismissible banner prompting unauthenticated users to log in.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Dismissible UI Pattern** — The banner has an optional close button (X).
 *    When clicked, it calls `onClose`, which the parent uses to set a state flag
 *    (e.g., `setShowLoginPrompt(false)`) that conditionally removes the banner
 *    from the DOM. This is the standard "dismissible notification" pattern.
 *
 * 2. **Progressive Disclosure UX** — Rather than hiding interactive features
 *    from guests (which would confuse them about what the app offers), we show
 *    the features and display this prompt when guests try to use them. This
 *    teaches guests about the feature while guiding them to sign up.
 *
 * 3. **Default Props via Short-Circuit** — `message || 'You need to log in...'`
 *    provides a default message when none is passed. This is simpler than
 *    defaultProps or default parameter values for strings.
 *
 * 4. **Conditional Rendering for Optional Close Button** — The close button
 *    only renders if `onClose` is provided. Some callers may want a non-
 *    dismissible prompt. This is an example of "graceful degradation" in
 *    component API design.
 *
 * 5. **Programmatic Navigation** — `useNavigate` from React Router is used
 *    to navigate to /login. The navigate function is called inside an onClick
 *    handler rather than using a `<Link>` because the "Log In" element is
 *    a `<button>`, not an anchor tag (semantic correctness — it triggers an
 *    action, not a hyperlink).
 *
 * WHERE THIS COMPONENT IS USED:
 * - ThreadCard.jsx — when a guest tries to vote or react
 * - ThreadPage.jsx — when a guest tries to reply
 * - Any future feature that requires authentication
 *
 * @see {@link ./ThreadCard.jsx} Example usage with vote/react guards
 */

import { useNavigate } from 'react-router-dom';

/**
 * LoginPrompt — An inline banner displayed to unauthenticated users when they
 * attempt a protected action (voting, replying, reacting, etc.).
 *
 * VISUAL LAYOUT:
 * ┌──────────────────────────────────────────────────────┐
 * │ Log in to vote, react, and join the discussion.      │
 * │                                    [Log In]  [✕]     │
 * └──────────────────────────────────────────────────────┘
 *
 * @param {Object} props
 * @param {string}   [props.message]  - Custom message text (default: "You need to log in to do that.")
 * @param {Function} [props.onClose]  - Optional callback to dismiss the prompt. If not provided, the close button is hidden.
 * @returns {JSX.Element} The login prompt banner
 */
function LoginPrompt({ message, onClose }) {
  /**
   * React Router's programmatic navigation hook.
   *
   * INTERVIEW TIP: We use `useNavigate()` instead of a `<Link to="/login">`
   * because the "Log In" element is a `<button>`. While you COULD wrap a
   * Link around a button, that's technically invalid HTML (interactive
   * element inside another interactive element). Using navigate() in an
   * onClick handler is the correct approach.
   */
  const navigate = useNavigate();

  return (
    <div className="login-prompt">
      {/*
        Message text — uses the provided message or a sensible default.

        INTERVIEW TIP: The `||` operator here acts as a "default value" mechanism.
        If `message` is undefined, null, or an empty string (all falsy),
        the default string is used. This is a JS idiom, though for more
        precise null-only defaults, you'd use `message ?? 'default'`
        (nullish coalescing — doesn't treat empty string as falsy).
      */}
      <span>{message || 'You need to log in to do that.'}</span>

      {/*
        "Log In" button — navigates to the login page.

        INTERVIEW TIP: `type="button"` is explicitly set. Without it, a
        <button> inside a <form> defaults to `type="submit"`, which would
        submit the form and reload the page. Always set `type="button"` on
        buttons that aren't form submit triggers. This is a common gotcha.
      */}
      <button
        className="login-prompt-btn"
        type="button"
        onClick={() => navigate('/login')}
      >
        Log In
      </button>

      {/*
        Dismiss (close) button — only rendered if `onClose` callback is provided.

        INTERVIEW TIP: `{onClose && (...)}` is conditional rendering via
        short-circuit evaluation. If onClose is undefined (falsy), the
        close button is not rendered at all. This allows the same component
        to be used in two modes:
          <LoginPrompt onClose={() => setShow(false)} />  — dismissible
          <LoginPrompt />                                  — permanent
      */}
      {onClose && (
        <button
          className="login-prompt-close"
          type="button"
          onClick={onClose}
          title="Dismiss"
        >
          &#x2715;{/* Unicode multiplication sign ✕ used as a close icon */}
        </button>
      )}
    </div>
  );
}

export default LoginPrompt;
