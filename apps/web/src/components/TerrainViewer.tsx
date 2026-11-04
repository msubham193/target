import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import type { TerrainMeta } from "../api/client";
import {
  buildMesh,
  type Camera,
  cameraFor,
  eyeOf,
  type Hit,
  orbit,
  pan,
  pick,
  type Preset,
  RAMP,
  rayThrough,
  type Scene,
  sceneOf,
  viewProjection,
  zoom,
} from "../lib/terrain";
import { formatElevation, formatMetres } from "../lib/terrain";
import { Button, Notice } from "./ui";

export type ColourMode = "elevation" | "grey";

const BACKGROUND: [number, number, number] = [0.067, 0.078, 0.102]; // the map's dark background
const LIGHT = [-0.5, 0.5, 0.7];

const VERTEX = `#version 300 es
in vec3 aPos;
in vec2 aSlope;
in float aHeight;
uniform mat4 uVP;
uniform float uExag;
out vec3 vNormal;
out float vHeight;
void main() {
  gl_Position = uVP * vec4(aPos.xy, aPos.z * uExag, 1.0);
  vNormal = normalize(vec3(-aSlope.x * uExag, -aSlope.y * uExag, 1.0));
  vHeight = aHeight;
}`;

const stops = RAMP.map(([r, g, b]) => `vec3(${r}, ${g}, ${b})`);
const FRAGMENT = `#version 300 es
precision highp float;
in vec3 vNormal;
in float vHeight;
uniform float uMin;
uniform float uMax;
uniform int uMode;
uniform vec3 uLight;
out vec4 outColor;
vec3 ramp(float t) {
  vec3 s[5] = vec3[5](${stops.join(", ")});
  float x = clamp(t, 0.0, 1.0) * 4.0;
  int i = int(min(3.0, floor(x)));
  return mix(s[i], s[i + 1], x - float(i));
}
void main() {
  float t = (vHeight - uMin) / max(uMax - uMin, 1e-9);
  vec3 base = uMode == 0 ? ramp(t) : vec3(0.78);
  float shade = clamp(dot(normalize(vNormal), normalize(uLight)), 0.0, 1.0);
  outColor = vec4(base * (0.35 + 0.65 * shade), 1.0);
}`;

const FRAME_VERTEX = `#version 300 es
in vec3 aPos;
uniform mat4 uVP;
uniform float uTop;
void main() { gl_Position = uVP * vec4(aPos.xy, aPos.z * uTop, 1.0); }`;
const FRAME_FRAGMENT = `#version 300 es
precision mediump float;
out vec4 outColor;
void main() { outColor = vec4(0.62, 0.66, 0.74, 1.0); }`;

