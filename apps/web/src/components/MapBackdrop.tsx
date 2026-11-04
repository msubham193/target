import { useEffect, useMemo, useState } from "react";

import terrainUrl from "../assets/login-terrain.svg";
import { readMapboxToken, satelliteBackdropUrl } from "../lib/mapbox";

/**
 * A darkened map behind a screen. With a Mapbox public token it is a dim satellite picture (a live request, never saved);
 * the locally drawn contour map is always laid over it, and is the whole backdrop when there is no token or no network.
 * It fills the nearest positioned ancestor and sits behind its content; nothing on the screen depends on it.
 */
export function MapBackdrop({ className = "" }: { className?: string }) {
  const imageUrl = useMemo(() => {
    const token = readMapboxToken();
    return token.ok ? satelliteBackdropUrl(token.token) : null;
  }, []);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!imageUrl) return;
    const image = new Image();
    image.onload = () => setReady(true);
    image.src = imageUrl;
    return () => {
      image.onload = null;
    };
  }, [imageUrl]);

  return (
    <div aria-hidden="true" data-testid="map-backdrop" className={`pointer-events-none absolute inset-0 -z-10 overflow-hidden ${className}`}>
      {imageUrl ? (
        <div
          data-testid="map-backdrop-satellite"
          className="login-drift absolute -inset-4 bg-cover bg-center transition-opacity duration-1000"
          style={{
            backgroundImage: `url("${imageUrl}")`,
            filter: "brightness(0.42) saturate(0.8) contrast(1.08)",
            opacity: ready ? 1 : 0,
          }}
        />
      ) : null}
      <div
        className="absolute inset-0 bg-accent opacity-25"
        style={{
          maskImage: `url(${terrainUrl})`,
          WebkitMaskImage: `url(${terrainUrl})`,
          maskSize: "cover",
          WebkitMaskSize: "cover",
          maskPosition: "center",
          WebkitMaskPosition: "center",
          maskRepeat: "no-repeat",
          WebkitMaskRepeat: "no-repeat",
        }}
      />
      {/* Darkens the whole map, lightly glows at the top left, and fades into the page colour at the edges. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse at 15% 0%, rgb(91 155 255 / 0.14), transparent 55%)," +
            "linear-gradient(to bottom, rgb(14 16 20 / 0.2), rgb(14 16 20 / 0.45) 60%, var(--color-canvas) 100%)",
        }}
      />
    </div>
  );
}
