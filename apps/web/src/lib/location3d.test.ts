import { isPoint, startView } from "./location3d";

const photo = (filename: string, state: string, lat: number | null, lon: number | null) => ({ filename, state, latitude: lat, longitude: lon });

describe("startView", () => {
  it("uses the project's area of interest first", () => {
    const view = startView({
      aoi: { type: "Polygon", coordinates: [[[85.8, 20.2], [85.9, 20.2], [85.9, 20.3], [85.8, 20.3], [85.8, 20.2]]] },
      photos: [photo("a.jpg", "confirmed", 28.6, 77.2)],
    });
    expect(view).toEqual({ source: "area of interest", label: "the project's area of interest", bounds: [85.8, 20.2, 85.9, 20.3] });
  });

  it("then the photographs a person confirmed, and never an unconfirmed GPS candidate", () => {
    const one = startView({ photos: [photo("cand.jpg", "candidate", 10, 10), photo("a.jpg", "confirmed", 20.2998, 85.8624)] });
    expect(one).toMatchObject({ source: "photographs", label: "the photograph a.jpg", bounds: [85.8624, 20.2998, 85.8624, 20.2998] });
    expect(isPoint(one!.bounds)).toBe(true);
    const two = startView({ photos: [photo("a.jpg", "confirmed", 20.1, 85.1), photo("b.jpg", "confirmed", 20.3, 85.4)] });
    expect(two).toMatchObject({ source: "photographs", label: "2 located photographs", bounds: [85.1, 20.1, 85.4, 20.3] });
  });

  it("then the extent of the project's placed rasters", () => {
    const view = startView({ photos: [photo("c.jpg", "candidate", 1, 1)], rasterBounds: [[83.9, 25.9, 89.9, 30.0], null, [86.7, 27.9, 86.8, 28.0]] });
    expect(view).toEqual({ source: "rasters", label: "the project's placed rasters", bounds: [83.9, 25.9, 89.9, 30.0] });
  });

  it("guesses nothing when the project has no location at all", () => {
    expect(startView({})).toBeNull();
    expect(startView({ aoi: null, photos: [photo("x.jpg", "none", null, null)], rasterBounds: [null] })).toBeNull();
  });

  it("ignores coordinates that cannot be on the Earth", () => {
    expect(startView({ aoi: { type: "Point", coordinates: [500, 95] } })).toBeNull();
  });
});
