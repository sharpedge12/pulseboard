/**
 * @file RichText.jsx
 * @description Renders text with @username mentions converted into clickable
 *              React Router links.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Regex-Based Text Parsing** — The component splits raw text into segments
 *    using a regex that matches @username patterns. This is a fundamental text
 *    processing technique used in chat apps, social media, and markdown renderers.
 *    The key insight is using a CAPTURING GROUP in the regex so that `split()`
 *    retains the matched segments in the output array.
 *
 * 2. **useMemo for Expensive String Operations** — `text.split()` creates a
 *    new array on every call. `useMemo` ensures this only happens when the
 *    `text` prop changes, not on every parent re-render. For long text with
 *    many mentions, this prevents unnecessary array allocations.
 *
 * 3. **Client-Side Routing with React Router Link** — Mentions are rendered
 *    as `<Link>` components (not `<a>` tags). This enables client-side
 *    navigation — clicking a mention loads the profile page without a full
 *    page reload. This is the key difference between SPAs and traditional
 *    server-rendered apps.
 *
 * 4. **Key Generation for Dynamic Lists** — Each text segment needs a unique
 *    `key` for React's reconciliation algorithm. We use `${part}-${index}`
 *    which combines the content with its position. Using index alone would be
 *    fragile if the text changed; using content alone would fail if the same
 *    word appears multiple times.
 *
 * 5. **String.prototype.split() with Capturing Groups** — This is a subtle
 *    but powerful JS feature. Normally, `split()` removes the delimiter.
 *    But when the regex contains a capturing group `()`, the matched text
 *    is INCLUDED in the resulting array. This is what makes the parsing work:
 *
 *      "Hello @alice and @bob".split(/(@[A-Za-z0-9_]{3,50})/g)
 *      // → ["Hello ", "@alice", " and ", "@bob", ""]
 *
 *    Without the capturing group:
 *      "Hello @alice and @bob".split(/@[A-Za-z0-9_]{3,50}/g)
 *      // → ["Hello ", " and ", ""]  ← mentions are LOST
 *
 * @example
 * // Usage in a post body renderer:
 * <RichText text="Hey @alice, check out what @bob posted!" />
 *
 * // Renders:
 * // <p>
 * //   <span>Hey </span>
 * //   <Link to="/profile/lookup/alice">@alice</Link>
 * //   <span>, check out what </span>
 * //   <Link to="/profile/lookup/bob">@bob</Link>
 * //   <span> posted!</span>
 * // </p>
 */

import { useMemo } from 'react';
import { Link } from 'react-router-dom';

/**
 * RichText — Converts @username mentions in plain text into clickable profile links.
 *
 * HOW THE PARSING WORKS (step by step):
 * ─────────────────────────────────────
 * Input text:  "Hey @alice, check out what @bob posted!"
 *
 * Step 1: Split with capturing group regex
 *   /(@[A-Za-z0-9_]{3,50})/g
 *
 *   Result: ["Hey ", "@alice", ", check out what ", "@bob", " posted!"]
 *
 * Step 2: Map over the array, testing each segment
 *   - "Hey "          → does NOT match → <span>Hey </span>
 *   - "@alice"        → MATCHES        → <Link to="/profile/lookup/alice">@alice</Link>
 *   - ", check out…"  → does NOT match → <span>, check out…</span>
 *   - "@bob"          → MATCHES        → <Link to="/profile/lookup/bob">@bob</Link>
 *   - " posted!"      → does NOT match → <span> posted!</span>
 *
 * REGEX EXPLANATION:
 *   @           — literal @ character
 *   [A-Za-z0-9_] — character class: letters, digits, underscore (valid username chars)
 *   {3,50}      — between 3 and 50 of those characters
 *   (...)       — capturing group (critical for split() to retain matches)
 *   /g          — global flag (find ALL matches, not just the first)
 *
 * @param {Object} props
 * @param {string} [props.text=''] - The raw text that may contain @username mentions
 * @returns {JSX.Element} A `<p>` element with mentions rendered as clickable links
 */
function RichText({ text = '' }) {
  /**
   * Split the text into an array of plain text and @mention segments.
   *
   * INTERVIEW TIP: `useMemo` caches the split result. The dependency
   * array `[text]` means this only re-runs when the text prop changes.
   *
   * Why memoize a string split? Because:
   * 1. `split()` allocates a new array every time
   * 2. This component may re-render when parent state changes
   *    (e.g., vote score updates on the thread page)
   * 3. Without memo, every re-render creates a new array, which
   *    causes React to diff all the <span>/<Link> children again
   *
   * For short text this optimization is negligible, but it demonstrates
   * the correct pattern for derived data in React components.
   *
   * @type {string[]} Array alternating between plain text and @mention segments
   */
  const parts = useMemo(() => text.split(/(@[A-Za-z0-9_]{3,50})/g), [text]);

  return (
    <p>
      {parts.map((part, index) => {
        /*
         * Test each segment against the @mention regex.
         *
         * INTERVIEW TIP: We use a SEPARATE regex (without the /g flag)
         * to test each individual segment. The split regex uses /g for
         * global matching, but the test regex matches the full segment
         * (anchored by ^ and $ implicitly since we test the whole string).
         *
         * Why test again? Because split() retains both the matches AND
         * the non-matching segments. We need to distinguish which is which.
         */
        if (/^@[A-Za-z0-9_]{3,50}$/.test(part)) {
          // Extract the username by removing the leading @
          const username = part.slice(1);
          return (
            /*
             * Render the mention as a React Router <Link>.
             *
             * INTERVIEW TIP: <Link> renders an <a> tag but intercepts
             * the click to perform client-side navigation (no page reload).
             * This is the fundamental difference between SPAs and traditional
             * server-rendered apps:
             *   <a href="/profile/...">   → full page reload
             *   <Link to="/profile/...">  → client-side navigation (fast)
             *
             * The `to` prop uses a lookup route: `/profile/lookup/username`.
             * This lets the server resolve the username to a user ID and
             * redirect to the canonical profile URL.
             *
             * The key uses both content and index to ensure uniqueness
             * even when the same username appears multiple times.
             */
            <Link
              key={`${part}-${index}`}
              className="mention-link"
              to={`/profile/lookup/${username}`}
            >
              {part}
            </Link>
          );
        }

        /*
         * Non-mention text — render as a plain <span>.
         *
         * INTERVIEW TIP: We wrap plain text in <span> elements (not
         * raw text nodes) so that each segment has a key for React's
         * reconciliation. Raw text nodes can't have keys, which would
         * make React's diffing less efficient.
         */
        return <span key={`${part}-${index}`}>{part}</span>;
      })}
    </p>
  );
}

export default RichText;
