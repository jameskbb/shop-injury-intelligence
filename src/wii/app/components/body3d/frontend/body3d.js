// Workshop Injury Intelligence: 3D body injury map (st.components.v2 frontend module).
//
// Streamlit calls the default export on mount and again whenever `data` changes, always with
// the same parentElement. The scene is therefore built once per parentElement and every later
// call only diffs the payload (region colours, active tool, selection) against what is shown.
//
// The figure is a DRAFTED ORTHOGRAPHIC PLATE, not a person: one continuous matte form on a
// vellum sheet, divided into regions by hairline linework. See docs/design-system.md.
//
// Honesty rules encoded here (see the Python wrapper for the contract):
//   * Every region has exactly ONE material. All meshes of a region share it, so the five
//     finger meshes can never show different values: NEISS records "finger" as one category.
//   * INK DENSITY = MAGNITUDE. A region's fill is its value on the sequential ramp; the ramp
//     never reaches the sheet colour, so a low value is still visibly drawn.
//   * LINE TREATMENT = CONFIDENCE, never colour. ONLY a limited or suppressed region is
//     outlined, and always with a dashed construction contour; a suppressed region
//     additionally takes NO fill (bare vellum), and an absent one keeps only the faintest
//     solid hairline. A stable region carries no contour at all --
//     its fill is what says "region", and a drafted plate does not outline every internal
//     area. That is what keeps "provisional" a signal instead of the loudest thing on the
//     figure. No hatch, no caution colour, anywhere on it.
//   * Selecting a region fades the OTHER regions toward the sheet. That is a focus treatment
//     applied to everything else equally, not a change to any region's value.

const THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.170.0/+esm";
const ORBIT_URL =
  "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/controls/OrbitControls.js/+esm";

const HAND_REGIONS = new Set(["hand", "finger", "wrist"]);
const HEAD_REGIONS = new Set(["head", "face", "eye", "ear", "mouth"]);
const TRUNK_REGIONS = new Set(["upper_trunk", "lower_trunk", "pubic_region"]);

// --- Ink. Every value below is a wii.app.theme token; no other colour may appear in this file.
const VELLUM = 0xe8eae5; // theme.VELLUM -- the sheet
const GRAPHITE = 0x22272a; // theme.GRAPHITE -- primary ink, all linework
const CONSTRUCTION = 0x7b7f7e; // theme.CONSTRUCTION (graphite @55%) flattened onto vellum
const RULE = 0xbcbfbc; // theme.RULE (graphite @22%) flattened onto vellum -- the faintest line
const BLUEPRINT = 0x1c5b7d; // theme.BLUEPRINT -- the one accent: selection and focus
const PAPER = VELLUM; // an unfilled region is bare sheet, never a colour

// Focus treatment: unfocused regions fade toward the sheet (they recede on a light ground the
// way they used to recede toward black on a dark one).
const FOCUS_DIM = 0.72;
// Fresnel ink at the silhouette: how a drawn form gets its contour without a specular highlight.
const EDGE = { stable: 0.5, limited: 0.62, suppressed: 0.78, absent: 0.5 };
// The depth channel: a stable region stands proud of the surface by at most this, along its own
// normal, scaled by `emphasis` and faded to zero at the region boundary so it reads as relief.
const RELIEF_MAX = 0.03;
const RELIEF_SPAN = 0.02; // world units over which the relief falls to zero at a region seam

// Motion. Two curves: ease-in-out-cubic to depart, ease-out-quint to settle (see MOTION below).
const DIM_MS = 250; // dim-before-move: the answer appears on the whole body first
const HOLD_MS = 600; // whole-body hold with the hotspots lit
const FLIGHT_MS = 1050; // turn-and-lean to the #1 hotspot
const ZOOMED_FLIGHT_MS = 900; // already zoomed in: one flight, no establishing hold
const SELECT_MS = 900; // click a region
const RETURN_MS = 700; // the un-answer: back to the whole body
const VIEW_BTN_MS = 800; // the reader's own move; the figure does not react
const INTRO_HOLD_MS = 300;
const INTRO_MS = 1200;
const PULSE_MS = 600; // one cycle, #1 hotspot only
const MAX_FLIGHT_MS = 1300;
const MAX_SEQUENCE_MS = 2600;
const DOLLY_LAG_MS = 100; // the figure turns first, then the camera leans in

const BODY_TARGET = [0, 0.9, 0];
const BODY_OFFSET = [1.05, 0.22, 3.85];
const WIDE_OFFSET = [-2.13, 1.91, 5.5]; // the establishing shot: load with no tool only
// Outboard of the figure's right hand and a little in front, looking back past it toward the
// body. The old framing looked from INBOARD at 0.42, which put the thigh at 0.31 -- nearer the
// camera than the hand itself -- so a faded thigh cut across the frame full height while the
// hand overflowed it (109% of the frame height). From outboard the thigh falls to 0.76, behind
// the hand, where it reads as the figure it belongs to instead of an obstruction.
const HAND_OFFSET = [-0.86, 0.15, 0.49];
const HAND_DIST = 0.6; // whole hand + a little wrist at ~75% of the frame height
const HEAD_DIR = [0.34, 0.12, 1];
const HEAD_FIT_RADIUS = 0.16; // half-width (world units) a head close-up keeps in frame
const LIMB_DIR = [0.38, 0.12, 1]; // x is mirrored to the anchor's side

// Proportions (review §5): a neutral mannequin is ~7.5 heads tall.
const HR = 0.115; // head radius before the head's own axis scale
const HEAD_S = [0.88, 1.02, 0.98];
const HEAD_C = [0, 1.652, 0.004];
const ARM_DX = 0.024; // arms sit outboard of the widened chest
const ARM_ABDUCT = 0.06; // ~3.4 degrees of arm roll away from the body
const BODY_H = 0.0075; // surface-nets cell size for the one-piece body

const instances = new WeakMap();
let threePromise = null;

function loadThree() {
  if (!threePromise) {
    threePromise = Promise.all([import(THREE_URL), import(ORBIT_URL)]).then(([T, O]) => ({
      THREE: T,
      OrbitControls: O.OrbitControls,
    }));
    threePromise.catch(() => {
      threePromise = null;
    });
  }
  return threePromise;
}

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

function prefersReducedMotion(payload) {
  if (payload && payload.reduced_motion) return true;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

// MOTION. Depart on ease-in-out-cubic; the last fifth settles on an ease-out-quint, so an
// arrival decelerates into the frame like a landing instead of easing symmetrically through it.
const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOutQuint = (t) => 1 - Math.pow(1 - t, 5);
const SETTLE_AT = 0.8;
const SETTLE_V = easeInOutCubic(SETTLE_AT);
function flightEase(t) {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  if (t < SETTLE_AT) return easeInOutCubic(t);
  return SETTLE_V + (1 - SETTLE_V) * easeOutQuint((t - SETTLE_AT) / (1 - SETTLE_AT));
}

// ---------------------------------------------------------------------------------------------
// Geometry: a procedural, bilateral figure. y is up (feet at 0, crown ~1.77), it faces +z, and
// the figure's right side is -x. No external model files.
//
// Trunk, head and limbs are ONE signed distance field blended with a smooth minimum, polygonised
// once with surface nets and then cut into region meshes along the lines where the labelling
// fields meet. Joints are therefore blends, not sleeves: there is no seam, no shoulder ball and
// no elbow or knee band in the silhouette. The hand is a second, much finer field built the same
// way, because a 1.4 mm grid over the whole figure is not affordable.
// ---------------------------------------------------------------------------------------------

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

function smin(a, b, k) {
  const h = Math.max(k - Math.abs(a - b), 0) / k;
  return Math.min(a, b) - h * h * k * 0.25;
}

// A capsule whose radius runs linearly from ra (at a) to rb (at b).
function roundCone(a, b, ra, rb) {
  const bx = b[0] - a[0];
  const by = b[1] - a[1];
  const bz = b[2] - a[2];
  const bb = bx * bx + by * by + bz * bz;
  return (x, y, z) => {
    const px = x - a[0];
    const py = y - a[1];
    const pz = z - a[2];
    const h = clamp01((px * bx + py * by + pz * bz) / bb);
    const qx = px - bx * h;
    const qy = py - by * h;
    const qz = pz - bz * h;
    return Math.sqrt(qx * qx + qy * qy + qz * qz) - (ra + (rb - ra) * h);
  };
}

// The same field evaluated in a non-uniformly scaled frame: a chest is broad and shallow, a foot
// is flat. Scales are <= 1, so the result stays a conservative under-estimate of true distance.
function scaledField(f, c, s) {
  const m = Math.min(s[0], s[1], s[2]);
  return (x, y, z) =>
    f(c[0] + (x - c[0]) / s[0], c[1] + (y - c[1]) / s[1], c[2] + (z - c[2]) / s[2]) * m;
}

function ellipsoidField(c, r) {
  const m = Math.min(r[0], r[1], r[2]);
  return (x, y, z) => {
    const a = (x - c[0]) / r[0];
    const b = (y - c[1]) / r[1];
    const d = (z - c[2]) / r[2];
    return (Math.sqrt(a * a + b * b + d * d) - 1) * m;
  };
}

// One solid of the figure, with a bounding sphere so the field can skip it when it cannot win.
function solid(a, b, ra, rb, k, scale) {
  const c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
  const half = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) / 2;
  const base = roundCone(a, b, ra, rb);
  const minS = scale ? Math.min(scale[0], scale[1], scale[2]) : 1;
  return {
    f: scale ? scaledField(base, c, scale) : base,
    k,
    cx: c[0],
    cy: c[1],
    cz: c[2],
    R: half + Math.max(ra, rb),
    minS,
  };
}

// The field is sampled ~2 million times to build the figure, so each solid first offers a cheap
// bounding-sphere lower bound. Once the nearest-bounded solid has been evaluated, any solid whose
// bound sits more than its own blend radius beyond that value cannot change the result and is
// skipped -- the accumulation order is unchanged, so the blended shape is identical.
function fieldOf(solids) {
  const n = solids.length;
  const lb = new Float64Array(n);
  return (x, y, z) => {
    let bi = 0;
    let bb = Infinity;
    for (let i = 0; i < n; i++) {
      const s = solids[i];
      const dx = x - s.cx;
      const dy = y - s.cy;
      const dz = z - s.cz;
      const v = (Math.sqrt(dx * dx + dy * dy + dz * dz) - s.R) * s.minS;
      lb[i] = v;
      if (v < bb) {
        bb = v;
        bi = i;
      }
    }
    const seed = solids[bi].f(x, y, z);
    let best = seed;
    let d = Infinity;
    for (let i = 0; i < n; i++) {
      const s = solids[i];
      if (i !== bi && lb[i] > best + s.k) continue;
      const v = i === bi ? seed : s.f(x, y, z);
      if (v < best) best = v;
      d = d === Infinity ? v : smin(d, v, s.k);
    }
    return d;
  };
}

