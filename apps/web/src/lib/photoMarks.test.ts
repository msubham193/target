import { act, renderHook } from "@testing-library/react";

import { anchorOf, boxFromCorners, loadMarks, usePhotoMarks } from "./photoMarks";

const SIZE = { width: 1000, height: 800 };

describe("boxFromCorners", () => {
  it("makes a box from any two opposite corners and keeps it inside the picture", () => {
    expect(boxFromCorners([300, 200], [100, 50], SIZE, 5)).toEqual({ kind: "box", x: 100, y: 50, width: 200, height: 150 });
    expect(boxFromCorners([-40, 700], [1200, 900], SIZE, 5)).toEqual({ kind: "box", x: 0, y: 700, width: 1000, height: 100 });
  });
  it("ignores a stray click that is too small to be a box", () => {
    expect(boxFromCorners([10, 10], [12, 11], SIZE, 6)).toBeNull();
  });
});

describe("anchorOf", () => {
  it("anchors a label at the top-left of each kind of mark", () => {
    expect(anchorOf({ kind: "box", x: 5, y: 6, width: 10, height: 10 })).toEqual([5, 6]);
    expect(anchorOf({ kind: "point", x: 7, y: 8 })).toEqual([7, 8]);
    expect(anchorOf({ kind: "outline", points: [[30, 40], [10, 90], [60, 20]] })).toEqual([10, 20]);
  });
});

describe("usePhotoMarks", () => {
  it("keeps marks per photograph in this browser, and they survive a reload", () => {
    const first = renderHook(() => usePhotoMarks("a1"));
    act(() => {
      first.result.current.add("car", { kind: "point", x: 1, y: 2 });
    });
    expect(first.result.current.marks).toHaveLength(1);
    expect(loadMarks("a1")).toHaveLength(1);
    expect(loadMarks("a2")).toHaveLength(0);
    first.unmount();
    expect(renderHook(() => usePhotoMarks("a1")).result.current.marks[0]).toMatchObject({ label: "car", shape: { kind: "point" } });
  });

  it("renames, adds a note, deletes one, and clears all", () => {
    const { result } = renderHook(() => usePhotoMarks("a3"));
    let id = "";
    act(() => {
      id = result.current.add("  ", { kind: "box", x: 0, y: 0, width: 10, height: 10 }).id;
      result.current.add("truck", { kind: "point", x: 5, y: 5 });
    });
    expect(result.current.marks[0]!.label).toBe("target"); // an empty label becomes "target"
    act(() => result.current.update(id, { label: "bus", note: "north side" }));
    expect(result.current.marks[0]).toMatchObject({ label: "bus", note: "north side" });
    act(() => result.current.remove(id));
    expect(result.current.marks.map((m) => m.label)).toEqual(["truck"]);
    act(() => result.current.clear());
    expect(loadMarks("a3")).toEqual([]);
  });

  it("drops anything unreadable or malformed instead of guessing", () => {
    localStorage.setItem(
      "geopoc.photo-marks.v1.bad",
      JSON.stringify([
        { id: "ok", label: "x", shape: { kind: "point", x: 1, y: 2 } },
        { id: "nan", label: "x", shape: { kind: "point", x: "1", y: 2 } },
        { id: "two", label: "x", shape: { kind: "outline", points: [[1, 2], [3, 4]] } },
        { label: "no id", shape: { kind: "point", x: 1, y: 2 } },
      ]),
    );
    expect(loadMarks("bad").map((m) => m.id)).toEqual(["ok"]);
    localStorage.setItem("geopoc.photo-marks.v1.broken", "{not json");
    expect(loadMarks("broken")).toEqual([]);
  });
});
