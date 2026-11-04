import {
  boundsOf,
  draftGeometry,
  draftHint,
  formatArea,
  formatLength,
  formatPosition,
  MIN_POINTS,
  pointsOf,
  samePoints,
  type Position,
} from "./annotations";

const SQUARE: Position[] = [
  [75, 27],
  [75.001, 27],
  [75.001, 27.001],
  [75, 27.001],
];

describe("the geometry sent for a drawing", () => {
  it("is a GeoJSON Point, LineString or Polygon in [longitude, latitude] order", () => {
    expect(draftGeometry("point", [[75, 27]])).toEqual({ type: "Point", coordinates: [75, 27] });
    expect(draftGeometry("line", [[75, 27], [75.01, 27]])).toEqual({
      type: "LineString",
      coordinates: [[75, 27], [75.01, 27]],
    });
  });

  it("closes a polygon's outline by repeating its first position, and leaves the drawing itself unchanged", () => {
    const geometry = draftGeometry("polygon", SQUARE);
    expect(geometry).toEqual({ type: "Polygon", coordinates: [[...SQUARE, SQUARE[0]]] });
    expect(SQUARE).toHaveLength(4);
  });

  it("is null until the shape has enough positions, so an incomplete shape cannot be sent", () => {
    expect(draftGeometry("point", [])).toBeNull();
    expect(draftGeometry("line", [[75, 27]])).toBeNull();
    expect(draftGeometry("polygon", SQUARE.slice(0, 2))).toBeNull();
    expect(MIN_POINTS).toEqual({ point: 1, line: 2, polygon: 3 });
  });

  it("does not share arrays with the drawing", () => {
    const points: Position[] = [[75, 27], [75.01, 27]];
    const geometry = draftGeometry("line", points) as { coordinates: number[][] };
    geometry.coordinates[0]![0] = 1;
    expect(points[0]).toEqual([75, 27]);
  });
});

describe("what the person is told while drawing", () => {
  it("says how many positions are still needed", () => {
    expect(draftHint("line", 0)).toMatch(/first of at least 2/);
    expect(draftHint("polygon", 1)).toBe("1 position placed; add 2 more.");
    expect(draftHint("polygon", 2)).toBe("2 positions placed; add 1 more.");
    expect(draftHint("polygon", 3)).toMatch(/Add more, or save/);
    expect(draftHint("point", 1)).toMatch(/move the point/);
  });
});

describe("stored geometry as drawing positions", () => {
  it("round-trips every kind, without the polygon's closing position", () => {
    for (const [kind, points] of [
      ["point", [[75, 27]]],
      ["line", [[75, 27], [75.01, 27]]],
      ["polygon", SQUARE],
    ] as const) {
      const geometry = draftGeometry(kind, points as Position[])!;
      expect(pointsOf(geometry)).toEqual(points);
    }
  });

  it("finds bounds, and gives a lone point an area to fit", () => {
    expect(boundsOf(draftGeometry("polygon", SQUARE)!)).toEqual([75, 27, 75.001, 27.001]);
    expect(boundsOf({ type: "Point", coordinates: [75, 27] })).toEqual([74.998, 26.998, 75.002, 27.002]);
  });

  it("compares positions exactly", () => {
    expect(samePoints(SQUARE, SQUARE.map((p) => [...p] as Position))).toBe(true);
    expect(samePoints(SQUARE, SQUARE.slice(0, 3))).toBe(false);
    expect(samePoints([[75, 27]], [[75, 27.0000001]])).toBe(false);
  });
});

describe("measurements are always shown with their unit", () => {
  it("formats lengths in metres or kilometres", () => {
    expect(formatLength(4.26)).toBe("4.3 m");
    expect(formatLength(991.9)).toBe("992 m");
    expect(formatLength(1240)).toBe("1.24 km");
    expect(formatLength(12_345)).toBe("12.35 km");
  });

  it("formats areas in square metres, hectares or square kilometres, never a bare number", () => {
    expect(formatArea(950)).toBe("950 m²");
    expect(formatArea(10_980)).toBe("1.10 ha (10,980 m²)");
    expect(formatArea(2_340_000)).toBe("2.34 km²");
    for (const value of [1, 9_999, 10_000, 999_999, 1_000_000]) expect(formatArea(value)).toMatch(/(m²|ha|km²)/);
  });

  it("formats a position as latitude then longitude", () => {
    expect(formatPosition([75.5, 27.25])).toBe("27.250000°, 75.500000°");
  });
});
