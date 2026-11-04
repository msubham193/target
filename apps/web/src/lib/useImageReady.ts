import { useEffect, useState } from "react";

/** True once the picture at `url` has loaded. Until then (or if it never does) the page simply shows without it. */
export function useImageReady(url: string | null): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!url) return;
    const image = new Image();
    image.onload = () => setReady(true);
    image.src = url;
    return () => {
      image.onload = null;
    };
  }, [url]);
  return ready;
}
