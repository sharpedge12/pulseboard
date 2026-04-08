/**
 * @file Pagination.jsx
 * @description Reusable pagination component with numbered page buttons, ellipsis
 *              markers, and Prev/Next navigation.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Pagination Algorithm** — The `buildPageRange()` function implements
 *    the standard "pages with ellipsis" algorithm. This is a VERY common
 *    interview question (asked at Google, Meta, Amazon). The algorithm
 *    always shows the first page, last page, current page, and N siblings
 *    on each side. Gaps are represented by "..." ellipsis markers.
 *
 * 2. **Controlled Component Pattern** — This component does NOT manage its
 *    own page state. It receives `currentPage` as a prop and calls
 *    `onPageChange(pageNumber)` when the user clicks a page. The parent
 *    owns the state (often synced to URL query params like `?page=3`).
 *    This makes Pagination reusable across different data sources.
 *
 * 3. **Early Return for Edge Cases** — `if (totalPages <= 1) return null;`
 *    eliminates the pagination UI when there's only one page. This is a
 *    common UX decision — don't show controls that serve no purpose.
 *
 * 4. **ARIA Attributes for Accessibility** — `aria-label`, `aria-current="page"`,
 *    and `disabled` are used to make pagination keyboard- and screen-reader-
 *    friendly. This demonstrates WCAG compliance awareness.
 *
 * 5. **Array.from for Range Generation** — Instead of a for-loop,
 *    `Array.from({ length: N }, (_, i) => i + 1)` generates `[1, 2, ..., N]`.
 *    This is a common functional programming pattern in React codebases.
 *
 * @example
 * // Basic usage in a page component
 * <Pagination
 *   currentPage={3}
 *   totalPages={10}
 *   totalItems={98}
 *   onPageChange={(page) => setSearchParams({ page })}
 *   itemLabel="threads"
 * />
 *
 * // Renders: [Prev] [1] [...] [2] [3] [4] [...] [10] [Next]  98 threads
 */

/**
 * Pagination — Renders numbered page buttons with ellipsis and Prev/Next controls.
 *
 * @param {Object} props
 * @param {number}   props.currentPage   - The active page number (1-indexed)
 * @param {number}   props.totalPages    - Total number of pages
 * @param {number}   [props.totalItems=0]  - Total item count (for the "N items" label)
 * @param {Function} props.onPageChange  - Callback: (pageNumber: number) => void
 * @param {number}   [props.siblingCount=1] - Number of page buttons to show on each side of the current page
 * @param {string}   [props.itemLabel='items'] - Label for the item count (e.g., "threads", "posts")
 * @returns {JSX.Element|null} The pagination controls, or null if only 1 page exists
 */