// Surface nets: one vertex per sign-changing cell, one quad per sign-changing grid edge. Each
// vertex is then projected onto the zero set and takes the field gradient as its normal.
function surfaceNets(sdf, lo, hi, h) {
  const nx = Math.ceil((hi[0] - lo[0]) / h) + 1;
  const ny = Math.ceil((hi[1] - lo[1]) / h) + 1;
  const nz = Math.ceil((hi[2] - lo[2]) / h) + 1;
  const f = new Float32Array(nx * ny * nz);
  // Sphere tracing along each row: a signed distance is 1-Lipschitz, so a sample further than
  // 2h from the surface lets the next |d|/h - 2 samples be filled with a same-sign ramp instead
  // of evaluated. Neighbouring samples differ by at most h*sqrt(3) < 2h, so a filled sample can
  // never take part in a sign change -- the polygonisation is identical, at a fraction of the
  // field evaluations. (The figure is a thin object inside a large box; most of the box is air.)
  for (let k = 0; k < nz; k++) {
    const z = lo[2] + k * h;
    for (let j = 0; j < ny; j++) {
      const y = lo[1] + j * h;
      const row = nx * (j + ny * k);
      let i = 0;
      while (i < nx) {
        const d = sdf(lo[0] + i * h, y, z);
        f[row + i] = d;
        const skip = Math.floor(Math.abs(d) / h) - 2;
        if (skip > 0) {
          const step = d > 0 ? -h : h;
          const last = Math.min(nx - 1, i + skip);
          for (let s = i + 1; s <= last; s++) f[row + s] = d + step * (s - i);
          i = last + 1;
        } else i++;
      }
    }
  }
  const at = (i, j, k) => i + nx * (j + ny * k);
  const cx = nx - 1;
  const cy = ny - 1;
  const cell = new Int32Array(cx * cy * (nz - 1)).fill(-1);
  const cat = (i, j, k) => i + cx * (j + cy * k);
  const C = [
    [0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1],
  ];
  const E = [
    [0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6],
    [3, 7],
  ];
  const pos = [];
  const v = new Float32Array(8);
  for (let k = 0; k < nz - 1; k++) {
    for (let j = 0; j < ny - 1; j++) {
      for (let i = 0; i < nx - 1; i++) {
        let mask = 0;
        for (let c = 0; c < 8; c++) {
          v[c] = f[at(i + C[c][0], j + C[c][1], k + C[c][2])];
          if (v[c] < 0) mask |= 1 << c;
        }
        if (mask === 0 || mask === 255) continue;
        let sx = 0;
        let sy = 0;
        let sz = 0;
        let m = 0;
        for (const [a, b] of E) {
          if (v[a] < 0 === v[b] < 0) continue;
          const t = v[a] / (v[a] - v[b]);
          sx += C[a][0] + (C[b][0] - C[a][0]) * t;
          sy += C[a][1] + (C[b][1] - C[a][1]) * t;
          sz += C[a][2] + (C[b][2] - C[a][2]) * t;
          m++;
        }
        cell[cat(i, j, k)] = pos.length / 3;
        pos.push(lo[0] + (i + sx / m) * h, lo[1] + (j + sy / m) * h, lo[2] + (k + sz / m) * h);
      }
    }
  }
  const quads = [];
  for (let k = 1; k < nz - 1; k++) {
    for (let j = 1; j < ny - 1; j++) {
      for (let i = 1; i < nx - 1; i++) {
        const s = f[at(i, j, k)] < 0;
        if (s !== f[at(i + 1, j, k)] < 0) {
          quads.push(cell[cat(i, j - 1, k - 1)], cell[cat(i, j, k - 1)], cell[cat(i, j, k)],
            cell[cat(i, j - 1, k)]);
        }
        if (s !== f[at(i, j + 1, k)] < 0) {
          quads.push(cell[cat(i - 1, j, k - 1)], cell[cat(i, j, k - 1)], cell[cat(i, j, k)],
            cell[cat(i - 1, j, k)]);
        }
        if (s !== f[at(i, j, k + 1)] < 0) {
          quads.push(cell[cat(i - 1, j - 1, k)], cell[cat(i, j - 1, k)], cell[cat(i, j, k)],
            cell[cat(i - 1, j, k)]);
        }
      }
    }
  }
  // Project onto the surface and take smooth normals from the field.
  const nrm = new Float32Array(pos.length);
  const e = h * 0.25;
  for (let p = 0; p < pos.length; p += 3) {
    let x = pos[p];
    let y = pos[p + 1];
    let z = pos[p + 2];
    let gx = 0;
    let gy = 0;
    let gz = 0;
    for (let it = 0; it < 3; it++) {
      const d = sdf(x, y, z);
      gx = sdf(x + e, y, z) - sdf(x - e, y, z);
      gy = sdf(x, y + e, z) - sdf(x, y - e, z);
      gz = sdf(x, y, z + e) - sdf(x, y, z - e);
      const g = Math.sqrt(gx * gx + gy * gy + gz * gz) || 1;
      gx /= g;
      gy /= g;
      gz /= g;
      if (it < 2) {
        const step = Math.max(-h, Math.min(h, d));
        x -= gx * step;
        y -= gy * step;
        z -= gz * step;
      }
    }
    pos[p] = x;
    pos[p + 1] = y;
    pos[p + 2] = z;
    nrm[p] = gx;
    nrm[p + 1] = gy;
    nrm[p + 2] = gz;
  }
  // Triangles along the shorter diagonal, wound to face along the field gradient.
  const tris = [];
  const P = (a, c) => pos[a * 3 + c];
  const d2 = (a, b) => (P(a, 0) - P(b, 0)) ** 2 + (P(a, 1) - P(b, 1)) ** 2 + (P(a, 2) - P(b, 2)) ** 2;
  const emit = (a, b, c) => {
    const ux = P(b, 0) - P(a, 0);
    const uy = P(b, 1) - P(a, 1);
    const uz = P(b, 2) - P(a, 2);
    const wx = P(c, 0) - P(a, 0);
    const wy = P(c, 1) - P(a, 1);
    const wz = P(c, 2) - P(a, 2);
    const fx = uy * wz - uz * wy;
    const fy = uz * wx - ux * wz;
    const fz = ux * wy - uy * wx;
    const dot =
      fx * (nrm[a * 3] + nrm[b * 3] + nrm[c * 3]) +
      fy * (nrm[a * 3 + 1] + nrm[b * 3 + 1] + nrm[c * 3 + 1]) +
      fz * (nrm[a * 3 + 2] + nrm[b * 3 + 2] + nrm[c * 3 + 2]);
    if (dot >= 0) tris.push(a, b, c);
    else tris.push(a, c, b);
  };
  for (let q = 0; q < quads.length; q += 4) {
    const [a, b, c, d] = [quads[q], quads[q + 1], quads[q + 2], quads[q + 3]];
    if (a < 0 || b < 0 || c < 0 || d < 0) continue;
    if (d2(a, c) <= d2(b, d)) {
      emit(a, b, c);
      emit(a, c, d);
    } else {
      emit(a, b, d);
      emit(b, c, d);
    }
  }
  return { pos, nrm, tris };
}

// Cut a polygonised surface into parts. `parts(x, y, z)` returns one distance per part; each
// triangle is clipped to the zone where its part is the nearest, with the cut interpolated
// along the edges, so neighbouring parts meet on one shared, smooth line. Each vertex also
// carries `w`: how far inside its own part it sits, which drives the relief (depth) channel and
// falls to zero exactly at the seam, so a lifted region never tears away from its neighbours.
function splitByPart(surf, parts, count) {
  const { pos, nrm, tris } = surf;
  const nv = pos.length / 3;
  const dist = [];
  const owner = new Int8Array(nv);
  const weight = new Float32Array(nv);
  for (let i = 0; i < nv; i++) {
    const d = parts(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
    dist.push(d);
    let best = 0;
    let second = Infinity;
    for (let g = 1; g < count; g++) {
      if (d[g] < d[best]) {
        second = d[best];
        best = g;
      } else if (d[g] < second) second = d[g];
    }
    owner[i] = best;
    weight[i] = second === Infinity ? 1 : clamp01((second - d[best]) / RELIEF_SPAN);
  }
  // Indexed output: an original vertex keeps one slot per part; cut points are appended.
  const out = Array.from({ length: count }, () => ({
    pos: [], nrm: [], w: [], idx: [], map: new Map(),
  }));
  const vert = (i) => ({
    i,
    p: [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]],
    n: [nrm[i * 3], nrm[i * 3 + 1], nrm[i * 3 + 2]],
    d: dist[i],
    w: weight[i],
  });
  const lerp = (a, b, t) => a.map((x, c) => x + (b[c] - x) * t);
  const slot = (g, q) => {
    const o = out[g];
    if (q.i !== undefined && o.map.has(q.i)) return o.map.get(q.i);
    const k = o.pos.length / 3;
    const l = Math.hypot(q.n[0], q.n[1], q.n[2]) || 1;
    o.pos.push(q.p[0], q.p[1], q.p[2]);
    o.nrm.push(q.n[0] / l, q.n[1] / l, q.n[2] / l);
    o.w.push(q.w === undefined ? 0 : q.w);
    if (q.i !== undefined) o.map.set(q.i, k);
    return k;
  };
  const push = (g, poly) => {
    const ks = poly.map((q) => slot(g, q));
    for (let i = 1; i + 1 < ks.length; i++) out[g].idx.push(ks[0], ks[i], ks[i + 1]);
  };
  for (let t = 0; t < tris.length; t += 3) {
    const a = owner[tris[t]];
    if (a === owner[tris[t + 1]] && a === owner[tris[t + 2]]) {
      push(a, [vert(tris[t]), vert(tris[t + 1]), vert(tris[t + 2])]);
      continue;
    }
    const tri = [vert(tris[t]), vert(tris[t + 1]), vert(tris[t + 2])];
    const groups = new Set([a, owner[tris[t + 1]], owner[tris[t + 2]]]);
    for (const g of groups) {
      // Margin of part g over the nearest other part among those present (<= 0 inside g).
      const m = (q) => {
        let o = Infinity;
        for (const x of groups) if (x !== g) o = Math.min(o, q.d[x]);
        return q.d[g] - o;
      };
      const poly = [];
      for (let i = 0; i < 3; i++) {
        const A = tri[i];
        const B = tri[(i + 1) % 3];
        const ma = m(A);
        const mb = m(B);
        if (ma <= 0) poly.push(A);
        if (ma <= 0 !== mb <= 0) {
          const s = ma / (ma - mb);
          // A cut point sits ON the seam, so its relief weight is zero by construction.
          poly.push({ p: lerp(A.p, B.p, s), n: lerp(A.n, B.n, s), d: lerp(A.d, B.d, s), w: 0 });
        }
      }
      if (poly.length >= 3) push(g, poly);
    }
  }
  for (const o of out) delete o.map;
  return out;
}

// ---------------------------------------------------------------------------------------------
// Drafting linework. A region's boundary is extracted from its cut mesh and rebuilt as a thin
// ribbon straddling the seam. The ribbon is widened in the vertex shader (so one geometry reads
// the same at whole-body and hand zoom) and, for a limited or suppressed estimate, broken into
// dashes along its own arc length -- the confidence channel, carried by line treatment alone.
// ---------------------------------------------------------------------------------------------

const CONTOUR_LIFT = 0.0002; // how far the ribbon floats above the surface it traces

