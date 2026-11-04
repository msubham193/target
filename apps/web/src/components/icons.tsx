import type { SVGProps } from "react";

/**
 * A small set of line icons drawn for this project (24 x 24 grid, 1.75 stroke). Icons are decorative by default;
 * every icon-only control carries its own accessible label.
 */
type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Base({ size = 16, children, ...props }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export const IconDashboard = (p: IconProps) => (
  <Base {...p}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="4.5" rx="1.5" />
    <rect x="13.5" y="11" width="7" height="9.5" rx="1.5" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
  </Base>
);
export const IconPlus = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 5v14M5 12h14" />
  </Base>
);
export const IconFolderPlus = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
    <path d="M12 11.5v5M9.5 14h5" />
  </Base>
);
export const IconUpload = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 15.5V4.5M7.5 9 12 4.5 16.5 9" />
    <path d="M4.5 15v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
  </Base>
);
export const IconMap = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 6.5 9 4l6 2.5 5.5-2.5v13.5L15 20l-6-2.5-5.5 2.5z" />
    <path d="M9 4v13.5M15 6.5V20" />
  </Base>
);
export const IconImage = (p: IconProps) => (
  <Base {...p}>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
    <circle cx="9" cy="10" r="1.75" />
    <path d="m4 17.5 5-4.5 3.5 3 3-2.5 4.5 4" />
  </Base>
);
export const IconSatellite = (p: IconProps) => (
  <Base {...p}>
    <rect x="9.5" y="9.5" width="5" height="5" rx="1" transform="rotate(45 12 12)" />
    <path d="m5 5 3 3M16 16l3 3M8 3.5 3.5 8M20.5 16 16 20.5" />
  </Base>
);
export const IconLeaf = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 19c0-8 4.5-13.5 14-14.5 0 9.5-5.5 14.5-14 14.5Z" />
    <path d="M5 19 13.5 10.5" />
  </Base>
);
export const IconVideo = (p: IconProps) => (
  <Base {...p}>
    <rect x="3.5" y="6" width="12.5" height="12" rx="2" />
    <path d="m16 10.5 4.5-2.5v8l-4.5-2.5" />
  </Base>
);
export const IconCube = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3.5 20 8v8l-8 4.5L4 16V8z" />
    <path d="M4 8l8 4.5L20 8M12 12.5v8" />
  </Base>
);
export const IconList = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 6.5h11M9 12h11M9 17.5h11" />
    <path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" strokeWidth={2.5} />
  </Base>
);
export const IconReport = (p: IconProps) => (
  <Base {...p}>
    <path d="M6.5 3.5h7.5l4.5 4.5v12.5h-12z" />
    <path d="M14 3.5V8h4.5M9 12.5h6M9 16h6" />
  </Base>
);
export const IconSettings = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3.5v2.5M12 18v2.5M3.5 12H6M18 12h2.5M6 6l1.8 1.8M16.2 16.2 18 18M6 18l1.8-1.8M16.2 7.8 18 6" />
  </Base>
);
export const IconChevronLeft = (p: IconProps) => (
  <Base {...p}>
    <path d="m14.5 6-6 6 6 6" />
  </Base>
);
export const IconChevronRight = (p: IconProps) => (
  <Base {...p}>
    <path d="m9.5 6 6 6-6 6" />
  </Base>
);
export const IconChevronDown = (p: IconProps) => (
  <Base {...p}>
    <path d="m6 9.5 6 6 6-6" />
  </Base>
);
export const IconMenu = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Base>
);
export const IconClose = (p: IconProps) => (
  <Base {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Base>
);
export const IconSearch = (p: IconProps) => (
  <Base {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4.5 4.5" />
  </Base>
);
export const IconZoomIn = (p: IconProps) => (
  <Base {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4.5 4.5M8.5 11h5M11 8.5v5" />
  </Base>
);
export const IconZoomOut = (p: IconProps) => (
  <Base {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4.5 4.5M8.5 11h5" />
  </Base>
);
export const IconFit = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15" />
  </Base>
);
export const IconFullscreen = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" />
  </Base>
);
export const IconExitFullscreen = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5" />
  </Base>
);
export const IconInfo = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.75h.01" />
  </Base>
);
export const IconAlert = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 4 21 19.5H3z" />
    <path d="M12 10v4.5M12 17.25h.01" />
  </Base>
);
export const IconCheck = (p: IconProps) => (
  <Base {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Base>
);
export const IconClock = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Base>
);
export const IconDownload = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 4.5v11M7.5 11 12 15.5 16.5 11" />
    <path d="M4.5 15v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
  </Base>
);
export const IconLayers = (p: IconProps) => (
  <Base {...p}>
    <path d="m12 4 8.5 4.5L12 13 3.5 8.5z" />
    <path d="m3.5 12.5 8.5 4.5 8.5-4.5M3.5 16.5 12 21l8.5-4.5" />
  </Base>
);
export const IconFolder = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
  </Base>
);
export const IconUser = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="8.5" r="3.5" />
    <path d="M5 20c1-3.5 3.8-5.5 7-5.5s6 2 7 5.5" />
  </Base>
);
export const IconStepBack = (p: IconProps) => (
  <Base {...p}>
    <path d="M6.5 5.5v13M18 6l-8 6 8 6z" />
  </Base>
);
export const IconStepForward = (p: IconProps) => (
  <Base {...p}>
    <path d="M17.5 5.5v13M6 6l8 6-8 6z" />
  </Base>
);
export const IconWifiOff = (p: IconProps) => (
  <Base {...p}>
    <path d="m3.5 3.5 17 17M8.5 12.5a5 5 0 0 1 3.5-1.4M5 9a10 10 0 0 1 3.4-2.1M15.6 7a10 10 0 0 1 3.4 2M12 18h.01" />
  </Base>
);
export const IconGlobe = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.5 2.3 3.75 5.1 3.75 8.5S14.5 18.2 12 20.5C9.5 18.2 8.25 15.4 8.25 12S9.5 5.8 12 3.5z" />
  </Base>
);
export const IconDatabase = (p: IconProps) => (
  <Base {...p}>
    <ellipse cx="12" cy="6" rx="7.5" ry="2.5" />
    <path d="M4.5 6v12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5V6M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5" />
  </Base>
);
export const IconCpu = (p: IconProps) => (
  <Base {...p}>
    <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />
    <path d="M9.5 3.5v3M14.5 3.5v3M9.5 17.5v3M14.5 17.5v3M3.5 9.5h3M3.5 14.5h3M17.5 9.5h3M17.5 14.5h3" />
  </Base>
);
export const IconSliders = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 4.5v15M12 4.5v15M19 4.5v15" />
    <path d="M3 14h4M10 9h4M17 15.5h4" />
  </Base>
);
