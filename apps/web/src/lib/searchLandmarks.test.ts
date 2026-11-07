import { vi } from "vitest";

import { searchPlaces } from "./mapbox";

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const LANDMARKS = {
  features: [
    { geometry: { coordinates: [86.0945, 19.8911] }, properties: { mapbox_id: "a", name: "Konark Sun Temple", full_address: "Konark, 752111, India" } },
    { geometry: { coordinates: [85.8196, 19.8045] }, properties: { mapbox_id: "b", name: "Puri", place_formatted: "Odisha, India" } },
  ],
};
const ADDRESSES = {
  features: [
    { id: "g1", geometry: { coordinates: [85.8196, 19.8046] }, properties: { name: "Puri", place_formatted: "Odisha, India" } },
    { id: "g2", geometry: { coordinates: [85.834, 20.2786] }, properties: { full_address: "Puri Cuttack Road, Bhubaneswar, India" } },
  ],
};

afterEach(() => vi.restoreAllMocks());

describe("searchPlaces with landmarks", () => {
  it("lists landmarks first, then addresses, each place once, preferring places near the view", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input).includes("/searchbox/") ? respond(LANDMARKS) : respond(ADDRESSES),
    );
    const matches = await searchPlaces("puri", "pk.test", undefined, { landmarks: true, near: { lon: 85.8245, lat: 20.2961 } });
    expect(matches.map((m) => m.label)).toEqual([
      "Konark Sun Temple, Konark, 752111, India",
      "Puri, Odisha, India",
      "Puri Cuttack Road, Bhubaneswar, India",
    ]);
    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls).toHaveLength(2);
    for (const url of urls) expect(url).toContain("&proximity=85.8245,20.2961");
  });

  it("still answers when one of the two services fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input).includes("/searchbox/") ? respond({}, 500) : respond(ADDRESSES),
    );
    const matches = await searchPlaces("puri", "pk.test", undefined, { landmarks: true });
    expect(matches).toHaveLength(2);
  });

  it("asks only the address geocoder when landmarks are not wanted", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(ADDRESSES));
    await searchPlaces("puri", "pk.test");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]![0])).toContain("/geocode/v6/");
  });
});