function contourGeometry(THREE, pos, nrm, idx) {
  const nv = pos.length / 3;
  if (!nv || !idx.length) return null;
  // Weld coincident vertices: a cut boundary is duplicated per triangle and would not chain.
  const rep = new Int32Array(nv);
  const seen = new Map();
  const Q = 1e5;
  for (let i = 0; i < nv; i++) {
    const key =
      `${Math.round(pos[i * 3] * Q)},${Math.round(pos[i * 3 + 1] * Q)},` +
      `${Math.round(pos[i * 3 + 2] * Q)}`;
    const had = seen.get(key);
    if (had === undefined) {
      seen.set(key, i);
      rep[i] = i;
    } else rep[i] = had;
  }
  // An edge used by exactly one triangle bounds the patch.
  const count = new Map();
  const put = (a, b) => {
    const k = a < b ? a * 1e6 + b : b * 1e6 + a;
    count.set(k, (count.get(k) || 0) + 1);
  };
  for (let t = 0; t < idx.length; t += 3) {
    const a = rep[idx[t]];
    const b = rep[idx[t + 1]];
    const c = rep[idx[t + 2]];
    if (a === b || b === c || c === a) continue;
    put(a, b);
    put(b, c);
    put(c, a);
  }
  const adj = new Map();
  const link = (a, b) => {
    let l = adj.get(a);
    if (!l) adj.set(a, (l = []));
    l.push(b);
  };
  for (const [k, n] of count) {
    if (n !== 1) continue;
    const a = Math.floor(k / 1e6);
    const b = k - a * 1e6;
    link(a, b);
    link(b, a);
  }
  if (!adj.size) return null;
  // Chain the boundary edges into loops so the dash runs ALONG the contour.
  const used = new Set();
  const loops = [];
  for (const start of adj.keys()) {
    if (used.has(start)) continue;
    const loop = [];
    let cur = start;
    let prev = -1;
    while (cur !== -1 && !used.has(cur)) {
      loop.push(cur);
      used.add(cur);
      let next = -1;
      for (const v of adj.get(cur) || []) {
        if (v !== prev && !used.has(v)) {
          next = v;
          break;
        }
      }
      prev = cur;
      cur = next;
    }
    if (loop.length > 2) loops.push(loop);
  }
  const vpos = [];
  const vdir = [];
  const varc = [];
  const tri = [];
  const at = (i, c) => pos[i * 3 + c];
  const nat = (i, c) => nrm[i * 3 + c];
  for (const loop of loops) {
    const n = loop.length;
    const closed = (adj.get(loop[n - 1]) || []).includes(loop[0]);
    let arc = 0;
    const steps = closed ? n : n - 1;
    for (let s = 0; s < steps; s++) {
      const a = loop[s];
      const b = loop[(s + 1) % n];
      const ax = at(a, 0) + nat(a, 0) * CONTOUR_LIFT;
      const ay = at(a, 1) + nat(a, 1) * CONTOUR_LIFT;
      const az = at(a, 2) + nat(a, 2) * CONTOUR_LIFT;
      const bx = at(b, 0) + nat(b, 0) * CONTOUR_LIFT;
      const by = at(b, 1) + nat(b, 1) * CONTOUR_LIFT;
      const bz = at(b, 2) + nat(b, 2) * CONTOUR_LIFT;
      let ex = bx - ax;
      let ey = by - ay;
      let ez = bz - az;
      const len = Math.hypot(ex, ey, ez);
      if (len < 1e-7) continue;
      ex /= len;
      ey /= len;
      ez /= len;
      // In-surface perpendicular: the ribbon straddles the seam, half to each side.
      const px = ey * nat(a, 2) - ez * nat(a, 1);
      const py = ez * nat(a, 0) - ex * nat(a, 2);
      const pz = ex * nat(a, 1) - ey * nat(a, 0);
      const pl = Math.hypot(px, py, pz) || 1;
      const ux = px / pl;
      const uy = py / pl;
      const uz = pz / pl;
      const base = vpos.length / 3;
      const a1 = arc;
      const a2 = arc + len;
      vpos.push(ax, ay, az, ax, ay, az, bx, by, bz, bx, by, bz);
      vdir.push(ux, uy, uz, -ux, -uy, -uz, ux, uy, uz, -ux, -uy, -uz);
      varc.push(a1, a1, a2, a2);
      tri.push(base, base + 1, base + 3, base, base + 3, base + 2);
      arc = a2;
    }
  }
  if (!tri.length) return null;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(vpos, 3));
  geo.setAttribute("normal", new THREE.Float32BufferAttribute(vdir, 3));
  geo.setAttribute("aArc", new THREE.Float32BufferAttribute(varc, 1));
  geo.setIndex(tri);
  return geo;
}

// ---------------------------------------------------------------------------------------------
// The figure. One field for trunk + head + limbs, a second for the hand.
//
// LABELLING is separate from SHAPE: the region fields below never contribute to the surface,
// they only say which region owns each point of it. That is what lets the head stay a smooth
// ellipsoid while `eye`, `mouth` and `ear` remain flush, hit-testable patches, and what lets
// `shoulder`, `elbow`, `knee` and `ankle` be bands on a continuous limb rather than sleeves.
// Regions are built as disjoint CSG sets (a subtraction, not a nearest-core contest), so every
// seam is exactly where it is declared to be.
// ---------------------------------------------------------------------------------------------

const HAND_PARTS = ["wrist", "hand", "finger", "finger", "finger", "finger", "finger"];

function armPivot(sx) {
  return [sx * 0.214, 1.382, -0.004];
}

// Roll a point about the shoulder so the arm hangs clear of the thigh, and step it outboard of
// the widened chest. The hand mesh is transformed by exactly the same rigid motion.
function abductPoint(sx, p) {
  const pv = armPivot(sx);
  const a = sx * ARM_ABDUCT;
  const c = Math.cos(a);
  const s = Math.sin(a);
  const dx = p[0] - pv[0];
  const dy = p[1] - pv[1];
  return [pv[0] + dx * c - dy * s + sx * ARM_DX, pv[1] + dx * s + dy * c, p[2]];
}

function buildBodyField() {
  const solids = [];
  const labels = [];
  const fns = [];
  const add = (region, side, f) => {
    labels.push({ region, side });
    fns.push(f);
  };

  // --- shape -------------------------------------------------------------------------------
  const HAX = [HR * HEAD_S[0], HR * HEAD_S[1], HR * HEAD_S[2]];
  const head = ellipsoidField(HEAD_C, HAX);
  solids.push({
    f: head, k: 0.04, cx: HEAD_C[0], cy: HEAD_C[1], cz: HEAD_C[2],
    R: Math.max(HAX[0], HAX[1], HAX[2]), minS: 1,
  });
  // Neck: radius +15% on the old figure, so the head does not perch on a stem.
  solids.push(solid([0, 1.35, -0.004], [0, 1.5, 0.0], 0.059, 0.054, 0.05));
  // Trunk: three ellipsoids -- broad and shallow at the chest, rounder at the waist and pelvis.
  // Ellipsoids, not capsules: a capsule carries a hemispherical cap past its end point, which
  // at the top of the chest reads as a hood around the head.
  const trunk = (c, r, k) => {
    solids.push({ f: ellipsoidField(c, r), k, cx: c[0], cy: c[1], cz: c[2], R: Math.max(...r), minS: 1 });
  };
  trunk([0, 1.258, -0.006], [0.203, 0.186, 0.112], 0.075);
  trunk([0, 1.055, 0.0], [0.148, 0.118, 0.099], 0.08);
  trunk([0, 0.928, 0.008], [0.152, 0.096, 0.104], 0.08);

  for (const sx of [-1, 1]) {
    const S = abductPoint(sx, [sx * 0.199, 1.355, -0.005]);
    const E = abductPoint(sx, [sx * 0.235, 1.103, 0.002]);
    const W = abductPoint(sx, [sx * 0.258, 0.867, 0.02]);
    // Arm: upper arm and forearm meet as a blend, not a sleeve. Forearm radii +10% except at
    // the wrist, where the hand's own field takes over and must stay the wider of the two.
    solids.push(solid(S, E, 0.05, 0.0415, 0.058));
    solids.push(solid(E, W, 0.0432, 0.026, 0.05));
    const H = [sx * 0.088, 0.935, 0.0];
    const K = [sx * 0.096, 0.505, 0.007];
    const CALF = [sx * 0.096, 0.37, -0.004];
    const A = [sx * 0.096, 0.145, -0.014];
    solids.push(solid(H, K, 0.082, 0.052, 0.055));
    solids.push(solid(K, CALF, 0.0515, 0.0445, 0.045));
    solids.push(solid(CALF, A, 0.0445, 0.029, 0.04));
    // Heel: carries the ankle down to the sole, so the foot is part of the leg rather than a
    // separate block sitting under it.
    solids.push(solid(A, [sx * 0.098, 0.048, -0.032], 0.029, 0.034, 0.035));
    solids.push(
      solid([sx * 0.096, 0.036, -0.04], [sx * 0.104, 0.03, 0.08], 0.04, 0.03, 0.035,
        [1, 0.6, 1]),
    );
    solids.push(
      solid([sx * 0.104, 0.028, 0.088], [sx * 0.107, 0.025, 0.128], 0.028, 0.022, 0.03,
        [1, 0.55, 1]),
    );
  }

  // --- labelling ---------------------------------------------------------------------------
  // Head, in normalised head coordinates: fwd 1 = front of the face, up 1 = crown, lat 1 = side.
  const hc = (x, y, z) => [
    Math.abs(x - HEAD_C[0]) / HAX[0],
    (y - HEAD_C[1]) / HAX[1],
    (z - HEAD_C[2]) / HAX[2],
  ];
  // A stadium (rounded-end band) and an oval, both in normalised head coordinates: the eye is
  // one shallow visor band across the front of the head, the mouth a band below it, the ear a
  // flush oval on each side. None of them is modelled -- they are flat patches of one smooth
  // ellipsoid, so the head carries no sockets, no lids, no lips and no brow.
  const stadium = (u, v, hu, ru, rv) => Math.hypot(Math.max(Math.abs(u) - hu, 0) / ru, v / rv) - 1;
  const boxEye = (x, y, z) => {
    const [lat, up, fwd] = hc(x, y, z);
    return Math.max(0.4 - fwd, stadium(lat, up - 0.14, 0.5, 0.28, 0.105)) * HR;
  };
  const boxMouth = (x, y, z) => {
    const [lat, up, fwd] = hc(x, y, z);
    return Math.max(0.45 - fwd, stadium(lat, up + 0.55, 0.26, 0.24, 0.085)) * HR;
  };
  const boxEar = (x, y, z) => {
    const [lat, up, fwd] = hc(x, y, z);
    return Math.max(0.7 - lat, Math.hypot((up + 0.02) / 0.3, (fwd + 0.15) / 0.34) - 1) * HR;
  };
  const boxFace = (x, y, z) => {
    const [lat, up, fwd] = hc(x, y, z);
    return Math.max(0.34 - fwd, Math.hypot(lat / 0.94, (up + 0.08) / 0.62) - 1) * HR;
  };
  const boxHead = (x, y, z) => {
    const [lat, up, fwd] = hc(x, y, z);
    return (Math.sqrt(lat * lat + up * up + fwd * fwd) - 1.02) * HR;
  };
  add("head", 0, (x, y, z) => Math.max(boxHead(x, y, z), -boxFace(x, y, z), -boxEar(x, y, z)));
  add("face", 0, (x, y, z) =>
    Math.max(boxFace(x, y, z), -boxEye(x, y, z), -boxMouth(x, y, z)));
  add("eye", 0, boxEye);
  add("mouth", 0, boxMouth);
  for (const sx of [-1, 1]) {
    add("ear", sx, (x, y, z) => Math.max(boxEar(x, y, z), sx > 0 ? -x : x));
  }

  // Trunk and neck: y bands inside a generous cylinder about the figure's own axis.
  const axial = (x, z, r) => Math.hypot(x, z) - r;
  const band = (lo, hi) => (y) => Math.max(lo - y, y - hi);
  const neckBand = band(1.415, 1.545);
  add("neck", 0, (x, y, z) => Math.max(axial(x, z + 0.002, 0.09), neckBand(y)));
  const trunkR = (x, z) => axial(x, z * 1.35, 0.245);
  add("upper_trunk", 0, (x, y, z) => Math.max(trunkR(x, z), band(1.1, 1.415)(y)));
  add("lower_trunk", 0, (x, y, z) => Math.max(trunkR(x, z), band(0.945, 1.1)(y)));
  add("pubic_region", 0, (x, y, z) => Math.max(trunkR(x, z), band(0.845, 0.945)(y)));

  // Limbs: bands along the limb's own axis, inside a generous cylinder about it. Adjacent bands
  // share a boundary, so `shoulder`, `elbow`, `knee` and `ankle` are named stretches of one
  // continuous surface -- there is no joint to see, only a region to click.
  const axisBand = (a, b, radius, u0, u1) => {
    const bx = b[0] - a[0];
    const by = b[1] - a[1];
    const bz = b[2] - a[2];
    const bb = bx * bx + by * by + bz * bz;
    const L = Math.sqrt(bb);
    return (x, y, z) => {
      const px = x - a[0];
      const py = y - a[1];
      const pz = z - a[2];
      const u = (px * bx + py * by + pz * bz) / bb;
      const h = clamp01(u);
      const qx = px - bx * h;
      const qy = py - by * h;
      const qz = pz - bz * h;
      return Math.max(Math.sqrt(qx * qx + qy * qy + qz * qz) - radius, (u0 - u) * L, (u - u1) * L);
    };
  };
  for (const sx of [-1, 1]) {
    const S = abductPoint(sx, [sx * 0.2, 1.386, -0.005]);
    const W = abductPoint(sx, [sx * 0.258, 0.867, 0.02]);
    add("shoulder", sx, axisBand(S, W, 0.1, -0.35, 0.11));
    add("upper_arm", sx, axisBand(S, W, 0.1, 0.11, 0.45));
    add("elbow", sx, axisBand(S, W, 0.1, 0.45, 0.56));
    add("lower_arm", sx, axisBand(S, W, 0.1, 0.56, 1.35));
    const H = [sx * 0.088, 0.955, 0.0];
    const A = [sx * 0.096, 0.105, -0.012];
    add("upper_leg", sx, axisBand(H, A, 0.125, -0.2, 0.535));
    add("knee", sx, axisBand(H, A, 0.125, 0.535, 0.63));
    add("lower_leg", sx, axisBand(H, A, 0.125, 0.63, 0.955));
    add("ankle", sx, axisBand(H, A, 0.125, 0.955, 1.08));
    const foot = axisBand(H, A, 0.16, 1.08, 3.0);
    add("foot", sx, (x, y, z) => Math.max(foot(x, y, z), z - 0.093));
    add("toe", sx, (x, y, z) => Math.max(foot(x, y, z), 0.093 - z));
  }

  const parts = (x, y, z) => {
    const out = new Array(fns.length);
    for (let i = 0; i < fns.length; i++) out[i] = fns[i](x, y, z);
    return out;
  };
  return { sdf: fieldOf(solids), parts, labels };
}

