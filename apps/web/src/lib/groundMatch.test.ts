import { fitGround, footPoint, fromLocal, type GroundPair, toLocal } from "./groundMatch";

const CAMERA = { lon: 85.862393, lat: 20.299752 };

/**
 * A synthetic phone 10 m above flat ground, facing north and tilted 20° down, 4096 × 3072 pixels. Gives the pixel where a
 * ground point (east, north metres from the camera) appears, so a fit can be checked against the truth.
 */
const HEIGHT = 10;
const PITCH = (20 * Math.PI) / 180;
const FOCAL_PX = 2000;
function pixelOf(e: number, n: number): { u: number; v: number } {
  const [dx, dy, dz] = [e, n, -HEIGHT];
  const forward = dy * Math.cos(PITCH) - dz * Math.sin(PITCH);
  const up = dy * Math.sin(PITCH) + dz * Math.cos(PITCH);
  return { u: 2048 + (FOCAL_PX * dx) / forward, v: 1536 - (FOCAL_PX * up) / forward };
}
function pair(e: number, n: number, noisePx: [number, number] = [0, 0], shiftM = 0): GroundPair {
  const { u, v } = pixelOf(e, n);
  const at = fromLocal(CAMERA, { e: e + shiftM, n });
  return { u: u + noisePx[0], v: v + noisePx[1], lon: at.lon, lat: at.lat };
}
function where(fit: ReturnType<typeof fitGround>, e: number, n: number) {
  if (!fit.ok) throw new Error(fit.reason);
  const { u, v } = pixelOf(e, n);
  return fit.project(u, v);
}
function errorM(placement: ReturnType<typeof where>, e: number, n: number): number {
  if (!placement.ok) throw new Error(placement.reason);
  const local = toLocal(CAMERA, placement);
  return Math.hypot(local.e - e, local.n - n);
}

const SPREAD: [number, number][] = [
  [-12, 25],
  [12, 25],
  [-20, 70],
  [20, 70],
  [0, 45],
  [-8, 110],
  [10, 120],
  [-3, 35],
];

describe("placing photo points on the ground from matched spots", () => {
  it("converts between degrees and local metres", () => {
    const point = fromLocal(CAMERA, { e: 120, n: -80 });
    const back = toLocal(CAMERA, point);
    expect(back.e).toBeCloseTo(120, 6);
    expect(back.n).toBeCloseTo(-80, 6);
  });

  it("recovers the ground exactly from four spots on flat ground", () => {
    const fit = fitGround(CAMERA, SPREAD.slice(0, 4).map(([e, n]) => pair(e, n)));
    expect(fit.ok).toBe(true);
    expect(errorM(where(fit, 5, 50), 5, 50)).toBeLessThan(1e-3);
    expect(errorM(where(fit, -15, 33), -15, 33)).toBeLessThan(1e-3);
    if (fit.ok) expect(fit.checkM).toBeNull(); // four spots fit exactly: nothing is left to check against
  });

  it("stays within about a metre when every click is off by a pixel", () => {
    const noise: [number, number][] = [[1, -1], [-1, 1], [1, 1], [-1, -1], [0, 1], [1, 0], [-1, 0], [0, -1]];
    const fit = fitGround(CAMERA, SPREAD.map(([e, n], i) => pair(e, n, noise[i])));
    expect(fit.ok).toBe(true);
    expect(errorM(where(fit, 4, 60), 4, 60)).toBeLessThan(1);
    if (fit.ok) {
      expect(fit.checkM).not.toBeNull();
      expect(fit.checkM!).toBeLessThan(2);
      expect(fit.residuals).toHaveLength(8);
    }
  });

  it("reports a much larger check error when one pair is matched wrongly", () => {
    const good = fitGround(CAMERA, SPREAD.slice(0, 6).map(([e, n]) => pair(e, n)));
    const bad = fitGround(CAMERA, SPREAD.slice(0, 6).map(([e, n], i) => pair(e, n, [0, 0], i === 4 ? 15 : 0)));
    expect(good.ok && bad.ok).toBe(true);
    if (good.ok && bad.ok) {
      expect(good.checkM!).toBeLessThan(0.01);
      expect(bad.checkM!).toBeGreaterThan(3);
    }
  });

  it("refuses too few spots, and spots along one line", () => {
    const three = fitGround(CAMERA, SPREAD.slice(0, 3).map(([e, n]) => pair(e, n)));
    expect(three).toMatchObject({ ok: false, reason: "Match at least 4 spots (3 so far)." });
    const line = fitGround(CAMERA, [0, 20, 40, 60, 80].map((n) => pair(0, 20 + n)));
    expect(line.ok).toBe(false);
    const threeOfFour = fitGround(CAMERA, [pair(0, 20), pair(0, 40), pair(0, 60), pair(10, 30)]);
    expect(threeOfFour).toMatchObject({ ok: false });
    if (!threeOfFour.ok) expect(threeOfFour.reason).toContain("on one line");
  });

  it("cannot place anything at or above the horizon", () => {
    const fit = fitGround(CAMERA, SPREAD.map(([e, n]) => pair(e, n)));
    if (!fit.ok) throw new Error(fit.reason);
    const horizon = 1536 - FOCAL_PX * Math.tan(PITCH);
    const sky = fit.project(2048, horizon - 300);
    expect(sky.ok).toBe(false);
  });

  it("calls a placement far outside the matched spots extrapolated", () => {
    const near = SPREAD.filter(([, n]) => n <= 45);
    const fit = fitGround(CAMERA, near.map(([e, n]) => pair(e, n)));
    const inside = where(fit, 0, 30);
    const far = where(fit, 0, 140);
    expect(inside).toMatchObject({ ok: true, extrapolated: false });
    expect(far).toMatchObject({ ok: true, extrapolated: true });
    if (inside.ok) expect(inside.distanceM).toBeCloseTo(30, 3);
  });

  it("takes the middle of a box's bottom edge as where the object touches the ground", () => {
    expect(footPoint({ type: "bbox_xyxy", coordinates: [100, 50, 300, 250] })).toEqual({ u: 200, v: 250 });
    expect(footPoint({ type: "polygon", coordinates: [[10, 10], [30, 40], [20, 60]] })).toEqual({ u: 20, v: 60 });
    expect(footPoint({ type: "bbox_xyxy", coordinates: [] })).toBeNull();
  });
});