function Pagination({
  currentPage,
  totalPages,
  totalItems = 0,
  onPageChange,
  siblingCount = 1,
  itemLabel = 'items',
}) {
  /*
   * Early return — no pagination needed when there's 0 or 1 page.
   *
   * INTERVIEW TIP: This is a guard clause. It simplifies the rest of the
   * function by eliminating an edge case upfront. Returning `null` from
   * a React component renders nothing to the DOM.
   */
  if (totalPages <= 1) return null;

  /**
   * Builds the array of page numbers and ellipsis markers to render.
   *
   * THE ALGORITHM (this is the interview-worthy part):
   * ─────────────────────────────────────────────────
   * Always show: page 1, page N (last), current page, and `siblingCount`
   * pages on each side of the current page. Fill gaps with "..." markers.
   *
   * Four cases:
   *
   * Case 1: Total pages fit — show all pages (no ellipsis).
   *   [1] [2] [3] [4] [5] [6] [7]
   *
   * Case 2: Near the start — left pages + ellipsis + last page.
   *   [1] [2] [3] [4] [5] [...] [20]
   *
   * Case 3: Near the end — first page + ellipsis + right pages.
   *   [1] [...] [16] [17] [18] [19] [20]
   *
   * Case 4: In the middle — first + ellipsis + siblings + ellipsis + last.
   *   [1] [...] [9] [10] [11] [...] [20]
   *
   * The `totalSlots` calculation determines when we need ellipsis:
   * totalSlots = siblingCount * 2 + 5
   *            = 1 * 2 + 5 = 7  (with default siblingCount=1)
   * This accounts for: left boundary + left ellipsis + left sibling +
   * current + right sibling + right ellipsis + right boundary.
   *
   * @returns {Array<number|string>} Array of page numbers and "..." strings
   */
  function buildPageRange() {
    // Maximum number of page slots before we need ellipsis
    const totalSlots = siblingCount * 2 + 5;

    // Case 1: All pages fit — no ellipsis needed
    if (totalPages <= totalSlots) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }

    // Calculate the sibling boundaries
    const leftSibling = Math.max(currentPage - siblingCount, 1);
    const rightSibling = Math.min(currentPage + siblingCount, totalPages);

    // Determine whether to show ellipsis on each side
    const showLeftEllipsis = leftSibling > 2;       // Gap between page 1 and left sibling
    const showRightEllipsis = rightSibling < totalPages - 1; // Gap between right sibling and last page

    // Case 2: Near the start — no left ellipsis
    if (!showLeftEllipsis && showRightEllipsis) {
      const leftCount = siblingCount * 2 + 3;
      const pages = Array.from({ length: leftCount }, (_, i) => i + 1);
      return [...pages, '...', totalPages];
    }

    // Case 3: Near the end — no right ellipsis
    if (showLeftEllipsis && !showRightEllipsis) {
      const rightCount = siblingCount * 2 + 3;
      const pages = Array.from(
        { length: rightCount },
        (_, i) => totalPages - rightCount + 1 + i
      );
      return [1, '...', ...pages];
    }

    // Case 4: In the middle — ellipsis on both sides
    const middlePages = Array.from(
      { length: rightSibling - leftSibling + 1 },
      (_, i) => leftSibling + i
    );
    return [1, '...', ...middlePages, '...', totalPages];
  }

  /** @type {Array<number|string>} The computed page range to render */
  const pages = buildPageRange();

  return (
    <div className="pagination-row">
      {/*
        ── Previous Button ──────────────────────────────────────────
        Disabled when on page 1 (can't go further back).

        INTERVIEW TIP: The `disabled` prop on a <button> makes it:
        - Visually grayed out (via CSS `:disabled` pseudo-class)
        - Not clickable (browser prevents the onClick handler)
        - Removed from tab order (keyboard users skip it)
        - Announced as "disabled" by screen readers
        All of that for free, just from the HTML `disabled` attribute.
      */}
      <button
        className="pagination-btn pagination-nav"
        type="button"
        disabled={currentPage <= 1}
        onClick={() => onPageChange(currentPage - 1)}
        aria-label="Previous page"
      >
        Prev
      </button>

      {/*
        ── Page Number Buttons ──────────────────────────────────────
        Renders the computed page range — mix of page numbers and "..." strings.

        INTERVIEW TIP: `pages.map()` handles two types of elements:
        - Ellipsis strings ("...") → rendered as a non-interactive <span>
        - Page numbers → rendered as clickable <button> elements

        The `key` for ellipsis uses the array index (`ellipsis-${index}`)
        because "..." can appear twice (left and right). Normally, using
        array index as a key is an anti-pattern, but it's fine here because
        the list is rebuilt from scratch each time (no reordering).
      */}
      <div className="pagination-pages">
        {pages.map((page, index) =>
          page === '...' ? (
            <span key={`ellipsis-${index}`} className="pagination-ellipsis">
              ...
            </span>
          ) : (
            <button
              key={page}
              className={
                page === currentPage
                  ? 'pagination-btn pagination-num active'
                  : 'pagination-btn pagination-num'
              }
              type="button"
              onClick={() => onPageChange(page)}
              /*
               * ARIA: `aria-current="page"` tells screen readers which
               * button represents the current page. This is the correct
               * ARIA role for pagination (not `aria-selected`, which is
               * for tabs and listboxes).
               */
              aria-current={page === currentPage ? 'page' : undefined}
              aria-label={`Page ${page}`}
            >
              {page}
            </button>
          )
        )}
      </div>

      {/*
        ── Next Button ──────────────────────────────────────────────
        Disabled when on the last page.
      */}
      <button
        className="pagination-btn pagination-nav"
        type="button"
        disabled={currentPage >= totalPages}
        onClick={() => onPageChange(currentPage + 1)}
        aria-label="Next page"
      >
        Next
      </button>

      {/*
        ── Item Count Label ─────────────────────────────────────────
        Displays "98 threads" or "42 posts" etc.
        Uses the `itemLabel` prop for flexible labeling.
      */}
      <span className="pagination-info">
        {totalItems} {itemLabel}
      </span>
    </div>
  );
}

export default Pagination;
