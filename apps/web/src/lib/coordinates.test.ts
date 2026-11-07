import { formatLatLon, parseCoordinateText } from "./coordinates";

const at = (lat: number, lon: number) => ({ kind: "coordinates", lat, lon });

describe("parseCoordinateText", () => {
  it.each([
    ["20.2961, 85.8245", 20.2961, 85.8245],
    ["20.2961 85.8245", 20.2961, 85.8245],
    ["  -33.8688 ; 151.2093 ", -33.8688, 151.2093],
    ["20.2961 N, 85.8245 E", 20.2961, 85.8245],
    ["20.2961N 85.8245E", 20.2961, 85.8245],
    ["N 20.2961 E 85.8245", 20.2961, 85.8245],
    ["85.8245 E 20.2961 N", 20.2961, 85.8245],
    ["40.7128° N, 74.0060° W", 40.7128, -74.006],
    ["33.8688 S, 151.2093 E", -33.8688, 151.2093],
  ])("reads %s", (text, lat, lon) => {
    expect(parseCoordinateText(text)).toEqual(at(lat, lon));
  });

  it("reads degrees, minutes and seconds", () => {
    const result = parseCoordinateText(`20°17'46.0"N 85°49'28.2"E`);
    expect(result.kind).toBe("coordinates");
    if (result.kind !== "coordinates") return;
    expect(result.lat).toBeCloseTo(20.296111, 5);
    expect(result.lon).toBeCloseTo(85.824500, 5);
    const minutes = parseCoordinateText("20°17.766'N, 85°49.47'E");
    expect(minutes.kind === "coordinates" && minutes.lat).toBeCloseTo(20.2961, 4);
  });

  it("treats place names as text to search for", () => {
    expect(parseCoordinateText("Bhubaneswar")).toEqual({ kind: "text" });
    expect(parseCoordinateText("Sector 5, Salt Lake")).toEqual({ kind: "text" });
    expect(parseCoordinateText("")).toEqual({ kind: "text" });
    expect(parseCoordinateText("NEWS")).toEqual({ kind: "text" });
  });

  it("explains positions it cannot accept, and never swaps them silently", () => {
    const swapped = parseCoordinateText("85.8245, 20.2961".replace("85.8245", "185.8245"));
    expect(swapped.kind).toBe("invalid");
    const reversed = parseCoordinateText("120.5, 20.3");
    expect(reversed).toEqual({
      kind: "invalid",
      message: "Latitude must be between -90 and 90. The latitude comes first: did you mean 20.3, 120.5?",
    });
    expect(parseCoordinateText("20 N, 30 S")).toMatchObject({ kind: "invalid" });
    expect(parseCoordinateText("-20 N, 30 E")).toMatchObject({ kind: "invalid" });
    expect(parseCoordinateText("20°75'N 85E")).toMatchObject({ kind: "invalid" });
    expect(parseCoordinateText("88, 10")).toMatchObject({ kind: "invalid" });
    expect(parseCoordinateText("20, 190")).toMatchObject({ kind: "invalid" });
    expect(parseCoordinateText("20.5")).toMatchObject({ kind: "invalid" });
  });
});

describe("formatLatLon", () => {
  it("shows hemispheres instead of signs", () => {
    expect(formatLatLon(20.2961, 85.8245)).toBe("20.29610° N, 85.82450° E");
    expect(formatLatLon(-33.8688, -70.5, 2)).toBe("33.87° S, 70.50° W");
  });
});
