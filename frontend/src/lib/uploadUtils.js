/**
 * @file uploadUtils.js — Client-side file upload validation utilities.
 *
 * **Interview topics: Client-side validation, defence in depth, MIME types,
 * file extension checking, the File API.**
 *
 * ### Why validate on the client AND the server?
 * This is called **defence in depth**.  The backend already validates uploads
 * (MIME type, magic bytes, file size, extension).  Client-side validation
 * is NOT a security measure — it's a **UX optimisation**.  It gives the user
 * instant feedback ("file too large") without waiting for a network round
 * trip.  A malicious user can always bypass client-side checks, which is
 * why the backend must independently enforce the same rules.
 *
 * ### Constants mirror backend settings:
 * The allowed MIME types, extensions, and size limit here are kept in sync
 * with the backend's `storage.py` configuration.  If the backend changes
 * its limits, these constants must be updated too — otherwise users would
 * see confusing "accepted locally, rejected by server" errors.
 *
 * ### The `File` object:
 * When a user selects a file via `<input type="file">`, the browser creates
 * a `File` object with properties like `.name`, `.size` (bytes), and `.type`
 * (MIME string).  These are what we validate against below.
 *
 * @module lib/uploadUtils
 */

/**
 * MIME types accepted for general attachments (threads, posts, chat messages).
 *
 * This string is used directly in the HTML `<input accept="...">` attribute,
 * which tells the browser's file picker to filter for these types.  Note that
 * `accept` is a hint — the browser may still allow other files, which is why
 * we also validate programmatically in `validateFile()`.
 *
 * @constant {string}
 */
export const ATTACHMENT_ACCEPT =
  'image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,' +
  'application/pdf,text/plain,application/msword,' +
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

/**
 * MIME types accepted for avatar uploads (images only, including GIF).
 *
 * Avatars are more restrictive than general attachments — no videos or
 * documents allowed.  GIF is included so users can have animated avatars.
 *
 * @constant {string}
 */
export const AVATAR_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif';

/**
 * Maximum upload size in bytes.
 *
 * Must match the backend's `settings.max_upload_size_mb` (currently 25 MB).
 * We define it in bytes because `File.size` is reported in bytes.
 *
 * **Interview note — why `25 * 1024 * 1024` instead of `25_000_000`?**
 * 1 MB = 1024 * 1024 bytes (binary definition, aka MiB).  Using `25_000_000`
 * (decimal MB) would be slightly smaller and could reject files the server
 * would accept.  We match the backend's definition exactly.
 *
 * @constant {number}
 */
export const MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB

/**
 * Human-readable label for the max upload size, used in error messages.
 * @constant {string}
 */
export const MAX_UPLOAD_SIZE_LABEL = '25 MB';

/**
 * Whitelist of allowed file extensions (lowercase, with leading dot).
 *
 * This is an extra guard beyond MIME type checking.  A file could have a
 * valid MIME type but a suspicious extension (e.g. `photo.exe` with
 * `image/jpeg` MIME — possible via spoofing).  The backend performs the
 * same extension check, plus magic-byte validation for belt-and-suspenders
 * security.
 *
 * @constant {string[]}
 */
export const ALLOWED_EXTENSIONS = [
  '.jpg', '.jpeg', '.png', '.webp', '.gif',
  '.mp4', '.webm',
  '.pdf', '.txt', '.doc', '.docx',
];

/**
 * Validates a File object before uploading to the server.
 *
 * Performs three checks in order:
 * 1. **Size check** — rejects files larger than 25 MB.
 * 2. **Empty file check** — rejects 0-byte files (corrupt or incomplete).
 * 3. **MIME type check** — ensures `file.type` is in the allowed list.
 * 4. **Extension check** — ensures the filename extension matches the
 *    allowed list (extra guard against MIME spoofing).
 *
 * **Interview note — early return pattern:**
 * Each check returns immediately on failure with a descriptive error
 * message.  This is cleaner than nested if/else blocks and ensures only
 * the first error is reported (users can fix one issue at a time).
 *
 * @param {File}    file             - The File object from `<input type="file">`
 * @param {object}  [options]        - Validation options
 * @param {boolean} [options.imageOnly=false] - If true, only image MIME types
 *                                     are allowed (used for avatar uploads)
 * @returns {{ valid: boolean, error?: string }} Result object.
 *          `valid` is true if the file passes all checks.
 *          `error` contains a user-facing message if validation failed.
 *
 * @example
 * const input = document.querySelector('input[type="file"]');
 * const file = input.files[0];
 * const result = validateFile(file, { imageOnly: true });
 * if (!result.valid) {
 *   alert(result.error); // "File is too large (30.5 MB). Maximum is 25 MB."
 * }
 */
export function validateFile(file, { imageOnly = false } = {}) {
  // Guard: no file selected (e.g. user cancelled the file picker)
  if (!file) {
    return { valid: false, error: 'No file selected.' };
  }

  // ── Check 1: File size ──────────────────────────────────────────────
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return {
      valid: false,
      // Convert bytes to MB for a user-friendly message.
      // `toFixed(1)` gives one decimal place, e.g. "30.5 MB".
      error: `File is too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Maximum is ${MAX_UPLOAD_SIZE_LABEL}.`,
    };
  }

  // ── Check 2: Empty file ─────────────────────────────────────────────
  // A 0-byte file is almost certainly corrupt or a result of a failed
  // drag-and-drop.  The backend would reject it anyway.
  if (file.size === 0) {
    return { valid: false, error: 'File is empty.' };
  }

  // ── Check 3: MIME type ──────────────────────────────────────────────
  // Split the comma-separated accept string into an array for `.includes()`.
  // If `imageOnly` is true, we use the restrictive avatar list.
  const allowedMimes = imageOnly
    ? AVATAR_ACCEPT.split(',')
    : ATTACHMENT_ACCEPT.split(',');

  if (!allowedMimes.includes(file.type)) {
    // Build a human-readable list of allowed types for the error message.
    const allowed = imageOnly
      ? 'JPEG, PNG, WebP, GIF'
      : 'images (JPEG, PNG, WebP, GIF), videos (MP4, WebM), documents (PDF, TXT, DOC, DOCX)';
    return {
      valid: false,
      // `file.type` might be empty if the browser can't determine the MIME
      // type — we show "unknown" in that case.
      error: `Unsupported file type "${file.type || 'unknown'}". Allowed: ${allowed}.`,
    };
  }

  // ── Check 4: File extension ─────────────────────────────────────────
  // Extract the extension from the filename.  `file.name` could be empty
  // in rare edge cases (e.g. programmatic File creation), so we guard.
  const name = file.name || '';
  const ext = name.includes('.') ? '.' + name.split('.').pop().toLowerCase() : '';

  // Only check if we actually got an extension (files without extensions
  // are unusual but possible — we let them through and let the backend
  // decide).
  if (ext && !ALLOWED_EXTENSIONS.includes(ext)) {
    return {
      valid: false,
      error: `File extension "${ext}" is not allowed. Allowed: ${ALLOWED_EXTENSIONS.join(', ')}.`,
    };
  }

  // All checks passed — file is safe to upload.
  return { valid: true };
}
