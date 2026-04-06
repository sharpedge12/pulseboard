import { useMemo } from 'react';
import { Link } from 'react-router-dom';

function RichText({ text = '' }) {
  const parts = useMemo(() => text.split(/(@[A-Za-z0-9_]{3,50})/g), [text]);

  return (
    <p>
      {parts.map((part, index) => {
        if (/^@[A-Za-z0-9_]{3,50}$/.test(part)) {
          const username = part.slice(1);
          return (
            <Link
              key={`${part}-${index}`}
              className="mention-link"
              to={`/profile/lookup/${username}`}
            >
              {part}
            </Link>
          );
        }
        return <span key={`${part}-${index}`}>{part}</span>;
      })}
    </p>
  );
}

export default RichText;