// The right hand (sx = -1) in its relaxed hang: palm toward the thigh (+x), thumb forward.
// One field, so no part meets another at a seam; cut into wrist / hand / five digits.
function buildHandField() {
  const X = (v) => -v;
  const wrist = roundCone([X(0.2585), 0.884, 0.0195], [X(0.2625), 0.826, 0.0235], 0.0271, 0.0236);
  const pc = [X(0.2655), 0.7955, 0.0245];
  const palm = (x, y, z) => {
    const ty = clamp01((pc[1] + 0.027 - y) / 0.054);
    const hz = 0.0185 + 0.0075 * ty;
    const R = 0.012;
    const qy = Math.abs(y - pc[1]) - 0.027 + R;
    const qz = Math.abs(z - pc[2]) - hz + R;
    const d2 = Math.hypot(Math.max(qy, 0), Math.max(qz, 0)) + Math.min(Math.max(qy, qz), 0) - R;
    const edge = clamp01(Math.abs(z - pc[2]) / (hz + 0.006));
    const qx = Math.abs(x - pc[0]) - 0.0052 * (1 - 0.55 * edge * edge);
    return Math.min(Math.max(d2, qx), 0) + Math.hypot(Math.max(d2, 0), Math.max(qx, 0)) - 0.0095;
  };
  const thenar = roundCone([X(0.2605), 0.826, 0.036], [X(0.2575), 0.797, 0.0565], 0.0118, 0.0106);
  const digits = [];
  const specs = [
    { z: 0.0485, y: 0.773, lens: [0.041, 0.023, 0.019], r: 0.0079, curl: 0.15, fan: 0.1 },
    { z: 0.0305, y: 0.769, lens: [0.044, 0.026, 0.02], r: 0.0082, curl: 0.19, fan: 0.03 },
    { z: 0.0125, y: 0.77, lens: [0.041, 0.024, 0.019], r: 0.0077, curl: 0.23, fan: -0.04 },
    { z: -0.0045, y: 0.776, lens: [0.034, 0.019, 0.016], r: 0.0067, curl: 0.27, fan: -0.12 },
  ];
  for (const f of specs) {
    const segs = [];
    let pt = [X(0.2655), f.y, f.z];
    let angle = 0.04;
    let r = f.r;
    f.lens.forEach((len, i) => {
      angle += f.curl * (i + 1) * 0.55;
      const d = [Math.sin(angle), -Math.cos(angle), f.fan];
      const l = Math.hypot(...d);
      const end = pt.map((v, c) => v + (d[c] / l) * len);
      const r1 = r * (i === 2 ? 0.84 : 0.93);
      segs.push(roundCone(pt, end, r, r1));
      pt = end;
      r = r1;
    });
    digits.push(segs);
  }
  {
    const segs = [];
    let pt = [X(0.2575), 0.797, 0.0565];
    const dirs = [[0.34, -0.74, 0.58], [0.3, -0.92, 0.26]];
    const lens = [0.031, 0.026];
    const radii = [[0.009, 0.0086], [0.0086, 0.0074]];
    dirs.forEach((d, i) => {
      const l = Math.hypot(...d);
      const end = pt.map((v, c) => v + (d[c] / l) * lens[i]);
      segs.push(roundCone(pt, end, radii[i][0], radii[i][1]));
      pt = end;
    });
    digits.push(segs);
  }
  const digit = (segs, x, y, z) => {
    let d = segs[0](x, y, z);
    for (let i = 1; i < segs.length; i++) d = smin(d, segs[i](x, y, z), 0.0022);
    return d;
  };
  const wristCut = (x, y, z) => Math.max(wrist(x, y, z), y - 0.884);
  const handOnly = (x, y, z) => smin(palm(x, y, z), thenar(x, y, z), 0.012);
  const sdf = (x, y, z) => {
    const base = smin(handOnly(x, y, z), wristCut(x, y, z), 0.014);
    let d = base;
    for (const segs of digits) d = Math.min(d, smin(base, digit(segs, x, y, z), 0.0065));
    return d;
  };
  const parts = (x, y, z) => [
    wristCut(x, y, z) + 0.001,
    handOnly(x, y, z),
    ...digits.map((segs) => digit(segs, x, y, z) - 0.0012),
  ];
  const surf = surfaceNets(sdf, [X(0.305), 0.665, -0.03], [X(0.215), 0.89, 0.1], 0.0014);
  return splitByPart(surf, parts, HAND_PARTS.length);
}

function buildFigure(THREE, materialFor, lineFor) {
  const group = new THREE.Group();
  const meshes = [];

  function addPiece(piece, region, side, mirror) {
    if (!piece.pos.length || !piece.idx.length) return;
    const pos = Float32Array.from(piece.pos);
    const nrm = Float32Array.from(piece.nrm);
    const idx = Array.from(piece.idx);
    if (mirror) {
      for (let i = 0; i < pos.length; i += 3) {
        pos[i] = -pos[i];
        nrm[i] = -nrm[i];
      }
      for (let i = 0; i < idx.length; i += 3) [idx[i + 1], idx[i + 2]] = [idx[i + 2], idx[i + 1]];
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
    geo.setAttribute("aRelief", new THREE.Float32BufferAttribute(Float32Array.from(piece.w), 1));
    geo.setIndex(idx);
    const mesh = new THREE.Mesh(geo, materialFor(region));
    mesh.castShadow = true;
    mesh.userData.region = region;
    mesh.userData.side = side;
    group.add(mesh);
    meshes.push(mesh);
    // Drafting linework along this region's own boundary. Built for every region, shown only
    // for a limited or suppressed one (see styleRegion) -- a region that is stable under one
    // filter is provisional under the next, and the ribbon is cheap to keep.
    const cg = contourGeometry(THREE, pos, nrm, idx);
    if (cg) {
      const line = new THREE.Mesh(cg, lineFor(region));
      line.raycast = () => {};
      line.userData.region = region;
      group.add(line);
    }
  }

  const body = buildBodyField();
  const bodySurf = surfaceNets(body.sdf, [-0.38, -0.02, -0.16], [0.38, 1.8, 0.2], BODY_H);
  const bodyPieces = splitByPart(bodySurf, body.parts, body.labels.length);
  bodyPieces.forEach((piece, g) => {
    const { region, side } = body.labels[g];
    addPiece(piece, region, side, false);
  });

  const hand = buildHandField();
  for (const sx of [-1, 1]) {
    const start = meshes.length;
    const lineStart = group.children.length;
    hand.forEach((piece, g) => addPiece(piece, HAND_PARTS[g], sx, sx > 0));
    // The hand is built un-abducted; move it with the arm it belongs to.
    const pivot = new THREE.Vector3(...armPivot(sx));
    const q = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(0, 0, 1),
      sx * ARM_ABDUCT,
    );
    for (let i = lineStart; i < group.children.length; i++) {
      const m = group.children[i];
      m.position.sub(pivot).applyQuaternion(q).add(pivot);
      m.quaternion.premultiply(q);
      m.position.x += sx * ARM_DX;
    }
    void start;
  }

  return { group, meshes };
}

// ---------------------------------------------------------------------------------------------
// Materials. A drawing is lit flat: matte Lambert shading with no specular, no clearcoat and no
// environment reflection, plus a fresnel ink term that inks the silhouette the way a drafter
// would. Confidence lives entirely in the contour material; magnitude entirely in the fill.
// ---------------------------------------------------------------------------------------------