function compile(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
  const program = gl.createProgram()!;
  for (const [type, source] of [
    [gl.VERTEX_SHADER, vertex],
    [gl.FRAGMENT_SHADER, fragment],
  ] as const) {
    const shader = gl.createShader(type)!;
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(`Shader error: ${gl.getShaderInfoLog(shader) ?? "unknown"}`);
    gl.attachShader(program, shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(`Program error: ${gl.getProgramInfoLog(program) ?? "unknown"}`);
  return program;
}

interface Renderer {
  draw: (camera: Camera, aspect: number, exaggeration: number, mode: ColourMode) => void;
  resize: (width: number, height: number) => void;
  dispose: () => void;
}

function createRenderer(canvas: HTMLCanvasElement, gl: WebGL2RenderingContext, scene: Scene): Renderer {
  const mesh = buildMesh(scene);
  const surface = compile(gl, VERTEX, FRAGMENT);
  const frame = compile(gl, FRAME_VERTEX, FRAME_FRAGMENT);

  const vao = gl.createVertexArray()!;
  gl.bindVertexArray(vao);
  const buffers: WebGLBuffer[] = [];
  const attribute = (program: WebGLProgram, name: string, data: Float32Array, size: number) => {
    const buffer = gl.createBuffer()!;
    buffers.push(buffer);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    const location = gl.getAttribLocation(program, name);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
  };
  attribute(surface, "aPos", mesh.positions, 3);
  attribute(surface, "aSlope", mesh.slopes, 2);
  // NaN heights belong to no-data vertices that no triangle uses; keep the buffer finite anyway.
  attribute(surface, "aHeight", mesh.heights.map((v) => (Number.isNaN(v) ? 0 : v)), 1);
  const indexBuffer = gl.createBuffer()!;
  buffers.push(indexBuffer);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);

  // The extent as a box, so the surface sits in a frame that shows where it ends.
  const frameVao = gl.createVertexArray()!;
  gl.bindVertexArray(frameVao);
  const w = scene.width;
  const d = scene.depth;
  const corners = [
    [0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0],
    [0, 0, 1], [w, 0, 1], [w, d, 1], [0, d, 1],
  ]; // prettier-ignore
  const edges = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7];
  const frameData = new Float32Array(edges.flatMap((i) => corners[i]!));
  attribute(frame, "aPos", frameData, 3);
  gl.bindVertexArray(null);

  const uniforms = {
    vp: gl.getUniformLocation(surface, "uVP"),
    exag: gl.getUniformLocation(surface, "uExag"),
    min: gl.getUniformLocation(surface, "uMin"),
    max: gl.getUniformLocation(surface, "uMax"),
    mode: gl.getUniformLocation(surface, "uMode"),
    light: gl.getUniformLocation(surface, "uLight"),
    frameVp: gl.getUniformLocation(frame, "uVP"),
    top: gl.getUniformLocation(frame, "uTop"),
  };

  return {
    resize(width, height) {
      canvas.width = Math.max(1, Math.round(width));
      canvas.height = Math.max(1, Math.round(height));
      gl.viewport(0, 0, canvas.width, canvas.height);
    },
    draw(camera, aspect, exaggeration, mode) {
      const { viewProjection: vp } = viewProjection(camera, aspect, scene);
      gl.clearColor(BACKGROUND[0], BACKGROUND[1], BACKGROUND[2], 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.useProgram(surface);
      gl.uniformMatrix4fv(uniforms.vp, false, vp);
      gl.uniform1f(uniforms.exag, exaggeration);
      gl.uniform1f(uniforms.min, scene.min);
      gl.uniform1f(uniforms.max, scene.max);
      gl.uniform1i(uniforms.mode, mode === "elevation" ? 0 : 1);
      gl.uniform3f(uniforms.light, LIGHT[0]!, LIGHT[1]!, LIGHT[2]!);
      gl.bindVertexArray(vao);
      gl.drawElements(gl.TRIANGLES, mesh.indices.length, gl.UNSIGNED_INT, 0);
      gl.useProgram(frame);
      gl.uniformMatrix4fv(uniforms.frameVp, false, vp);
      gl.uniform1f(uniforms.top, Math.max(scene.relief * exaggeration, 1));
      gl.bindVertexArray(frameVao);
      gl.drawArrays(gl.LINES, 0, edges.length);
      gl.bindVertexArray(null);
    },
    dispose() {
      for (const buffer of buffers) gl.deleteBuffer(buffer);
      gl.deleteVertexArray(vao);
      gl.deleteVertexArray(frameVao);
      gl.deleteProgram(surface);
      gl.deleteProgram(frame);
    },
  };
}

const KEY_STEP = 12; // pixels of drag per key press

/**
 * The terrain as a lit surface you can orbit, zoom and pan. All drawing is local WebGL2 from the grid the API sent; nothing
 * is requested from any other address. The pointer reads the stored value of the cell under it: elevation is shown only for
 * cells that have data, and never interpolated.
 */
export function TerrainViewer({
  values,
  meta,
  exaggeration,
  colour,
  onHover,
  onSelect,
}: {
  values: Float32Array;
  meta: TerrainMeta;
  exaggeration: number;
  colour: ColourMode;
  onHover: (hit: Hit | null) => void;
  onSelect: (hit: Hit) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<Renderer | null>(null);
  const scene = useMemo(() => sceneOf(values, meta), [values, meta]);
  const [camera, setCameraState] = useState<Camera>(() => cameraFor(scene));
  const cameraRef = useRef(camera);
  const propsRef = useRef({ exaggeration, colour, scene, onHover, onSelect });
  propsRef.current = { exaggeration, colour, scene, onHover, onSelect };
  const [problem, setProblem] = useState<string | null>(null);
  const describedBy = useId();

  const setCamera = useCallback((next: Camera) => {
    cameraRef.current = next;
    setCameraState(next);
  }, []);

  // A new surface starts from a fresh view of it.
  useEffect(() => {
    setCamera(cameraFor(scene));
  }, [scene, setCamera]);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const renderer = rendererRef.current;
    if (!canvas || !renderer) return;
    const p = propsRef.current;
    renderer.draw(cameraRef.current, canvas.width / Math.max(1, canvas.height), p.exaggeration, p.colour);
  }, []);

  // The WebGL context and the mesh: made once per surface.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let gl: WebGL2RenderingContext | null = null;
    try {
      gl = canvas.getContext("webgl2", { antialias: true });
    } catch {
      gl = null;
    }
    if (!gl) {
      setProblem("This browser cannot draw WebGL 2, which the 3D view needs. The data and its description are still shown.");
      return;
    }
    let renderer: Renderer;
    try {
      renderer = createRenderer(canvas, gl, scene);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "The 3D view could not be started.");
      return;
    }
    setProblem(null);
    rendererRef.current = renderer;
    const fit = () => {
      const box = frameRef.current?.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      renderer.resize((box?.width ?? 800) * ratio, (box?.height ?? 500) * ratio);
      redraw();
    };
    fit();
    const observer = new ResizeObserver(fit);
    if (frameRef.current) observer.observe(frameRef.current);
    const lost = (event: Event) => {
      event.preventDefault();
      setProblem("The graphics context was lost. Reload the page to draw the terrain again.");
    };
    canvas.addEventListener("webglcontextlost", lost);
    return () => {
      observer.disconnect();
      canvas.removeEventListener("webglcontextlost", lost);
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [scene, redraw]);

  useEffect(() => {
    redraw();
  }, [camera, exaggeration, colour, redraw]);

  // --- pointer, wheel and keyboard ---------------------------------------------------------------------------------------

  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ mode: "orbit" | "pan"; x: number; y: number; moved: number } | null>(null);
  const pinch = useRef<number | null>(null);
  const hoverFrame = useRef(0);

  const hitAt = useCallback((clientX: number, clientY: number): Hit | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const box = canvas.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return null;
    const p = propsRef.current;
    const ray = rayThrough(cameraRef.current, p.scene, clientX - box.left, clientY - box.top, box.width, box.height);
    return ray ? pick(p.scene, ray.origin, ray.direction, p.exaggeration) : null;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setCamera(zoom(cameraRef.current, event.deltaY * 0.0015, propsRef.current.scene));
    };
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => canvas.removeEventListener("wheel", wheel);
  }, [setCamera]);

  const viewportHeight = () => canvasRef.current?.getBoundingClientRect().height || 500;

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()] as [{ x: number; y: number }, { x: number; y: number }];
      pinch.current = Math.hypot(a.x - b.x, a.y - b.y);
      drag.current = null;
      return;
    }
    const wantsPan = event.button === 1 || event.button === 2 || event.shiftKey;
    drag.current = { mode: wantsPan ? "pan" : "orbit", x: event.clientX, y: event.clientY, moved: 0 };
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (pointers.current.has(event.pointerId)) pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 2 && pinch.current !== null) {
      const [a, b] = [...pointers.current.values()] as [{ x: number; y: number }, { x: number; y: number }];
      const now = Math.hypot(a.x - b.x, a.y - b.y);
      setCamera(zoom(cameraRef.current, (pinch.current - now) * 0.005, propsRef.current.scene));
      pinch.current = now;
      return;
    }
    const d = drag.current;
    if (d) {
      const dx = event.clientX - d.x;
      const dy = event.clientY - d.y;
      d.x = event.clientX;
      d.y = event.clientY;
      d.moved += Math.abs(dx) + Math.abs(dy);
      if (d.moved > 0) {
        setCamera(d.mode === "pan" ? pan(cameraRef.current, dx, dy, viewportHeight(), propsRef.current.scene) : orbit(cameraRef.current, dx, dy));
      }
      return;
    }
    cancelAnimationFrame(hoverFrame.current);
    const { clientX, clientY } = event;
    hoverFrame.current = requestAnimationFrame(() => propsRef.current.onHover(hitAt(clientX, clientY)));
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    const d = drag.current;
    drag.current = null;
    if (d && d.moved < 5) {
      const hit = hitAt(event.clientX, event.clientY);
      if (hit) propsRef.current.onSelect(hit);
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    const scene_ = propsRef.current.scene;
    const arrows: Record<string, [number, number]> = { ArrowLeft: [-KEY_STEP, 0], ArrowRight: [KEY_STEP, 0], ArrowUp: [0, -KEY_STEP], ArrowDown: [0, KEY_STEP] };
    const move = arrows[event.key];
    if (move) {
      event.preventDefault();
      setCamera(event.shiftKey ? pan(cameraRef.current, move[0] * 2, move[1] * 2, viewportHeight(), scene_) : orbit(cameraRef.current, move[0], move[1]));
    } else if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      setCamera(zoom(cameraRef.current, -0.15, scene_));
    } else if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      setCamera(zoom(cameraRef.current, 0.15, scene_));
    } else if (event.key === "Home") {
      event.preventDefault();
      setCamera(cameraFor(scene_));
    }
  };

  const preset = (name: Preset) => setCamera(cameraFor(scene, name));
  const yawDegrees = (camera.yaw * 180) / Math.PI;
  const eye = eyeOf(camera);
  const e = meta.elevation;

  return (
    <div className="space-y-2">
      <div role="toolbar" aria-label="3D view controls" className="flex flex-wrap items-center gap-1.5">
        <Button size="sm" variant="secondary" onClick={() => preset("oblique")}>
          Reset view
        </Button>
        <Button size="sm" variant="secondary" onClick={() => preset("top")}>
          Top view
        </Button>
        <Button size="sm" variant="secondary" onClick={() => preset("north")}>
          From the north
        </Button>
        <Button size="sm" variant="secondary" aria-label="Zoom in" onClick={() => setCamera(zoom(cameraRef.current, -0.25, scene))}>
          Zoom in
        </Button>
        <Button size="sm" variant="secondary" aria-label="Zoom out" onClick={() => setCamera(zoom(cameraRef.current, 0.25, scene))}>
          Zoom out
        </Button>
        <span className="ml-auto text-xs text-fg-subtle">Drag to orbit · Shift-drag or right-drag to pan · wheel to zoom · arrow keys, + and − when focused</span>
      </div>

      <div ref={frameRef} className="relative h-[min(60vh,34rem)] min-h-80 overflow-hidden rounded-lg border border-line bg-[#11141a]">
        <canvas
          ref={canvasRef}
          data-testid="terrain-canvas"
          data-yaw={yawDegrees.toFixed(1)}
          data-pitch={((camera.pitch * 180) / Math.PI).toFixed(1)}
          data-distance={camera.distance.toFixed(1)}
          data-exaggeration={exaggeration}
          data-eye={eye.map((v) => v.toFixed(1)).join(",")}
          tabIndex={0}
          role="img"
          aria-label={`3D terrain of ${meta.filename}`}
          aria-describedby={describedBy}
          className="absolute inset-0 h-full w-full touch-none cursor-grab outline-none focus-visible:ring-2 focus-visible:ring-accent/60 active:cursor-grabbing"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={() => propsRef.current.onHover(null)}
          onContextMenu={(event) => event.preventDefault()}
          onKeyDown={onKeyDown}
        />
        <p id={describedBy} className="sr-only">
          Drag to orbit, shift-drag to pan, scroll to zoom. With the surface focused, arrow keys orbit, shift and arrow keys pan, plus and minus
          zoom, and Home resets the view.
        </p>

        {problem ? (
          <div className="absolute inset-0 flex items-center justify-center p-4">
            <Notice tone="error" title="The 3D view cannot be drawn">
              {problem}
            </Notice>
          </div>
        ) : (
          <>
            <div
              data-testid="north-arrow"
              className="pointer-events-none absolute right-3 top-3 flex h-12 w-12 items-center justify-center rounded-full border border-line-strong bg-surface/80 text-[11px] font-semibold text-fg"
              aria-label={`North is ${Math.round(((yawDegrees % 360) + 360) % 360)} degrees clockwise from the top of the view`}
              role="img"
            >
              <span style={{ transform: `rotate(${yawDegrees}deg)` }} className="block leading-none">
                <span className="block text-center">N</span>
                <span className="block text-center text-base leading-3">▲</span>
              </span>
            </div>
            <div className="pointer-events-none absolute bottom-2 left-2 space-y-0.5 rounded-md bg-surface/80 px-2 py-1.5 text-[11px] text-fg-muted">
              <p>
                Surface {formatMetres(scene.width)} × {formatMetres(scene.depth)} · one cell {formatMetres(meta.cell_size_m[0]!)} × {formatMetres(meta.cell_size_m[1]!)}
              </p>
              <p data-testid="exaggeration-badge" className={exaggeration === 1 ? "" : "font-medium text-warn"}>
                {exaggeration === 1 ? "Vertical exaggeration ×1 (true scale)" : `Vertical exaggeration ×${exaggeration}: heights are drawn ${exaggeration} times taller than true scale`}
              </p>
            </div>
            {colour === "elevation" ? (
              <div className="pointer-events-none absolute left-2 top-2 w-40 rounded-md bg-surface/80 px-2 py-1.5 text-[11px] text-fg-muted" aria-label="Elevation colour scale">
                <div
                  className="h-2 rounded-sm"
                  style={{ background: `linear-gradient(to right, ${RAMP.map(([r, g, b]) => `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`).join(", ")})` }}
                />
                <div className="mt-0.5 flex justify-between font-mono">
                  <span>{formatElevation(e.min, e.unit_label)}</span>
                  <span>{formatElevation(e.max, e.unit_label)}</span>
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
