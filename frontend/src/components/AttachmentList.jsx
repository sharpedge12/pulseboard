import { assetUrl } from '../lib/api';

function AttachmentList({ attachments = [] }) {
  if (!attachments.length) {
    return null;
  }

  return (
    <div className="attachment-list">
      {attachments.map((attachment) => {
        const url = attachment.public_url.startsWith('http')
          ? attachment.public_url
          : assetUrl(attachment.public_url);
        const isImage = attachment.file_type === 'image';
        return (
          <a
            key={attachment.id}
            className="attachment-card"
            href={url}
            target="_blank"
            rel="noreferrer"
          >
            {isImage ? (
              <img
                src={url}
                alt={attachment.file_name}
              />
            ) : (
              <strong>{attachment.file_name}</strong>
            )}
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
