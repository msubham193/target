import type { TerrainMeta } from "../api/client";
import {
  buildMesh,
  cameraFor,
  cellAt,
  cellPosition,
  distanceLimits,
  eyeOf,
  FOV,
  formatElevation,
  formatMetres,
  invert,
  lookAt,
  MAX_PITCH,
  MIN_PITCH,
  multiply,
  nativePosition,
  orbit,
  pan,
  perspective,
  pick,
  rampColour,
  rayThrough,
  sceneOf,
  valueAt,
  zoom,
} from "./terrain";

function meta(cols: number, rows: number, over: Partial<TerrainMeta["elevation"]> = {}, cell = [10, 10]): Pick<TerrainMeta, "grid" | "cell_size_m" | "elevation"> {
  return {
    grid: { width: cols, height: rows, decimation: 1, method: "nearest" },
    cell_size_m: cell,
    elevation: { unit: "metre", unit_label: "m", unit_source: "declared", to_metre: 1, role_source: "declared", declared_scale_offset_applied: false, nodata_value: null, valid_cells: cols * rows, nodata_cells: 0, min: 0, max: 0, mean: 0, ...over },
  };
}

/** A grid whose value is a function of (col,row), with the matching metadata (min/max measured from the values). */
function scene(cols: number, rows: number, f: (c: number, r: number) => number, over: Partial<TerrainMeta["elevation"]> = {}, cell = [10, 10]) {
  const values = new Float32Array(cols * rows);
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) values[r * cols + c] = f(c, r);
  const valid = Array.from(values).filter((v) => !Number.isNaN(v));
  return sceneOf(values, meta(cols, rows, { min: Math.min(...valid), max: Math.max(...valid), ...over }, cell));
}

const near = (a: number, b: number, digits = 6) => expect(a).toBeCloseTo(b, digits);

describe("matrices", () => {
  it("inverts a view-projection and multiplies back to the identity", () => {
    const vp = multiply(perspective(FOV, 1.5, 0.5, 5000), lookAt([30, -80, 60], [10, 10, 0]));
    const inverse = invert(vp)!;
    const product = multiply(vp, inverse);
    for (let i = 0; i < 16; i++) near(product[i]!, i % 5 === 0 ? 1 : 0, 3);
  });

  it("has no inverse for a matrix that cannot be inverted", () => {
    expect(invert(new Float32Array(16))).toBeNull();
  });
});

describe("the scene", () => {
  it("places cell centres in metres, north up, and finds the cell under a position", () => {
    const s = scene(5, 4, () => 10, {}, [20, 30]);
    expect([s.width, s.depth]).toEqual([80, 90]);
    expect(cellPosition(s, 0, 0)).toEqual({ x: 0, y: 90 }); // the north-west cell is the north-west corner
    expect(cellPosition(s, 4, 3)).toEqual({ x: 80, y: 0 });
    expect(cellAt(s, 0, 90)).toEqual({ col: 0, row: 0 });
    expect(cellAt(s, 79, 1)).toEqual({ col: 4, row: 3 });
    expect(cellAt(s, -11, 0)).toBeNull();
    expect(cellAt(s, 0, 200)).toBeNull();
  });

  it("refuses a grid that is not the size its description gave", () => {
    expect(() => sceneOf(new Float32Array(10), meta(4, 4))).toThrow(RangeError);
  });

  it("states the relief in metres using the unit conversion, and no-data has no value", () => {
    const s = scene(3, 3, (c, r) => (c === 1 && r === 1 ? Number.NaN : 100 + c * 50), { to_metre: 0.3048 });
    near(s.relief, 100 * 0.3048);
    expect(valueAt(s, 1, 1)).toBeNull();
    expect(valueAt(s, 2, 0)).toBe(200);
    expect(valueAt(s, -1, 0)).toBeNull();
    expect(valueAt(s, 3, 0)).toBeNull();
  });
});

describe("the mesh", () => {
  it("has two triangles per cell of four valid corners, at the source heights relative to the lowest cell", () => {
    const s = scene(3, 3, (c, r) => 100 + r * 10 + c);
    const mesh = buildMesh(s);
    expect(mesh.triangles).toBe(8); // 2 x 2 quads
    expect(mesh.skippedCells).toBe(0);
    const i = 1 * 3 + 2; // row 1, col 2
    expect(Array.from(mesh.positions.slice(i * 3, i * 3 + 3))).toEqual([20, 10, 12]); // x, y (north = up), z above the lowest (100)
    expect(mesh.heights[i]).toBe(112); // the source value itself, for colouring
  });

  it("leaves a hole where there is no data: no triangle touches a no-data cell, and nothing is filled in", () => {
    const s = scene(4, 4, (c, r) => (c === 1 && r === 1 ? Number.NaN : 50 + c));
    const mesh = buildMesh(s);
    // the four quads around the hole are skipped
    expect(mesh.skippedCells).toBe(4);
    expect(mesh.triangles).toBe(2 * (9 - 4));
    const hole = 1 * 4 + 1;
    expect(mesh.indices.includes(hole)).toBe(false);
    expect(Number.isNaN(mesh.heights[hole]!)).toBe(true); // still no value
  });

  it("gives a plane a constant slope in the right direction and a flat surface none", () => {
    const rising = buildMesh(scene(4, 4, (c) => c * 5)); // 5 m per 10 m cell to the east
    for (let i = 0; i < 16; i++) {
      near(rising.slopes[i * 2]!, 0.5);
      near(rising.slopes[i * 2 + 1]!, 0);
    }
    const north = buildMesh(scene(4, 4, (_c, r) => (3 - r) * 4)); // rows run north to south, so this rises to the north
    near(north.slopes[1]!, 0.4);
    const flat = buildMesh(scene(4, 4, () => 7));
    expect(Array.from(flat.slopes).every((v) => v === 0)).toBe(true);
  });

  it("does not let a hole pull a slope towards a made-up value", () => {
    const m = buildMesh(scene(3, 1, (c) => (c === 2 ? Number.NaN : c * 10)));
    near(m.slopes[0]!, 1); // from its one valid neighbour
    near(m.slopes[2]!, 1);
    expect(m.slopes[4]).toBe(0); // the no-data cell itself
  });
});

