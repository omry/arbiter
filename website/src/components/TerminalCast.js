import React from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';

export default function TerminalCast({
  src,
  title,
  audio,
  audioMeta,
  intro,
  introSegment,
  introSeconds,
}) {
  const castSrc = useBaseUrl(src);
  const audioSrc = audio ? useBaseUrl(audio) : null;
  const audioMetaSrc = audioMeta ? useBaseUrl(audioMeta) : null;
  const playerSrc = useBaseUrl('/cast-player.html');
  const params = new URLSearchParams({
    cast: castSrc,
    title,
  });
  if (audioSrc) {
    params.set('audio', audioSrc);
  }
  if (audioMetaSrc) {
    params.set('audioMeta', audioMetaSrc);
  }
  if (intro) {
    params.set('intro', intro);
  }
  if (introSegment) {
    params.set('introSegment', introSegment);
  }
  if (introSeconds != null) {
    params.set('introSeconds', String(introSeconds));
  }

  return (
    <div className="aa-terminal">
      <iframe
        title={title}
        src={`${playerSrc}?${params.toString()}`}
        loading="lazy"
        allowFullScreen
        allow="autoplay"
      />
    </div>
  );
}
