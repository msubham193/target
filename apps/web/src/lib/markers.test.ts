import { describe, expect, it } from "vitest";

import { chooseMarker } from "./markers";

const at = (id: string, x: number, y: number) => ({ id, x, y });

describe("chooseMarker", () => {
  it("is an ordinary map click when no marker is near", () => {
    expect(chooseMarker({ x: 100, y: 100 }, [at("a", 150, 100)])).toEqual({ kind: "none" });
    expect(chooseMarker({ x: 100, y: 100 }, [])).toEqual({ kind: "none" });
  });

  it("selects the only marker near the click", () => {
    expect(chooseMarker({ x: 104, y: 100 }, [at("a", 100, 100), at("far", 300, 300)])).toEqual({ kind: "one", id: "a" });
  });

  it("selects the nearest of two overlapping markers when it is clearly nearer", () => {
    // The smoke-test case: two photographs about 7 px apart, clicked on the centre of one of them.
    expect(chooseMarker({ x: 100, y: 100 }, [at("void", 105, 105), at("gps", 100, 100)])).toEqual({ kind: "one", id: "gps" });
    expect(chooseMarker({ x: 105, y: 105 }, [at("void", 105, 105), at("gps", 100, 100)])).toEqual({ kind: "one", id: "void" });
  });

  it("selects a marker by its centre even when its neighbour is only 5.5 px away (photographs 30 m apart)", () => {
    expect(chooseMarker({ x: 200, y: 200 }, [at("void", 197, 204.6), at("gps", 200, 200)])).toEqual({ kind: "one", id: "gps" });
    expect(chooseMarker({ x: 197, y: 204.6 }, [at("void", 197, 204.6), at("gps", 200, 200)])).toEqual({ kind: "one", id: "void" });
  });

  it("asks for a click midway between two markers 5.5 px apart", () => {
    expect(chooseMarker({ x: 198.5, y: 202.3 }, [at("void", 197, 204.6), at("gps", 200, 200)])).toEqual({ kind: "several", ids: ["void", "gps"] });
  });

  it("does not depend on the order the map returned the markers in", () => {
    const markers = [at("a", 92, 100), at("b", 100, 100), at("c", 108, 100)];
    for (const order of [markers, [...markers].reverse(), [markers[1]!, markers[2]!, markers[0]!]]) {
      expect(chooseMarker({ x: 100, y: 100 }, order)).toEqual({ kind: "one", id: "b" });
    }
  });

  it("asks when a click is between markers and cannot say which was meant, nearest first", () => {
    expect(chooseMarker({ x: 102, y: 100 }, [at("b", 106, 100), at("a", 98, 100), at("c", 140, 100)])).toEqual({ kind: "several", ids: ["a", "b"] });
  });

  it("asks when markers sit on top of each other", () => {
    expect(chooseMarker({ x: 100, y: 100 }, [at("a", 100, 100), at("b", 100, 100)])).toEqual({ kind: "several", ids: ["a", "b"] });
  });

  it("selects a marker 14 px from its neighbour when clicked on its centre", () => {
    expect(chooseMarker({ x: 100, y: 100 }, [at("plain", 110, 90), at("gps", 100, 100)])).toEqual({ kind: "one", id: "gps" });
  });

  it("honours a custom radius", () => {
    expect(chooseMarker({ x: 100, y: 100 }, [at("a", 120, 100)], 25)).toEqual({ kind: "one", id: "a" });
  });
});