describe("the camera", () => {
  const s = scene(21, 21, () => 0, {}, [10, 10]); // 200 m square, flat

  it("starts looking at the middle of the surface from the south, north up, and frames it", () => {
    const c = cameraFor(s);
    expect(c.target[0]).toBe(100);
    expect(c.target[1]).toBe(100);
    const eye = eyeOf({ ...c, yaw: 0 });
    expect(eye[1]).toBeLessThan(c.target[1]); // south of the target
    near(eye[0], c.target[0], 6);
    expect(eye[2]).toBeGreaterThan(c.target[2]);
    expect(cameraFor(s, "top").pitch).toBe(MAX_PITCH);
    near(cameraFor(s, "north").yaw, Math.PI);
  });

  it("orbits: dragging right turns the scene to the right, dragging down raises the camera, and it never flips", () => {
    const c = cameraFor(s);
    expect(orbit(c, 50, 0).yaw).toBeLessThan(c.yaw);
    expect(orbit(c, 0, 20).pitch).toBeGreaterThan(c.pitch);
    expect(orbit(c, 0, 1e6).pitch).toBe(MAX_PITCH);
    expect(orbit(c, 0, -1e6).pitch).toBe(MIN_PITCH);
    expect(orbit(c, 100, 100).distance).toBe(c.distance);
  });

  it("zooms by distance within limits and keeps the target", () => {
    const c = cameraFor(s);
    const [lo, hi] = distanceLimits(s);
    expect(zoom(c, -0.3, s).distance).toBeLessThan(c.distance);
    expect(zoom(c, 0.3, s).distance).toBeGreaterThan(c.distance);
    expect(zoom(c, -100, s).distance).toBe(lo);
    expect(zoom(c, 100, s).distance).toBe(hi);
    expect(zoom(c, -0.3, s).target).toEqual(c.target);
  });

  it("pans along the ground the way the drag goes, and stays near the surface", () => {
    const c = { ...cameraFor(s), yaw: 0 };
    const right = pan(c, 100, 0, 600, s); // drag right: the scene moves right, so the target moves west
    expect(right.target[0]).toBeLessThan(c.target[0]);
    near(right.target[1], c.target[1], 6);
    const up = pan(c, 0, -100, 600, s); // drag up: the scene moves up the screen, so the target moves north... away from the viewer
    expect(up.target[1]).toBeGreaterThan(c.target[1]);
    const far = pan(c, -1e7, -1e7, 600, s);
    expect(far.target[0]).toBeLessThanOrEqual(s.width + 0.25 * 200 + 1e-6);
    expect(far.target[1]).toBeLessThanOrEqual(s.depth + 0.25 * 200 + 1e-6);
    expect(far.target[2]).toBe(c.target[2]);
  });

  it("turns the pan direction with the view", () => {
    const c = { ...cameraFor(s), yaw: Math.PI / 2 }; // from the east, looking west
    const moved = pan(c, 0, -100, 600, s);
    expect(moved.target[0]).toBeLessThan(c.target[0]); // up the screen is now west
  });
});

