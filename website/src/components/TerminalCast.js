import React from 'react';

export default function TerminalCast({src, title}) {
  return (
    <div className="aa-terminal">
      <iframe
        title={title}
        src={src}
        loading="lazy"
        allowFullScreen
      />
    </div>
  );
}