function makeRegionMaterial(THREE) {
  const uniforms = {
    uInk: { value: new THREE.Color(GRAPHITE) },
    uEdge: { value: EDGE.stable },
    uRelief: { value: 0.0 },
  };
  const m = new THREE.MeshLambertMaterial({ color: PAPER });
  m.userData.uniforms = uniforms;
  m.userData.baseColor = new THREE.Color(PAPER);
  m.userData.baseEdge = EDGE.stable;
  m.userData.baseRelief = 0;
  m.userData.state = "absent";
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        "#include <common>\nattribute float aRelief;\nuniform float uRelief;\n" +
          "varying vec3 vWNrm;\nvarying vec3 vVDir;",
      )
      .replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\ntransformed += normalize(objectNormal) * (uRelief * aRelief);",
      )
      .replace(
        "#include <project_vertex>",
        "#include <project_vertex>\nvWNrm = normalize(mat3(modelMatrix) * objectNormal);\n" +
          "vVDir = cameraPosition - (modelMatrix * vec4(transformed, 1.0)).xyz;",
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        "#include <common>\nuniform vec3 uInk;\nuniform float uEdge;\n" +
          "varying vec3 vWNrm;\nvarying vec3 vVDir;",
      )
      .replace(
        "#include <color_fragment>",
        "#include <color_fragment>\n" +
          "float wiiF = 1.0 - abs(dot(normalize(vWNrm), normalize(vVDir)));\n" +
          "diffuseColor.rgb = mix(diffuseColor.rgb, uInk, smoothstep(0.55, 0.99, wiiF) * uEdge);",
      );
  };
  m.customProgramCacheKey = () => "wii-body3d-draft-v1";
  return m;
}

// The confidence channel. One material per region; `uDash` is the only thing that says
// "provisional", and it is a line treatment, never a colour or a fill.
function makeLineMaterial(THREE) {
  const uniforms = {
    uLine: { value: 0.0016 },
    uDash: { value: 0.0 },
    uDashScale: { value: 100.0 },
  };
  const m = new THREE.MeshBasicMaterial({
    color: GRAPHITE,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: 1,
    depthWrite: false,
  });
  m.polygonOffset = true;
  m.polygonOffsetFactor = -2;
  m.polygonOffsetUnits = -2;
  m.userData.uniforms = uniforms;
  m.userData.baseColor = new THREE.Color(GRAPHITE);
  m.userData.baseOpacity = 1;
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        "#include <common>\nattribute float aArc;\nuniform float uLine;\nvarying float vArc;",
      )
      .replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\ntransformed += normalize(normal) * uLine;\nvArc = aArc;",
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        "#include <common>\nuniform float uDash;\nuniform float uDashScale;\nvarying float vArc;",
      )
      .replace(
        "#include <dithering_fragment>",
        "#include <dithering_fragment>\n" +
          "if (uDash > 0.5 && fract(vArc * uDashScale) > 0.58) discard;",
      );
  };
  m.customProgramCacheKey = () => "wii-body3d-line-v1";
  return m;
}

// The contract, applied to one region's fill and its contour:
//   stable      filled at the value's ink density, NO contour -- the figure stays one
//               continuous form whose ink varies, not a paint-by-numbers grid
//   limited     same fill one step lighter (the payload lightens it), dashed contour
//   suppressed  NO fill -- bare vellum -- dashed contour only
//   absent      no fill, and only the faintest SOLID hairline -- "nothing recorded" is
//               settled, and must not look like the dashed line that means "provisional"
//
// Only the exceptions are drawn, so a dashed region is genuinely exceptional. The figure's own
// outline comes from the fresnel ink term in `makeRegionMaterial`, not from this pass.
function styleRegion(THREE, m, lm, region) {
  const u = m.userData.uniforms;
  const lu = lm.userData.uniforms;
  const state = region ? region.state : "absent";
  const emphasis = region ? Math.max(0, Math.min(1, Number(region.emphasis) || 0)) : 0;
  m.userData.state = state;
  if (state === "stable" || state === "limited") {
    m.color.set((region && region.color) || PAPER);
  } else {
    m.color.setHex(PAPER);
  }
  m.userData.baseColor.copy(m.color);
  m.userData.baseEdge = EDGE[state] || EDGE.stable;
  u.uEdge.value = m.userData.baseEdge;
  // The depth channel: stable regions only, capped at RELIEF_MAX, scaled by emphasis.
  m.userData.baseRelief = state === "stable" ? RELIEF_MAX * emphasis : 0;
  u.uRelief.value = m.userData.baseRelief;
  // Three treatments, three meanings: no line at all (stable -- the fill says it), a dashed
  // construction line (provisional), and the faintest solid hairline (settled, but nothing
  // recorded). An absent region keeps that hairline so the silhouette does not dissolve into
  // the sheet wherever nothing was reported, and so "nothing here" never looks provisional.
  const provisional = state === "limited" || state === "suppressed";
  lm.userData.baseColor.setHex(provisional ? CONSTRUCTION : RULE);
  lm.userData.baseOpacity = provisional ? 0.95 : 0.6;
  lm.color.copy(lm.userData.baseColor);
  lm.opacity = lm.userData.baseOpacity;
  lm.visible = state !== "stable";
  lu.uDash.value = provisional ? 1 : 0;
}

// ---------------------------------------------------------------------------------------------
// Instance
// ---------------------------------------------------------------------------------------------

function createInstance(root, libs) {
  const { THREE, OrbitControls } = libs;
  const stage = root.querySelector(".b3d-stage");
  const inst = {
    THREE,
    root,
    payload: null,
    regionsSig: null,
    appliedTool: undefined,
    appliedSelected: undefined,
    selected: null,
    hovered: null,
    peeked: null,
    flash: null,
    tween: null,
    dimTween: null,
    dimK: 0,
    seqTimers: [],
    disposed: false,
    setTriggerValue: null,
    raf: 0,
    dirty: true,
    scratch: new THREE.Vector3(),
    sph: new THREE.Spherical(),
  };

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.tabIndex = 0;
  stage.appendChild(renderer.domElement);
  inst.renderer = renderer;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.02, 60);
  inst.scene = scene;
  inst.camera = camera;

  // A drawing is lit flat. Near-ambient hemisphere light carries the fill, one soft key gives
  // just enough form to read the volume and casts the contact shadow. No environment map, no
  // specular, no rim: a glossy highlight is what made the old figure read as a mannequin.
  scene.add(new THREE.AmbientLight(0xffffff, 1.55));
  scene.add(new THREE.HemisphereLight(0xffffff, VELLUM, 0.85));
  const key = new THREE.DirectionalLight(0xffffff, 0.75);
  key.position.set(0.8, 3.4, 2.6);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.left = -1.6;
  key.shadow.camera.right = 1.6;
  key.shadow.camera.top = 1.6;
  key.shadow.camera.bottom = -1.6;
  key.shadow.radius = 10;
  key.shadow.bias = -0.0008;
  scene.add(key);

  // Ground: a soft contact occlusion only. No plinth, no grid floor, no bevel -- the figure
  // stands on the sheet.
  const shadowPlane = new THREE.Mesh(
    new THREE.CircleGeometry(1.6, 64),
    new THREE.ShadowMaterial({ color: GRAPHITE, opacity: 0.16 }),
  );
  shadowPlane.rotation.x = -Math.PI / 2;
  shadowPlane.receiveShadow = true;
  scene.add(shadowPlane);

  // Figure.
  const materials = new Map();
  const lines = new Map();
  const materialFor = (region) => {
    if (!materials.has(region)) materials.set(region, makeRegionMaterial(THREE));
    return materials.get(region);
  };
  const lineFor = (region) => {
    if (!lines.has(region)) lines.set(region, makeLineMaterial(THREE));
    return lines.get(region);
  };
  const { group, meshes } = buildFigure(THREE, materialFor, lineFor);
  scene.add(group);
  inst.materials = materials;
  inst.lines = lines;
  inst.meshes = meshes;

  // Selection / hover / pulse outlines: an inverted hull pushed out along normals. It sits
  // outside the region and never tints the region itself, so it cannot be read as a value
  // change. Hulls are created lazily, only for meshes that have ever needed one.
  // Each outline material is used by at most ONE region at a time (one selection, one hover,
  // one pulse), so it can carry that region's own relief: without it, an outline around a
  // lifted region would sit buried under the surface it is meant to mark.
  const makeOutline = (hex, opacity, widthUniform) => {
    const m = new THREE.MeshBasicMaterial({
      color: hex, side: THREE.BackSide, transparent: true, opacity,
    });
    m.userData.relief = { value: 0 };
    m.onBeforeCompile = (shader) => {
      shader.uniforms.uOutline = widthUniform;
      shader.uniforms.uORelief = m.userData.relief;
      shader.vertexShader = shader.vertexShader
        .replace(
          "#include <common>",
          "#include <common>\nuniform float uOutline;\nuniform float uORelief;\n" +
            "attribute float aRelief;",
        )
        .replace(
          "#include <begin_vertex>",
          "#include <begin_vertex>\n" +
            "transformed += normalize(normal) * (uOutline + uORelief * aRelief);",
        );
    };
    m.customProgramCacheKey = () => "wii-body3d-outline-v1";
    return m;
  };
  inst.outlineUniform = { value: 0.004 };
  inst.flashUniform = { value: 0.006 };
  inst.selOutline = makeOutline(BLUEPRINT, 1, inst.outlineUniform);
  inst.hoverOutline = makeOutline(GRAPHITE, 0.55, inst.outlineUniform);
  inst.flashOutline = makeOutline(BLUEPRINT, 1, inst.flashUniform);
  inst.hulls = new Set();

  // Per-region anchors on the figure's right side (sx = -1), used for camera framing.
  inst.anchors = new Map();
  const box = new THREE.Box3();
  group.updateMatrixWorld(true);
  for (const region of materials.keys()) {
    box.makeEmpty();
    for (const m of meshes) {
      if (m.userData.region === region && m.userData.side <= 0) box.expandByObject(m);
    }
    if (!box.isEmpty()) inst.anchors.set(region, box.getCenter(new THREE.Vector3()));
  }
  // The hand as a whole -- wrist included -- is what the Hand view frames.
  box.makeEmpty();
  for (const m of meshes) {
    if (HAND_REGIONS.has(m.userData.region) && m.userData.side < 0) box.expandByObject(m);
  }
  inst.handCenter = box.getCenter(new THREE.Vector3());

  // Controls.
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enablePan = false;
  controls.minDistance = 0.3;
  controls.maxDistance = 6.5;
  controls.minPolarAngle = 0.18;
  controls.maxPolarAngle = Math.PI * 0.56;
  controls.rotateSpeed = 0.8;
  controls.zoomSpeed = 0.9;
  controls.target.set(...BODY_TARGET);
  controls.addEventListener("start", () => {
    // The user takes over mid-flight: drop the flight and any scheduled follow-up move.
    cancelSequence(inst);
  });
  controls.addEventListener("change", () => {
    inst.dirty = true;
  });
  inst.controls = controls;

  // Picking with a small screen-space tolerance: the exact ray first, then rings of sample
  // rays at 3px and 6px; the first ring with any hit wins, nearest hit within it.
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const RINGS = [[[0, 0]]];
  for (const rad of [3, 6]) {
    const ring = [];
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2;
      ring.push([Math.cos(a) * rad, Math.sin(a) * rad]);
    }
    RINGS.push(ring);
  }
  const pick = (ev) => {
    const r = renderer.domElement.getBoundingClientRect();
    for (const ring of RINGS) {
      let best = null;
      for (const [dx, dy] of ring) {
        const x = ev.clientX + dx - r.left;
        const y = ev.clientY + dy - r.top;
        ndc.set((x / r.width) * 2 - 1, -(y / r.height) * 2 + 1);
        raycaster.setFromCamera(ndc, camera);
        const hit = raycaster.intersectObjects(meshes, false)[0];
        if (hit && (!best || hit.distance < best.distance)) best = hit;
      }
      if (best) return best.object.userData.region;
    }
    return null;
  };
  inst.pick = pick;
  let down = null;
  const onMove = (ev) => {
    if (ev.pointerType === "touch") return; // touch uses tap-to-peek, never hover
    inst.pointer = { clientX: ev.clientX, clientY: ev.clientY };
    setHover(inst, pick(ev), ev);
  };
  const onLeave = () => {
    inst.pointer = null;
    setHover(inst, null);
  };
  const onDown = (ev) => {
    down = { x: ev.clientX, y: ev.clientY, touch: ev.pointerType === "touch" };
  };
  const onUp = (ev) => {
    if (!down) return;
    const moved = Math.hypot(ev.clientX - down.x, ev.clientY - down.y);
    const touch = down.touch;
    down = null;
    if (moved > 5) return;
    const region = pick(ev);
    // BODY-04: on touch there is no hover, so the first tap PEEKS (pins the tooltip, changes
    // nothing) and only a second tap on the same region selects and reruns the page.
    if (touch) {
      if (!region) {
        inst.peeked = null;
        setHover(inst, null);
        return;
      }
      if (inst.peeked !== region) {
        inst.peeked = region;
        inst.pointer = { clientX: ev.clientX, clientY: ev.clientY };
        setHover(inst, region, ev);
        return;
      }
      inst.peeked = null;
    }
    if (region) selectRegion(inst, region, true);
  };
  const onKey = (ev) => handleKey(inst, ev);
  const cv = renderer.domElement;
  cv.addEventListener("pointermove", onMove);
  cv.addEventListener("pointerleave", onLeave);
  cv.addEventListener("pointerdown", onDown);
  cv.addEventListener("pointerup", onUp);
  cv.addEventListener("keydown", onKey);

  // Sizing.
  const resize = () => {
    const w = stage.clientWidth || 800;
    const h = stage.clientHeight || 560;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    inst.dirty = true;
  };
  const ro = new ResizeObserver(resize);
  ro.observe(stage);
  resize();

  // Loop: renders only when something changed (controls damping, tween, pulse, hover). There is
  // no idle motion in this scene -- every frame is the consequence of a gesture or new data.
  const loop = (now) => {
    if (inst.disposed) return;
    inst.raf = requestAnimationFrame(loop);
    tick(inst, now);
    if (controls.update()) inst.dirty = true;
    if (inst.dirty) {
      applyHighlights(inst);
      renderer.render(scene, camera);
      inst.dirty = false;
      updateOverlays(inst);
      if (inst.rehover) {
        inst.rehover = false;
        rehover(inst);
      }
    }
  };
  inst.raf = requestAnimationFrame(loop);

  inst.dispose = () => {
    inst.disposed = true;
    cancelAnimationFrame(inst.raf);
    for (const t of inst.seqTimers) clearTimeout(t);
    ro.disconnect();
    cv.removeEventListener("pointermove", onMove);
    cv.removeEventListener("pointerleave", onLeave);
    cv.removeEventListener("pointerdown", onDown);
    cv.removeEventListener("pointerup", onUp);
    cv.removeEventListener("keydown", onKey);
    controls.dispose();
    scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material && o.material.map) o.material.map.dispose();
      if (o.material) o.material.dispose();
    });
    renderer.dispose();
    renderer.domElement.remove();
    if (window.__wiiBody3d === inst.api) delete window.__wiiBody3d;
  };

  // Small read-only hook used by the browser verification script.
  inst.api = {
    project(region) {
      const a = inst.anchors.get(region);
      if (!a) return null;
      const v = a.clone().project(camera);
      const r = renderer.domElement.getBoundingClientRect();
      return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
    },
    state: () => ({
      selected: inst.selected,
      flashing: Boolean(inst.flash),
      tweening: Boolean(inst.tween),
      distance: camera.position.distanceTo(controls.target),
    }),
    // The full camera pose. The choreography is a behavioural contract now -- one flight per
    // question, of a stated length, with the arrival pulse moving nothing -- and `distance`
    // alone cannot see it: the dolly is held back by DOLLY_LAG_MS at each end, so the radius is
    // deliberately constant while the orbit is still turning.
    pose: () => ({
      position: camera.position.toArray(),
      target: controls.target.toArray(),
      distance: camera.position.distanceTo(controls.target),
    }),
    // Current colour (hex) of every mesh assigned to a region. All meshes of a region share one
    // material, so e.g. meshColors("finger") returns one identical value per finger segment.
    meshColors(region) {
      return inst.meshes
        .filter((m) => m.userData.region === region)
        .map((m) => `#${m.material.color.getHexString()}`);
    },
    // The steady state each region was styled into, so the confidence channel can be verified
    // without sampling pixels (a hairline at whole-body distance is sub-pixel to measure).
    regionStates() {
      const out = {};
      for (const [region, m] of inst.materials) out[region] = m.userData.state;
      return out;
    },
    // Line treatment per region: the confidence channel, readable without sampling pixels.
    lineState(region) {
      const lm = inst.lines.get(region);
      if (!lm) return null;
      return {
        visible: lm.visible,
        dashed: lm.userData.uniforms.uDash.value > 0.5,
        color: `#${lm.color.getHexString()}`,
      };
    },
  };
  window.__wiiBody3d = inst.api;

  bindDom(inst, root);
  return inst;
}