describe("picking", () => {
  const flat = scene(21, 21, () => 100, {}, [10, 10]);

  it("returns the cell and value under the pointer, in the right place", () => {
    const camera = { ...cameraFor(flat), yaw: 0, pitch: MAX_PITCH, target: [100, 100, 0] as [number, number, number] };
    const ray = rayThrough(camera, flat, 400, 300, 800, 600)!; // the centre of the view
    const hit = pick(flat, ray.origin, ray.direction, 1)!;
    expect(hit).not.toBeNull();
    expect(hit.value).toBe(100);
    expect(Math.abs(hit.x - 100)).toBeLessThan(15);
    expect(Math.abs(hit.y - 100)).toBeLessThan(15);
    expect(hit.col).toBeGreaterThanOrEqual(9);
    expect(hit.col).toBeLessThanOrEqual(11);
  });

  it("hits a point to the right of the centre on the right", () => {
    const camera = { ...cameraFor(flat), yaw: 0, pitch: MAX_PITCH, target: [100, 100, 0] as [number, number, number] };
    const centreRay = rayThrough(camera, flat, 400, 300, 800, 600)!;
    const centre = pick(flat, centreRay.origin, centreRay.direction, 1)!;
    const rightRay = rayThrough(camera, flat, 600, 300, 800, 600)!;
    const right = pick(flat, rightRay.origin, rightRay.direction, 1)!;
    expect(right.x).toBeGreaterThan(centre.x + 5);
    expect(right.col).toBeGreaterThan(centre.col);
  });

  it("misses when the pointer is off the surface", () => {
    const camera = { ...cameraFor(flat), yaw: 0, pitch: MAX_PITCH, distance: 4000, target: [100, 100, 0] as [number, number, number] };
    const ray = rayThrough(camera, flat, 5, 5, 800, 600)!;
    expect(pick(flat, ray.origin, ray.direction, 1)).toBeNull();
  });

  it("sees the near side of a ridge, not the ground behind it, and the exaggeration moves the hit", () => {
    const ridge = scene(41, 21, (c) => (c === 20 ? 150 : 100), {}, [10, 10]); // a 50 m wall across the middle
    const eye: [number, number, number] = [-50, 100, 40];
    const length = Math.hypot(250, 0, -20);
    const direction: [number, number, number] = [250 / length, 0, -20 / length];
    const hit = pick(ridge, eye, direction, 1)!;
    expect(hit).not.toBeNull();
    expect(hit.col).toBeLessThanOrEqual(20); // the wall or ground before it
    expect(hit.col).toBeLessThan(25);
    const lowGround = pick(ridge, [-50, 100, 5], [1, 0, -0.0001], 1);
    expect(lowGround?.col ?? 0).toBeLessThanOrEqual(20); // a grazing ray stops at the wall, never behind it
  });

  it("passes through a no-data hole instead of inventing a surface there", () => {
    const holey = scene(21, 21, (c, r) => (c >= 8 && c <= 12 && r >= 8 && r <= 12 ? Number.NaN : 100), {}, [10, 10]);
    const camera = { ...cameraFor(holey), yaw: 0, pitch: MAX_PITCH, target: [100, 100, 0] as [number, number, number] };
    const ray = rayThrough(camera, holey, 400, 300, 800, 600)!;
    expect(pick(holey, ray.origin, ray.direction, 1)).toBeNull(); // straight down through the hole: nothing there
    const edge = rayThrough(camera, holey, 400 + 190, 300, 800, 600)!;
    expect(pick(holey, edge.origin, edge.direction, 1)).not.toBeNull(); // beside the hole there is ground
  });
});

describe("picking through a hole", () => {
  it("never reports ground the ray could only reach by passing underneath the surface through a hole", () => {
    // flat ground with a no-data band across it: a ray above the ground that dips below ground level inside the band
    // has no surface to hit there, and must not "hit" the ground on the far side from below
    const holey = scene(31, 21, (c) => (c >= 8 && c <= 12 ? Number.NaN : 100), {}, [10, 10]);
    // a shallow ray (it stays within the scene's box): above the ground at the hole's near edge, 2 m below it at the far edge
    const length = Math.hypot(1, 0, -0.06);
    const direction: [number, number, number] = [1 / length, 0, -0.06 / length];
    const origin: [number, number, number] = [0, 100, 5.5];
    expect(pick(holey, origin, direction, 1)).toBeNull();
    // the same ray over ground with no hole does hit it
    const solid = scene(31, 21, () => 100, {}, [10, 10]);
    expect(pick(solid, origin, direction, 1)).not.toBeNull();
  });
});

describe("colour and words", () => {
  it("runs the ramp from dark to light across the range and clamps outside it", () => {
    const low = rampColour(0);
    const high = rampColour(1);
    expect(rampColour(-5)).toEqual(low);
    expect(rampColour(5)).toEqual(high);
    expect(high[0] + high[1] + high[2]).toBeGreaterThan(low[0] + low[1] + low[2]); // lighter at the top, readable in greyscale
    expect(rampColour(0.5)).not.toEqual(low);
  });

  it("states elevations with their unit, and distances in m or km", () => {
    expect(formatElevation(1234.56, "m")).toBe("1,234.6 m");
    expect(formatElevation(12.345, "ft")).toBe("12.35 ft");
    expect(formatMetres(450)).toBe("450 m");
    expect(formatMetres(12_500)).toBe("12.5 km");
  });

  it("gives the native coordinates of a cell centre from the extent the server stated", () => {
    const m = { extent_native: { x_min: 500000, y_min: 2999600, x_max: 500600, y_max: 3000000 }, cell_size_native: [10, 10] } as TerrainMeta;
    expect(nativePosition(m, 0, 0)).toEqual({ x: 500005, y: 2999995 });
    expect(nativePosition(m, 59, 39)).toEqual({ x: 500595, y: 2999605 });
  });
});
