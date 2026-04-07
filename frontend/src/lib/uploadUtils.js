/**
 * Client-side file upload validation utilities.
 *
 * These mirror the backend ALLOWED_CONTENT_TYPES / ALLOWED_EXTENSIONS so the
 * user gets instant feedback before wasting bandwidth on a rejected upload.
 */

/** MIME types accepted for general attachments (threads, posts, chat). */
export const ATTACHMENT_ACCEPT =
  'image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,' +
  'application/pdf,text/plain,application/msword,' +
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

/** MIME types accepted for avatar uploads (images only, including GIF). */
export const AVATAR_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif';

/** Maximum upload size in bytes (must match backend settings.max_upload_size_mb). */
export const MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB

/** Human-readable max size label. */
export const MAX_UPLOAD_SIZE_LABEL = '25 MB';

/** Allowed file extensions (lowercase, for display). */
export const ALLOWED_EXTENSIONS = [
  '.jpg', '.jpeg', '.png', '.webp', '.gif',
  '.mp4', '.webm',
  '.pdf', '.txt', '.doc', '.docx',
];

/**
 * Validate a file before uploading.
 *
 * @param {File} file - The File object from an input element.
 * @param {object} [options]
 * @param {boolean} [options.imageOnly] - If true, only image MIME types are allowed.
 * @returns {{ valid: boolean, error?: string }}
 */
export function validateFile(file, { imageOnly = false } = {}) {
  if (!file) {
    return { valid: false, error: 'No file selected.' };
  }

  // Size check
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return {
      valid: false,
      error: `File is too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Maximum is ${MAX_UPLOAD_SIZE_LABEL}.`,
    };
  }

  // Empty file check
  if (file.size === 0) {
    return { valid: false, error: 'File is empty.' };
  }

  // MIME type check
  const allowedMimes = imageOnly
    ? AVATAR_ACCEPT.split(',')
    : ATTACHMENT_ACCEPT.split(',');

  if (!allowedMimes.includes(file.type)) {
    const allowed = imageOnly
      ? 'JPEG, PNG, WebP, GIF'
      : 'images (JPEG, PNG, WebP, GIF), videos (MP4, WebM), documents (PDF, TXT, DOC, DOCX)';
    return {
      valid: false,
      error: `Unsupported file type "${file.type || 'unknown'}". Allowed: ${allowed}.`,
    };
  }

  // Extension check (as extra guard)
  const name = file.name || '';
  const ext = name.includes('.') ? '.' + name.split('.').pop().toLowerCase() : '';
  if (ext && !ALLOWED_EXTENSIONS.includes(ext)) {
    return {
      valid: false,
      error: `File extension "${ext}" is not allowed. Allowed: ${ALLOWED_EXTENSIONS.join(', ')}.`,
    };
  }

  return { valid: true };
}