function bindDom(inst, root) {
  const stage = root.querySelector(".b3d-stage");
  if (inst.renderer.domElement.parentNode !== stage) stage.appendChild(inst.renderer.domElement);
  inst.root = root;
  let live = root.querySelector(".b3d-live");
  if (!live) {
    live = document.createElement("p");
    live.className = "b3d-live";
    live.setAttribute("aria-live", "polite");
    live.setAttribute("aria-atomic", "true");
    root.appendChild(live);
  }
  stage.setAttribute("role", "group");
  const cv = inst.renderer.domElement;
  cv.setAttribute("role", "application");
  cv.setAttribute("aria-roledescription", "3D body map");
  inst.ui = {
    tool: root.querySelector(".b3d-tool"),
    readout: root.querySelector(".b3d-readout"),
    empty: root.querySelector(".b3d-empty"),
    note: root.querySelector(".b3d-finger-note"),
    tip: root.querySelector(".b3d-tip"),
    live,
    stage,
    bodyBtn: root.querySelector('[data-view="body"]'),
    handBtn: root.querySelector('[data-view="hand"]'),
  };
  inst.ui.bodyBtn.onclick = () => {
    cancelSequence(inst);
    flyTo(inst, bodyView(), VIEW_BTN_MS);
  };
  inst.ui.handBtn.onclick = () => {
    cancelSequence(inst);
    flyTo(inst, handView(inst), VIEW_BTN_MS);
  };
  root.dataset.state = "ready";
  updateLabel(inst);
}

// ---------------------------------------------------------------------------------------------
// Camera views and the motion grammar
//
// Every move answers a user action or a data change; nothing moves on its own. One move, one
// question, and every move ends in a hold long enough to read. Under `prefers-reduced-motion`
// each flight becomes a cut with the same holds and the same dimming.
// ---------------------------------------------------------------------------------------------

function bodyView() {
  const t = BODY_TARGET;
  return {
    target: t,
    pos: [t[0] + BODY_OFFSET[0], t[1] + BODY_OFFSET[1], t[2] + BODY_OFFSET[2]],
  };
}

// Centred on the whole hand -- wrist, palm and digits -- so nothing hangs out of frame.
function handView(inst) {
  const f = inst.handCenter.clone();
  const d = new inst.THREE.Vector3(...HAND_OFFSET).normalize().multiplyScalar(HAND_DIST);
  return { target: f.toArray(), pos: f.clone().add(d).toArray() };
}

// A close view of one region: its right-side anchor, seen from the front and slightly to the
// outside, at a distance that suits the region's size and still keeps the figure in frame.
function regionView(inst, region) {
  const { THREE } = inst;
  let a = inst.anchors.get(region);
  if (!a) return bodyView();
  if (region === "finger") return handView(inst);
  const aspect = inst.camera.aspect || 1;
  const halfW = Math.tan(THREE.MathUtils.degToRad(inst.camera.fov / 2)) * aspect;
  const narrow = clamp01((1.1 - aspect) / 0.6);
  let dir;
  let dist;
  if (HAND_REGIONS.has(region)) {
    dir = new THREE.Vector3(...HAND_OFFSET);
    dist = Math.max(HAND_DIST, 0.135 / halfW);
  } else if (HEAD_REGIONS.has(region)) {
    // On a narrow canvas the horizontal field of view shrinks with the aspect, so turn toward
    // the front, aim between the region and the head's centre, and back off until a head-sized
    // circle fits the canvas width.
    dir = new THREE.Vector3(HEAD_DIR[0] * (1 - 0.55 * narrow), HEAD_DIR[1], HEAD_DIR[2]);
    const head = inst.anchors.get("head");
    if (head && narrow > 0) a = a.clone().lerp(head, 0.5 * narrow);
    dist = Math.max(0.62, HEAD_FIT_RADIUS / halfW);
  } else if (region === "neck") {
    dir = new THREE.Vector3(...HEAD_DIR);
    dist = Math.max(0.75, 0.13 / halfW);
  } else if (TRUNK_REGIONS.has(region)) {
    dir = new THREE.Vector3(...BODY_OFFSET);
    dist = Math.max(1.5, 0.26 / halfW);
  } else {
    dir = new THREE.Vector3(...LIMB_DIR);
    if (a.x < -0.01) dir.x = -dir.x; // look from the anchor's own side
    const small = region === "toe" || region === "ankle" || region === "foot";
    dist = Math.max(small ? 0.8 : 1.0, (small ? 0.14 : 0.18) / halfW);
  }
  dir.normalize();
  const pos = a.clone().addScaledVector(dir, dist);
  return { target: a.toArray(), pos: pos.toArray() };
}

// Cancel, never queue: a second payload mid-flight drops the current tween and the new move
// starts from wherever the camera is now.
function cancelSequence(inst) {
  for (const t of inst.seqTimers) clearTimeout(t);
  inst.seqTimers = [];
  inst.tween = null;
  inst.flash = null;
}

function later(inst, ms, fn) {
  const id = setTimeout(() => {
    if (!inst.disposed) fn();
  }, Math.min(ms, MAX_SEQUENCE_MS));
  inst.seqTimers.push(id);
}

function flyTo(inst, view, ms, opts) {
  const o = opts || {};
  const { camera, controls, THREE } = inst;
  // Whatever the pointer was over is about to move out from under it: drop the hover now and
  // hit-test again once the camera lands (see rehover).
  if (inst.hovered || !inst.ui.tip.hidden) setHover(inst, null);
  const dur = Math.min(Math.max(0, ms || 0), MAX_FLIGHT_MS);
  if (prefersReducedMotion(inst.payload) || !dur) {
    controls.target.set(...view.target);
    camera.position.set(...view.pos);
    inst.tween = null;
    inst.dirty = true;
    inst.rehover = true;
    if (o.then) o.then();
    return;
  }
  const fromTarget = controls.target.clone();
  const toTarget = new THREE.Vector3(...view.target);
  const a = new THREE.Spherical().setFromVector3(camera.position.clone().sub(fromTarget));
  const b = new THREE.Spherical().setFromVector3(new THREE.Vector3(...view.pos).sub(toTarget));
  let dth = b.theta - a.theta;
  while (dth > Math.PI) dth -= Math.PI * 2;
  while (dth < -Math.PI) dth += Math.PI * 2;
  // Let the flight start or end beyond the orbit limit instead of fighting the clamp.
  controls.maxDistance = Math.max(6.5, a.radius + 0.05, b.radius + 0.05);
  // Orbit and dolly on separate timelines: the figure turns first and the camera then leans in,
  // so "flying to the hand" reads as turning to look and then closing in, not a straight dive.
  const split = o.split !== false && dur >= 600 && Math.abs(b.radius - a.radius) > 0.05;
  inst.tween = {
    t0: performance.now(),
    ms: dur,
    fromTarget,
    toTarget,
    a,
    b,
    dth,
    lag: split ? DOLLY_LAG_MS : 0,
    then: o.then,
  };
  inst.dirty = true;
}

