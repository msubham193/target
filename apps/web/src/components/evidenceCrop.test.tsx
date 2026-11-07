import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { cropWindow, EvidenceCrop } from "./EvidenceCrop";

describe("cropWindow", () => {
  it("frames the box with room around it, at a 4:3 shape, centred on it", () => {
    const w = cropWindow({ x0: 1000, y0: 1000, x1: 1200, y1: 1100 }, 4000, 3000);
    expect(w.w / w.h).toBeCloseTo(4 / 3, 5);
    expect(w.x + w.w / 2).toBeCloseTo(1100, 5);
    expect(w.y + w.h / 2).toBeCloseTo(1050, 5);
    expect(w.w).toBeGreaterThan(200 * 2); // more than the box itself, so there is context
  });

  it("does not blow a tiny box up into a blur: the window is never narrower than the minimum", () => {
    const w = cropWindow({ x0: 500, y0: 500, x1: 520, y1: 515 }, 4000, 3000, 160);
    expect(w.w).toBeGreaterThanOrEqual(160);
  });

  it("stays inside the picture, even for a box in a corner or one bigger than the picture", () => {
    const corner = cropWindow({ x0: 0, y0: 0, x1: 100, y1: 80 }, 4000, 3000);
    expect(corner.x).toBeGreaterThanOrEqual(0);
    expect(corner.y).toBeGreaterThanOrEqual(0);
    const edge = cropWindow({ x0: 3900, y0: 2900, x1: 4000, y1: 3000 }, 4000, 3000);
    expect(edge.x + edge.w).toBeLessThanOrEqual(4000 + 1e-6);
    expect(edge.y + edge.h).toBeLessThanOrEqual(3000 + 1e-6);
    const huge = cropWindow({ x0: 0, y0: 0, x1: 4000, y1: 3000 }, 4000, 3000);
    expect(huge.w).toBeLessThanOrEqual(4000);
    expect(huge.h).toBeLessThanOrEqual(3000);
  });
});

describe("EvidenceCrop", () => {
  const region = { type: "bbox_xyxy", coordinates: [1000, 1000, 1200, 1100], image_width: 4096, image_height: 3072 };
  const show = () =>
    render(
      <MemoryRouter>
        <EvidenceCrop src="/original.jpg" fallback="/view.jpg" region={region} alt="Evidence: car" openTo="/projects/p1/assets/a1/ground" />
      </MemoryRouter>,
    );

  it("zooms the picture onto the object, draws its box, and says how big the box is", () => {
    show();
    const image = screen.getByAltText("Evidence: car");
    // the picture is drawn much larger than its frame and shifted so that the box area is what shows
    expect(parseFloat(image.style.width)).toBeGreaterThan(200);
    expect(parseFloat(image.style.left)).toBeLessThan(0);
    expect(image.style.imageOrientation).toBe("none"); // box coordinates are in the file's own pixel grid
    expect(document.querySelectorAll("svg rect").length).toBeGreaterThanOrEqual(2); // the box, and the inset's marks
    expect(screen.getByText(/box 200×100 px of a 4096×3072 px picture/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the photograph" })).toHaveAttribute("href", "/projects/p1/assets/a1/ground");
  });

  it("falls back to the preview if the full-size picture cannot be loaded", () => {
    show();
    const image = screen.getByAltText("Evidence: car");
    expect(image).toHaveAttribute("src", "/original.jpg");
    fireEvent.error(image);
    expect(screen.getByAltText("Evidence: car")).toHaveAttribute("src", "/view.jpg");
  });
});
