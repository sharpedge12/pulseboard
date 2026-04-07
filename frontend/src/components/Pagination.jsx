/**
 * Pagination component with numbered page buttons, ellipsis, and Prev/Next.
 *
 * Props:
 *   currentPage  - active page (1-indexed)
 *   totalPages   - total number of pages
 *   totalItems   - total item count (for "N threads" label)
 *   onPageChange - callback(pageNumber)
 *   siblingCount - how many page numbers to show on each side of current (default 1)
 *   itemLabel    - label for items, e.g. "threads" (default "items")
 */
function Pagination({
  currentPage,
  totalPages,
  totalItems = 0,
  onPageChange,
  siblingCount = 1,
  itemLabel = 'items',
}) {
  if (totalPages <= 1) return null;

  /**
   * Build the array of page numbers / ellipsis markers to render.
   * Always shows: first page, last page, current page, and `siblingCount` pages
   * on each side of the current page. Gaps are filled with '...' markers.
   */
  function buildPageRange() {
    const totalSlots = siblingCount * 2 + 5; // siblings + current + 2 boundaries + 2 ellipses

    // If total pages fits in the available slots, show all pages
    if (totalPages <= totalSlots) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }

    const leftSibling = Math.max(currentPage - siblingCount, 1);
    const rightSibling = Math.min(currentPage + siblingCount, totalPages);

    const showLeftEllipsis = leftSibling > 2;
    const showRightEllipsis = rightSibling < totalPages - 1;

    if (!showLeftEllipsis && showRightEllipsis) {
      // Near the start: show first N pages + ellipsis + last page
      const leftCount = siblingCount * 2 + 3;
      const pages = Array.from({ length: leftCount }, (_, i) => i + 1);
      return [...pages, '...', totalPages];
    }

    if (showLeftEllipsis && !showRightEllipsis) {
      // Near the end: first page + ellipsis + last N pages
      const rightCount = siblingCount * 2 + 3;
      const pages = Array.from(
        { length: rightCount },
        (_, i) => totalPages - rightCount + 1 + i
      );
      return [1, '...', ...pages];
    }

    // In the middle: first + ellipsis + siblings + ellipsis + last
    const middlePages = Array.from(
      { length: rightSibling - leftSibling + 1 },
      (_, i) => leftSibling + i
    );
    return [1, '...', ...middlePages, '...', totalPages];
  }

  const pages = buildPageRange();

  return (
    <div className="pagination-row">
      <button
        className="pagination-btn pagination-nav"
        type="button"
        disabled={currentPage <= 1}
        onClick={() => onPageChange(currentPage - 1)}
        aria-label="Previous page"
      >
        Prev
      </button>

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
              aria-current={page === currentPage ? 'page' : undefined}
              aria-label={`Page ${page}`}
            >
              {page}
            </button>
          )
        )}
      </div>

      <button
        className="pagination-btn pagination-nav"
        type="button"
        disabled={currentPage >= totalPages}
        onClick={() => onPageChange(currentPage + 1)}
        aria-label="Next page"
      >
        Next
      </button>

      <span className="pagination-info">
        {totalItems} {itemLabel}
      </span>
    </div>
  );
}

export default Pagination;