// One soft pulse on the #1 hotspot. Not three: three at once is a light show, not an answer.
function startFlash(inst, regions, ms, then) {
  if (prefersReducedMotion(inst.payload)) {
    if (then) then();
    return;
  }
  inst.flash = { t0: performance.now(), ms, regions: new Set(regions), then };
  inst.dirty = true;
}

// The focus dim runs on its own timeline so it can lead the camera by DIM_MS.
function startDim(inst, to, ms) {
  if (prefersReducedMotion(inst.payload) || !ms) {
    inst.dimTween = null;
    inst.dimK = to;
    inst.dirty = true;
    return;
  }
  inst.dimTween = { t0: performance.now(), ms, from: inst.dimK, to };
  inst.dirty = true;
}

function tick(inst, now) {
  const tw = inst.tween;
  if (tw) {
    const k = clamp01((now - tw.t0) / tw.ms);
    const ka = flightEase(k);
    const kr = tw.lag
      ? flightEase(clamp01((now - tw.t0 - tw.lag) / Math.max(1, tw.ms - 2 * tw.lag)))
      : ka;
    inst.controls.target.lerpVectors(tw.fromTarget, tw.toTarget, ka);
    const s = inst.sph;
    s.theta = tw.a.theta + tw.dth * ka;
    s.phi = tw.a.phi + (tw.b.phi - tw.a.phi) * ka;
    s.radius = tw.a.radius + (tw.b.radius - tw.a.radius) * kr;
    inst.camera.position.copy(inst.controls.target).add(inst.scratch.setFromSpherical(s));
    inst.dirty = true;
    if (k >= 1) {
      inst.tween = null;
      inst.controls.maxDistance = 6.5;
      inst.rehover = true;
      if (tw.then) tw.then();
    }
  } else if (inst.controls.maxDistance !== 6.5) {
    inst.controls.maxDistance = 6.5;
  }
  const dm = inst.dimTween;
  if (dm) {
    const k = clamp01((now - dm.t0) / dm.ms);
    inst.dimK = dm.from + (dm.to - dm.from) * easeOutQuint(k);
    inst.dirty = true;
    if (k >= 1) inst.dimTween = null;
  }
  const fl = inst.flash;
  inst.pulse = 0;
  if (fl) {
    const k = (now - fl.t0) / fl.ms;
    if (k >= 1) {
      inst.flash = null;
      if (fl.then) fl.then();
    } else {
      inst.pulse = 0.5 * (1 - Math.cos(k * Math.PI * 2)); // exactly one cycle
    }
    inst.dirty = true;
  }
}

function ensureHull(inst, mesh) {
  let hull = mesh.userData.hull;
  if (!hull) {
    hull = new inst.THREE.Mesh(mesh.geometry, inst.selOutline);
    hull.raycast = () => {};
    hull.visible = false;
    mesh.add(hull);
    mesh.userData.hull = hull;
  }
  return hull;
}

// Selection focus, hover and the arrival pulse are layered over the steady material state each
// rendered frame. The steady state (baseColor / baseEdge / baseRelief / line treatment) is
// never modified here.
function applyHighlights(inst) {
  const flashRegions = inst.flash ? inst.flash.regions : null;
  const sel = inst.selected && inst.materials.has(inst.selected) ? inst.selected : null;
  // Drill-down focus: the tool's hotspots stay drawn and everything else fades toward the sheet,
  // exactly like a selection. It changes emphasis only, never a value.
  const spots = !sel && inst.focusRegions && inst.focusRegions.size ? inst.focusRegions : null;
  const p = inst.pulse || 0;
  const camD = inst.camera.position.distanceTo(inst.controls.target);
  const paper = inst.paper || (inst.paper = new inst.THREE.Color(PAPER));
  const dimK = sel || spots ? inst.dimK : 0;
  for (const [region, m] of inst.materials) {
    const u = m.userData.uniforms;
    const lm = inst.lines.get(region);
    const dim = Boolean(
      (sel ? region !== sel : spots && !spots.has(region)) && m.userData.state !== "absent",
    );
    const k = dim ? FOCUS_DIM * dimK : 0;
    m.userData.dim = dim;
    m.color.copy(m.userData.baseColor);
    if (k > 0) m.color.lerp(paper, k);
    u.uEdge.value = m.userData.baseEdge * (1 - 0.55 * k);
    // A faded region also lies flat: the depth channel belongs to what is being read now.
    u.uRelief.value = m.userData.baseRelief * (1 - k);
    if (!lm) continue;
    lm.color.copy(lm.userData.baseColor);
    if (k > 0) lm.color.lerp(paper, k * 0.85);
    lm.opacity = lm.userData.baseOpacity * (1 - 0.5 * k);
  }
  // Line weight follows the camera distance so the drawing reads the same at whole-body and
  // hand zoom; the dash period is tied to it, so a dashed contour is always a dashed contour.
  // ~3px of ink either way: heavy enough to carry theme.CONSTRUCTION's 3.35:1 contrast, light
  // enough to read as drafting linework rather than a hard border.
  const w = Math.min(0.0045, Math.max(0.0005, 0.0012 * camD));
  inst.outlineUniform.value = Math.min(0.009, Math.max(0.0016, 0.0019 * camD));
  inst.flashUniform.value = inst.outlineUniform.value * (1 + 3 * p);
  inst.flashOutline.opacity = 0.35 + 0.65 * p;
  for (const lm of inst.lines.values()) {
    lm.userData.uniforms.uLine.value = w;
    lm.userData.uniforms.uDashScale.value = 1 / (6 * w);
  }
  // Give each outline the relief of the region it is currently drawn around.
  const reliefOf = (region) => {
    const m = region && inst.materials.get(region);
    return m ? m.userData.uniforms.uRelief.value : 0;
  };
  inst.selOutline.userData.relief.value = reliefOf(sel);
  inst.hoverOutline.userData.relief.value = reliefOf(inst.hovered);
  inst.flashOutline.userData.relief.value = reliefOf(
    flashRegions && flashRegions.size ? [...flashRegions][0] : null,
  );
  for (const mesh of inst.meshes) {
    const r = mesh.userData.region;
    const isSel = sel === r;
    const isFlash = Boolean(flashRegions && flashRegions.has(r));
    const isHov = inst.hovered === r && !isSel;
    const want = isSel || isFlash || isHov;
    if (!want) {
      if (mesh.userData.hull) mesh.userData.hull.visible = false;
      continue;
    }
    const hull = ensureHull(inst, mesh);
    hull.visible = true;
    hull.material = isSel ? inst.selOutline : isFlash ? inst.flashOutline : inst.hoverOutline;
  }
}

function updateOverlays(inst) {
  const { camera, controls, ui } = inst;
  const dist = camera.position.distanceTo(controls.target);
  const nearHand = dist < 1.0 && controls.target.distanceTo(inst.handCenter) < 0.2;
  const mirrored = new inst.THREE.Vector3(-inst.handCenter.x, inst.handCenter.y, inst.handCenter.z);
  const nearLeft = dist < 1.0 && controls.target.distanceTo(mirrored) < 0.2;
  ui.note.hidden = !(nearHand || nearLeft);
  ui.handBtn.setAttribute("aria-pressed", String(nearHand || nearLeft));
  ui.bodyBtn.setAttribute("aria-pressed", String(dist > 3.4));
}

// ---------------------------------------------------------------------------------------------
// Interaction and accessibility
// ---------------------------------------------------------------------------------------------

function regionInfo(inst, region) {
  return (inst.payload && inst.payload.regions && inst.payload.regions[region]) || null;
}

function regionText(inst, region) {
  const info = regionInfo(inst, region);
  if (!info) return region;
  return `${info.label || region}: ${info.display || ""}`.replace(/: $/, "");
}

function updateLabel(inst) {
  const measure = (inst.payload && inst.payload.measure_label) ||
    "Estimated U.S. ER-treated injuries";
  const sel = inst.selected ? `Selected: ${regionText(inst, inst.selected)}.` : "No area selected.";
  const label =
    `Interactive 3D body injury map. ${measure}. ${sel} ` +
    "Arrow keys turn the figure, plus and minus zoom, Escape clears the selection.";
  inst.ui.stage.setAttribute("aria-label", label);
  inst.renderer.domElement.setAttribute("aria-label", label);
  // BODY-04: mirror the selected region's reading into the header, so the value stays legible
  // on a touch screen where there is no hover.
  const out = inst.ui.readout;
  if (!out) return;
  const info = inst.selected ? regionInfo(inst, inst.selected) : null;
  if (!info) {
    out.hidden = true;
    out.textContent = "";
    return;
  }
  out.hidden = false;
  out.textContent = String(info.tooltip || regionText(inst, inst.selected));
}

function announce(inst) {
  inst.ui.live.textContent = inst.selected
    ? `Selected: ${regionText(inst, inst.selected)}`
    : "Selection cleared";
}

function handleKey(inst, ev) {
  const { camera, controls, THREE } = inst;
  const offset = camera.position.clone().sub(controls.target);
  const s = new THREE.Spherical().setFromVector3(offset);
  let handled = true;
  switch (ev.key) {
    case "ArrowLeft":
      s.theta -= 0.1;
      break;
    case "ArrowRight":
      s.theta += 0.1;
      break;
    case "ArrowUp":
      s.phi -= 0.1;
      break;
    case "ArrowDown":
      s.phi += 0.1;
      break;
    case "+":
    case "=":
      s.radius *= 0.88;
      break;
    case "-":
    case "_":
      s.radius /= 0.88;
      break;
    case "Escape":
      if (inst.selected) selectRegion(inst, null, true);
      else handled = false;
      break;
    default:
      handled = false;
  }
  if (!handled) return;
  ev.preventDefault();
  if (ev.key === "Escape") return;
  cancelSequence(inst);
  s.phi = Math.min(controls.maxPolarAngle, Math.max(controls.minPolarAngle, s.phi));
  s.radius = Math.min(controls.maxDistance, Math.max(controls.minDistance, s.radius));
  camera.position.copy(controls.target).add(offset.setFromSpherical(s));
  inst.dirty = true;
}

