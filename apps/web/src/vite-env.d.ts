/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** A Mapbox public access token (pk.…), for the optional online satellite basemap. Never a secret token. */
  readonly VITE_MAPBOX_ACCESS_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
