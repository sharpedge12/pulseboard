/**
 * @file AttachmentList.jsx
 * @description Renders a list of file attachments — images are displayed as
 *              thumbnails, other files show as download links with file type labels.
 *
 * INTERVIEW CONCEPTS DEMONSTRATED:
 * ─────────────────────────────────
 * 1. **Conditional Rendering Based on Data Type** — The component renders
 *    different UI for images vs. non-image files. This is the "polymorphic
 *    rendering" pattern — one component handles multiple visual representations
 *    based on the data's `file_type` property.
 *
 * 2. **Default Props with Destructuring** — `attachments = []` provides a
 *    default empty array, which means callers don't need to check for
 *    null/undefined before passing the prop. The component handles the
 *    empty case gracefully by returning null.
 *
 * 3. **Early Return for Empty State** — `if (!attachments.length) return null;`
 *    avoids rendering an empty container div when there are no attachments.
 *    This keeps the DOM clean and prevents CSS from applying styles to an
 *    empty element (e.g., margins, padding from `.attachment-list`).
 *
 * 4. **URL Resolution Strategy** — Attachment URLs can be either absolute
 *    (starting with "http" — for externally hosted files) or relative paths
 *    (for files uploaded to the local server). The `assetUrl()` helper
 *    prepends the API base URL for relative paths.
 *
 * 5. **Security: target="_blank" with rel="noreferrer"** — External links
 *    use `rel="noreferrer"` to prevent the opened page from accessing
 *    `window.opener` (a security vulnerability called "tabnapping").
 *    Modern browsers also imply `noopener` when `noreferrer` is set.
 *
 * @see {@link ../lib/api.js} for the `assetUrl` helper function
 */

import { assetUrl } from '../lib/api';

/**
 * AttachmentList — Renders file attachments as image thumbnails or download links.
 *
 * VISUAL LAYOUT:
 * ┌──────────────────────────────────────────────┐
 * │ ┌────────┐  ┌────────┐  ┌─────────────────┐ │
 * │ │ [img]  │  │ [img]  │  │ report.pdf      │ │
 * │ │        │  │        │  │ document         │ │
 * │ └────────┘  └────────┘  └─────────────────┘ │
 * └──────────────────────────────────────────────┘
 *   Images render as <img> thumbnails; non-images show filename + type label.
 *   All items are wrapped in <a> tags linking to the full file.
 *
 * @param {Object} props
 * @param {Array<Object>} [props.attachments=[]] - Array of attachment objects:
 *   @param {number} props.attachments[].id          - Unique attachment ID
 *   @param {string} props.attachments[].public_url  - URL path to the file (absolute or relative)
 *   @param {string} props.attachments[].file_type   - Type of file: "image", "video", "document", etc.
 *   @param {string} props.attachments[].file_name   - Original filename for display and alt text
 * @returns {JSX.Element|null} The attachment grid, or null if no attachments
 */
function AttachmentList({ attachments = [] }) {
  /*
   * Guard clause — render nothing if there are no attachments.
   *
   * INTERVIEW TIP: `!attachments.length` works because 0 is falsy in JS.
   * This is equivalent to `attachments.length === 0` but more concise.
   * Some style guides prefer the explicit comparison for clarity.
   */
  if (!attachments.length) {
    return null;
  }

  return (
    <div className="attachment-list">
      {attachments.map((attachment) => {
        /*
         * Resolve the attachment URL.
         *
         * INTERVIEW TIP: This URL resolution pattern handles two cases:
         * 1. Absolute URL (starts with "http") — external/CDN hosted files
         * 2. Relative path — local uploads, resolved via assetUrl() which
         *    prepends the API server base URL (e.g., "http://localhost:8000")
         *
         * This same pattern appears throughout the codebase (UserIdentity,
         * MentionTextarea, UserActionModal) for avatar URLs.
         */
        const url = attachment.public_url.startsWith('http')
          ? attachment.public_url
          : assetUrl(attachment.public_url);

        /** @type {boolean} Whether this attachment is an image (renders as thumbnail) */
        const isImage = attachment.file_type === 'image';

        return (
          /*
           * Each attachment is an <a> tag — clickable to view/download.
           *
           * INTERVIEW TIP:
           * - `target="_blank"` opens in a new tab (don't navigate away from the thread)
           * - `rel="noreferrer"` is a security best practice for external links:
           *   it prevents the new page from accessing window.opener (tabnapping)
           *   and also prevents sending the Referer header (privacy).
           * - `key={attachment.id}` uses a stable unique ID (from the database),
           *   NOT the array index. Using array indices as keys can cause bugs
           *   when items are reordered or removed.
           */
          <a
            key={attachment.id}
            className="attachment-card"
            href={url}
            target="_blank"
            rel="noreferrer"
          >
            {isImage ? (
              /*
               * Image attachments render as inline thumbnails.
               * The `alt` text uses the original filename for accessibility.
               */
              <img
                src={url}
                alt={attachment.file_name}
              />
            ) : (
              /*
               * Non-image attachments show the filename as bold text.
               * This covers documents, PDFs, videos, etc.
               */
              <strong>{attachment.file_name}</strong>
            )}
            {/*
              File type label — only shown for non-image files.
              Displays the type (e.g., "document", "video") in muted text
              below the filename to help users identify the file format.
            */}
            {!isImage && (
              <span className="muted-copy">{attachment.file_type}</span>
            )}
          </a>
        );
      })}
    </div>
  );
}

export default AttachmentList;