function setHover(inst, region, ev) {
  const { tip, stage } = inst.ui;
  if (region !== inst.hovered) {
    inst.hovered = region;
    inst.dirty = true;
  }
  stage.classList.toggle("is-hot", Boolean(region));
  const info = region && regionInfo(inst, region);
  if (!info || !ev) {
    tip.hidden = true;
    return;
  }
  tip.textContent = "";
  const title = document.createElement("b");
  title.textContent = info.label || region;
  tip.appendChild(title);
  const tooltip = String(info.tooltip || info.display || "");
  // The tooltip usually already leads with the label; avoid saying it twice.
  const body = tooltip.startsWith(info.label || "\u0000")
    ? tooltip.slice((info.label || "").length).replace(/^[\s:\-–]+/, "")
    : tooltip;
  tip.appendChild(document.createTextNode(body));
  // Units and basis, so a reading is never a bare number (both formatted in Python).
  const basis = inst.payload && inst.payload.basis_label;
  if (basis) {
    const foot = document.createElement("small");
    foot.textContent = String(basis);
    tip.appendChild(foot);
  }
  tip.hidden = false;
  const r = inst.root.getBoundingClientRect();
  const x = ev.clientX - r.left;
  const y = ev.clientY - r.top;
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  const left = x + 16 + tw > r.width ? Math.max(8, x - 16 - tw) : x + 16;
  const top = Math.min(Math.max(8, y - th / 2), Math.max(8, r.height - th - 8));
  tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
}

// Re-run the hover hit-test at the last known pointer position (after a camera flight), so the
// tooltip names what is under the pointer now, or disappears if that is empty sheet.
function rehover(inst) {
  const p = inst.pointer;
  if (!p || !inst.pick) {
    setHover(inst, null);
    return;
  }
  setHover(inst, inst.pick(p), p);
}

// Selecting flies to the region; clearing returns to the whole body. Picking the selected
// region again clears it (matching Python's pick_region toggle), and a keyboard clear is sent
// to Python as exactly that: a re-pick of the current region.
function selectRegion(inst, region, fromUser) {
  const prev = inst.selected;
  const next = fromUser && region && region === prev ? null : region;
  cancelSequence(inst);
  inst.selected = next;
  inst.dirty = true;
  if (next) {
    // The outline appears at DEPARTURE: the reader sees what they hit before the camera moves,
    // which is why there is no arrival pulse here -- the outline is the confirmation.
    inst.focusRegions = null;
    startDim(inst, 1, DIM_MS);
    flyTo(inst, regionView(inst, next), SELECT_MS, { split: true });
  } else if (prev) {
    // The un-answer: fly back while the dimming lifts over the same 700ms. If a tool is still
    // active, its hotspot focus returns at the END of the flight -- never a second flight.
    inst.focusRegions = null;
    startDim(inst, 0, RETURN_MS);
    const spot = inst.activeSpot;
    flyTo(inst, bodyView(), RETURN_MS, {
      split: false,
      then: () => {
        if (!spot) return;
        inst.focusRegions = new Set(spot.regions.slice(0, 3));
        inst.dimK = 0;
        startDim(inst, 1, DIM_MS);
        inst.dirty = true;
      },
    });
  }
  updateLabel(inst);
  if (next !== prev) announce(inst);
  const sent = region || prev;
  if (fromUser && sent && inst.setTriggerValue) {
    inst.userPick = { from: inst.appliedSelected, to: next };
    inst.appliedSelected = next;
    inst.setTriggerValue("selected", sent);
  }
}

// The tool drill-down, and the one orchestrated moment in the product: dim the whole body so
// the answer lands before anything moves, hold it long enough to read, then turn and lean in to
// the #1 area and pulse it once. Total ~2.5s, inside the 2,600ms sequence cap.
function drill(inst, spot) {
  const top = spot.regions.filter((r) => inst.materials.has(r)).slice(0, 3);
  if (!top.length) return;
  const first = top.find((r) => inst.anchors.has(r)) || top[0];
  const hadFocus = Boolean(inst.focusRegions && inst.focusRegions.size);
  cancelSequence(inst);
  inst.focusRegions = new Set(top);
  inst.dirty = true;
  const target = regionView(inst, first);
  if (prefersReducedMotion(inst.payload)) {
    inst.dimK = 1;
    flyTo(inst, target, 0);
    return;
  }
  // 1. Dim (0-250ms), before the camera moves.
  if (!hadFocus) inst.dimK = 0;
  startDim(inst, 1, DIM_MS);
  const pulse = () => startFlash(inst, [first], PULSE_MS);
  const here = inst.controls.target;
  const camD = inst.camera.position.distanceTo(here);
  const atBody =
    camD > 3.4 && here.distanceTo(new inst.THREE.Vector3(...BODY_TARGET)) < 0.05;
  const alreadyFramed =
    camD < 1.7 && here.distanceTo(new inst.THREE.Vector3(...target.target)) < 0.06;
  if (alreadyFramed) {
    // Same region as the current frame: no flight at all, just the cross-dim and the pulse.
    later(inst, DIM_MS, pulse);
    return;
  }
  if (atBody) {
    // 2. Hold the whole body with the hotspots lit. 3. Turn and lean in. 4. Pulse #1 only.
    later(inst, DIM_MS + HOLD_MS, () =>
      flyTo(inst, target, FLIGHT_MS, { split: true, then: pulse }));
    return;
  }
  // Already zoomed in somewhere else: cross-dim in place, then one flight. No establishing shot.
  later(inst, DIM_MS, () =>
    flyTo(inst, target, ZOOMED_FLIGHT_MS, { split: true, then: pulse }));
}

function applyPayload(inst, payload, first) {
  inst.payload = payload;
  const regions = (payload && payload.regions) || {};
  const sig = JSON.stringify(regions);
  if (sig !== inst.regionsSig) {
    inst.regionsSig = sig;
    for (const [region, m] of inst.materials) {
      styleRegion(inst.THREE, m, inst.lines.get(region), regions[region]);
    }
    inst.dirty = true;
  }

  // Empty state: when nothing in this filter selection clears the reporting floor, the plate
  // says so rather than showing an unfilled figure that reads as broken.
  const ids = Object.keys(regions);
  const nothingDrawn =
    ids.length > 0 && ids.every((r) => regions[r].state === "suppressed" || regions[r].state === "absent");
  if (inst.ui.empty) inst.ui.empty.hidden = !nothingDrawn;

  // Only an explicitly chosen tool drives the drill-down; "all tools" stays on the whole body.
  const hotspots = Array.isArray(payload.hotspots) ? payload.hotspots : [];
  const toolId = payload.active_tool || null;
  const spot = (toolId && hotspots.find((h) => h.tool_id === toolId)) || null;
  inst.activeSpot = spot; // read by selectRegion to restore drill focus on deselect
  if (!spot && inst.focusRegions) {
    inst.focusRegions = null;
    inst.dirty = true;
  }
  const toolUi = inst.ui.tool;
  toolUi.textContent = "";
  toolUi.hidden = false;
  toolUi.classList.toggle("is-hint", !spot);
  if (spot) {
    const labels = spot.regions
      .slice(0, 3)
      .map((r) => (regions[r] && regions[r].label) || r)
      .join(", ");
    toolUi.append("Areas most often recorded with ");
    const s = document.createElement("strong");
    s.textContent = spot.tool_label;
    toolUi.append(s, `: ${labels}`);
  } else if (hotspots.length) {
    toolUi.append("Pick a tool to fly to the areas most often recorded with it.");
  } else {
    toolUi.hidden = true;
  }

  let sel = payload.selected || null;
  // A click reaches Python in the same run that builds the next payload, so the payload can lag
  // one run behind a click. Ignore the pre-click value until Python catches up.
  if (inst.userPick) {
    if (sel === inst.userPick.to || sel !== inst.userPick.from) inst.userPick = null;
    else sel = inst.userPick.to;
  }
  const selChanged = sel !== inst.appliedSelected;
  const toolChanged = toolId !== inst.appliedTool;
  inst.appliedTool = toolId;
  inst.appliedSelected = sel;

  if (first) {
    inst.selected = sel;
    const body = bodyView();
    const dest = sel ? regionView(inst, sel) : null;
    if (prefersReducedMotion(payload)) {
      if (spot && !dest) {
        inst.focusRegions = new Set(spot.regions.slice(0, 3));
        inst.dimK = 1;
      }
      if (dest) inst.dimK = 1;
      flyTo(inst, dest || (spot ? regionView(inst, spot.regions[0]) : body), 0);
      updateLabel(inst);
      return;
    }
    inst.controls.target.set(...body.target);
    if (dest || spot) {
      // A question has already been asked: start composed on the whole body and answer it. The
      // establishing shot is skipped entirely.
      inst.camera.position.set(...body.pos);
      if (dest) {
        inst.dimK = 1;
        later(inst, INTRO_HOLD_MS, () => flyTo(inst, dest, SELECT_MS, { split: true }));
      } else {
        drill(inst, spot);
      }
    } else {
      // Nothing has been asked: hold the wide shot briefly, then one flight in. No pulse.
      const t = body.target;
      inst.camera.position.set(
        t[0] + WIDE_OFFSET[0], t[1] + WIDE_OFFSET[1], t[2] + WIDE_OFFSET[2],
      );
      inst.dirty = true;
      later(inst, INTRO_HOLD_MS, () => flyTo(inst, body, INTRO_MS, { split: false }));
    }
    updateLabel(inst);
    return;
  }
  if (selChanged) {
    selectRegion(inst, sel, false);
  } else if (toolChanged) {
    if (spot && !inst.selected) drill(inst, spot);
    else if (!spot && !inst.selected) {
      cancelSequence(inst);
      startDim(inst, 0, RETURN_MS);
      flyTo(inst, bodyView(), RETURN_MS, { split: false });
    }
  }
  updateLabel(inst);
}

// ---------------------------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------------------------

export default function body3d(component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;
  const root = parentElement.querySelector(".b3d");
  if (!root) return undefined;
  const payload = data || { regions: {} };

  const existing = instances.get(parentElement);
  if (existing && existing !== "pending") {
    existing.setTriggerValue = setTriggerValue;
    if (existing.root !== root) bindDom(existing, root);
    applyPayload(existing, payload, false);
    return cleanupFor(parentElement);
  }
  if (existing === "pending") {
    // Three.js is still loading; remember the newest payload for when it arrives.
    parentElement.__b3dPending = payload;
    return cleanupFor(parentElement);
  }

  const fail = (reason) => {
    root.dataset.state = "nowebgl";
    root.querySelector(".b3d-fallback").hidden = false;
    root.dataset.reason = reason;
    // Persistent state (not a trigger) so the page keeps its 2D fallback on every rerun. The
    // reason must be set before `webgl`, which is what the page reacts to.
    setStateValue("reason", reason);
    setStateValue("webgl", false);
  };

  if (!hasWebGL()) {
    fail("no-webgl");
    return undefined;
  }

  instances.set(parentElement, "pending");
  parentElement.__b3dPending = payload;
  let inst = null;
  loadThree()
    .then((libs) => {
      try {
        inst = createInstance(parentElement.querySelector(".b3d") || root, libs);
      } catch (err) {
        console.error("body3d: WebGL renderer failed", err);
        instances.delete(parentElement);
        fail("renderer-failed");
        return;
      }
      inst.setTriggerValue = setTriggerValue;
      instances.set(parentElement, inst);
      applyPayload(inst, parentElement.__b3dPending, true);
    })
    .catch((err) => {
      console.error("body3d: could not load Three.js", err);
      instances.delete(parentElement);
      fail("three-load-failed");
    });

  return cleanupFor(parentElement);
}

// Streamlit keeps only the cleanup returned by the latest call and runs it on unmount.
function cleanupFor(parentElement) {
  return () => {
    const i = instances.get(parentElement);
    if (i && i !== "pending") i.dispose();
    instances.delete(parentElement);
  };
}
