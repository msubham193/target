import { distanceMetres } from "./compare";
import {
  bearingInPicture,
  bearingTo,
  classColourHex,
  destination,
  formatBearing,
  normaliseDegrees,
  viewOutline,
  viewWidthFromFocal,
} from "./sightLines";

const CAMERA = { lon: 85.862393, lat: 20.299752 };

describe("direction lines from a photo", () => {
  it("works out the width of view from the 35 mm equivalent focal length and the picture's shape", () => {
    // A phone main camera, 26 mm equivalent, 4:3 landscape: about 67° across.
    expect(viewWidthFromFocal(26, 4096, 3072)).toBeCloseTo(67.3, 1);
    // The same lens on a portrait picture sees less from side to side.
    expect(viewWidthFromFocal(26, 3072, 4096)!).toBeLessThan(55);
    expect(viewWidthFromFocal(0, 4096, 3072)).toBeNull();
  });

  it("gives the centre column the camera's own direction and the edges half the view either side", () => {
    expect(bearingInPicture(90, 60, 2048, 4096)).toBeCloseTo(90, 6);
    expect(bearingInPicture(90, 60, 0, 4096)).toBeCloseTo(60, 6);
    expect(bearingInPicture(90, 60, 4096, 4096)).toBeCloseTo(120, 6);
    // A quarter of the way across is not a quarter of the angle: the picture is flat, the angles are not.
    expect(bearingInPicture(0, 60, 1024, 4096)).toBeCloseTo(360 - 16.1, 1);
    // Across north the direction wraps round.
    expect(bearingInPicture(10, 60, 0, 4096)).toBeCloseTo(340, 6);
  });

  it("finds the point a distance away along a direction, and the direction back", () => {
    for (const bearing of [0, 45, 90, 212.5, 300]) {
      const end = destination(CAMERA, bearing, 250);
      expect(distanceMetres(CAMERA, end)).toBeCloseTo(250, 0);
      expect(bearingTo(CAMERA, end)).toBeCloseTo(bearing, 3);
    }
    expect(destination(CAMERA, 0, 1000).lat).toBeGreaterThan(CAMERA.lat);
    expect(destination(CAMERA, 90, 1000).lon).toBeGreaterThan(CAMERA.lon);
  });

  it("writes directions in degrees with a compass point", () => {
    expect(formatBearing(212.4)).toBe("212° SW");
    expect(formatBearing(359.7)).toBe("0° N");
    expect(formatBearing(-90)).toBe("270° W");
    expect(normaliseDegrees(-0)).toBe(0);
  });

  it("outlines the view as a closed shape starting at the camera", () => {
    const ring = viewOutline(CAMERA, 90, 60, 200, 12);
    expect(ring[0]).toEqual([CAMERA.lon, CAMERA.lat]);
    expect(ring.at(-1)).toEqual([CAMERA.lon, CAMERA.lat]);
    expect(ring).toHaveLength(15);
    const firstEdge = { lon: ring[1]![0], lat: ring[1]![1] };
    expect(bearingTo(CAMERA, firstEdge)).toBeCloseTo(60, 3);
  });

  it("gives each class one colour in a form the map can read", () => {
    expect(classColourHex("car")).toMatch(/^#[0-9a-f]{6}$/);
    expect(classColourHex("Car")).toBe(classColourHex("car"));
  });
});
