// 3D wallet graph. Same interface as graph2d.ts: setData, highlight, focus, setActive.
// My wallets are coins standing over three orange rings; every other address is a small
// matte solid whose silhouette is its glyph in kinds.ts.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { PATH, chainLabel, chainOf, kindColor, kindOf, kindShape, linkColor, linkDash, pct, title, typeOf, type Kind, type Palette } from "@/lib/tracker/kinds";
import type { Graph, GraphNode } from "@/lib/tracker/types";
import { canvasFonts, drawChain, drawGlyph, fadeIn, reducedMotion, type Look, type Renderer, type RendererOptions } from "./draw";

const K = 14; // ideal distance between linked nodes, world units
const FLATTEN = 0.12; // extra pull towards the plinth's plane, so the graph reads as a table of coins
const LINE = 0.3; // opacity of plain transfer lines: many of them cross
const COIN = 6; // radius of a wallet's coin, world units
const NEAR = 170; // counterparties are named within this distance of the camera, world units
const REVEAL = { coin: 900, stagger: 110, rest: 700 }; // the entrance, in milliseconds
const FADE = 0.86; // how far a node off the shown path sinks into the background
const FLIGHT = 700; // ms of a camera flight
const HOP = 320; // ms between the hops of a path as it draws itself
const ease = (t: number) => 1 - (1 - Math.min(Math.max(t, 0), 1)) ** 3;

/** One line of text on a sprite: [text, font, colour, letter spacing]. */
type Text = [string, string, string, number?];
type Pair = [number, number?];

interface Node {
  n: GraphNode;
  color: string;
  shape: string;
  size: number;
  dim: number;
  label: Text[] | null;
  p: THREE.Vector3;
}

/** What one setData builds. */
interface World {
  hit: THREE.InstancedMesh;
  solids: { mesh: THREE.InstancedMesh; ids: number[] }[];
  coins: { coin: THREE.Mesh<THREE.BufferGeometry, THREE.Material[]>; i: number; kind: Kind }[];
  edgeLines: Lines;
  stems: Lines;
  guessLines: Lines[];
  arrows: THREE.InstancedMesh;
  linkLabels: [THREE.Sprite, number, number][];
  nodeLabels: [THREE.Sprite, number][];
}
interface Lines {
  obj: THREE.LineSegments<THREE.BufferGeometry, THREE.LineBasicMaterial | THREE.LineDashedMaterial>;
  /** Node indices per segment; a stem has one end only. */
  pairs: Pair[];
}

function sprite(canvas: HTMLCanvasElement, height: number, attenuate: boolean) {
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  // Drawn over the scene: a label must stay readable.
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, sizeAttenuation: attenuate, depthTest: false, transparent: true, fog: false }));
  s.scale.set((canvas.width / canvas.height) * height, height, 1);
  s.center.set(0.5, 0); // it stands on its anchor, so it never covers what it names
  s.renderOrder = 2;
  return s;
}

function dispose(material: THREE.Material | THREE.Material[]) {
  for (const m of [material].flat()) {
    if ("map" in m && m.map instanceof THREE.Texture) m.map.dispose();
    m.dispose();
  }
}

function clear(group: THREE.Object3D) {
  for (const child of [...group.children]) {
    group.remove(child);
    if (child instanceof THREE.InstancedMesh) child.dispose();
    if (child.userData.shared) continue; // its geometry and materials outlive a rebuild
    if (child instanceof THREE.Mesh || child instanceof THREE.Line) child.geometry.dispose();
    if (child instanceof THREE.Mesh || child instanceof THREE.Line || child instanceof THREE.Sprite) dispose(child.material);
  }
}

// What the solids reflect: a white ceiling over the page, and one bright window.
function environment(renderer: THREE.WebGLRenderer, p: Palette) {
  const canvas = Object.assign(document.createElement("canvas"), { width: 4, height: 128 });
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const sky = ctx.createLinearGradient(0, 0, 0, 128);
    for (const [at, color] of [[0, "#ffffff"], [0.45, p.cardBg], [0.6, p.background], [1, p.surfaceInset]] as const) sky.addColorStop(at, color);
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, 4, 128);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const room = new THREE.Scene();
  const dome = new THREE.Mesh(new THREE.SphereGeometry(50, 32, 16), new THREE.MeshBasicMaterial({ map: texture, side: THREE.BackSide }));
  const pane = new THREE.Mesh(new THREE.PlaneGeometry(26, 26), new THREE.MeshBasicMaterial({ color: new THREE.Color(2, 2, 2) }));
  pane.position.set(-24, 30, 26);
  pane.lookAt(0, 0, 0);
  room.add(dome, pane);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const map = pmrem.fromScene(room, 0.03).texture;
  pmrem.dispose();
  for (const mesh of [dome, pane]) {
    mesh.geometry.dispose();
    mesh.material.dispose();
  }
  texture.dispose();
  return map;
}

// One solid per glyph, about one unit in radius, modelled facing +z: it is turned to the viewer.
function solids(): Record<string, THREE.BufferGeometry> {
  const slab = (points: number[][], depth: number) =>
    new THREE.ExtrudeGeometry(new THREE.Shape(points.map(([x = 0, y = 0]) => new THREE.Vector2(x, y))), { depth, bevelEnabled: false }).translate(0, 0, -depth / 2);
  const arch = new THREE.Shape();
  arch.moveTo(-1, -1).lineTo(-1, 0).absarc(0, 0, 1, Math.PI, 0, true).lineTo(1, -1).lineTo(0.4, -1).lineTo(0.4, 0).absarc(0, 0, 0.4, 0, Math.PI, false).lineTo(-0.4, -1);
  const star = Array.from({ length: 8 }, (_, i) => [(i % 2 ? 0.42 : 1.3) * Math.sin((i * Math.PI) / 4), (i % 2 ? 0.42 : 1.3) * Math.cos((i * Math.PI) / 4)]);
  return {
    square: new THREE.BoxGeometry(1.6, 1.6, 1.6),
    diamond: new THREE.OctahedronGeometry(1.25),
    arch: new THREE.ExtrudeGeometry(arch, { depth: 0.6, bevelEnabled: false, curveSegments: 16 }).translate(0, 0.1, -0.3),
    star: slab(star, 0.5),
    hexagon: new THREE.CylinderGeometry(1.1, 1.1, 0.6, 6).rotateX(-Math.PI / 2),
    pentagon: new THREE.CylinderGeometry(1.1, 1.1, 0.6, 5).rotateX(-Math.PI / 2),
    tag: slab([[-1.1, -0.75], [0.35, -0.75], [1.15, 0], [0.35, 0.75], [-1.1, 0.75]], 0.5),
    ring: new THREE.TorusGeometry(0.78, 0.2, 8, 24),
    linked: new THREE.CylinderGeometry(1.1, 1.1, 0.6, 8).rotateX(-Math.PI / 2).rotateZ(Math.PI / 8),
  };
}

export function create3d(el: HTMLElement, { onSelect, tip }: RendererOptions): Renderer {
  const still = reducedMotion();
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); // throws without WebGL
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0); // the stage's own gradient shows through
  el.append(renderer.domElement);
  const scene = new THREE.Scene();
  const fog = new THREE.FogExp2(0x000000, 0); // far nodes sink into the background: a depth cue (density set per frame)
  scene.fog = fog;
  scene.environmentIntensity = 0.45;
  const camera = new THREE.PerspectiveCamera(40, 1, 1, 5000);
  camera.position.set(0, 55, 160);
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(-2, 3, 3);
  const sky = new THREE.HemisphereLight(0xffffff, 0x000000, 0.9);
  scene.add(key, sky);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = !still;
  // The scene turns slowly until it is first touched.
  controls.autoRotate = !still;
  controls.autoRotateSpeed = 0.35;
  let touched = false; // the camera is the viewer's from the first touch on
  let flight: { t0: number; eye0: THREE.Vector3; eye: THREE.Vector3; target0: THREE.Vector3; target: THREE.Vector3 } | null = null;
  controls.addEventListener("start", () => {
    flight = null; // the viewer's hand takes over from a camera flight
    touched = true;
    controls.autoRotate = false;
  });
  const timer = new THREE.Timer();
  timer.connect(document);

  const world = new THREE.Group(); // rebuilt on every setData
  const pathGroup = new THREE.Group();
  const plinth = new THREE.Group(); // three orange rings under everything
  const circle = new THREE.BufferGeometry().setFromPoints(Array.from({ length: 96 }, (_, i) => new THREE.Vector3(Math.cos((i * Math.PI) / 48), 0, Math.sin((i * Math.PI) / 48))));
  const faint = new THREE.LineBasicMaterial({ color: PATH, transparent: true, opacity: 0.45 });
  for (const r of [0.38, 0.7, 1]) {
    const ring = new THREE.LineLoop(circle, faint);
    ring.scale.setScalar(r);
    plinth.add(ring);
  }
  const halo = new THREE.Mesh(new THREE.TorusGeometry(1, 0.06, 8, 64), new THREE.MeshBasicMaterial({ color: PATH })); // around the selected node
  halo.visible = false;
  scene.add(world, pathGroup, plinth, halo);

  const shapes = solids();
  const matte = new THREE.MeshStandardMaterial({ metalness: 0, roughness: 0.8 });
  const paper = new THREE.Color(); // the background things fade into
  const coinGeometry = new THREE.CylinderGeometry(1, 1, 0.18, 56).rotateX(Math.PI / 2);
  let look: Look | null = null;
  let fonts = { name: "", tag: "", text: "", mono: "", hop: "" };
  let coinMaterials: Partial<Record<Kind, THREE.MeshStandardMaterial[][]>> = {}; // kind -> [bright, faded], each [edge, face, face]

  function coinMaterial(kind: Kind, p: Palette) {
    let made = coinMaterials[kind];
    if (!made) {
      const color = kindColor(kind, p);
      const canvas = Object.assign(document.createElement("canvas"), { width: 256, height: 256 });
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, 256, 256);
        drawGlyph(ctx, kindShape(kind), color, 256, p);
      }
      const face = new THREE.CanvasTexture(canvas);
      face.colorSpace = THREE.SRGBColorSpace;
      face.anisotropy = 4;
      // The caps are mapped for a lying cylinder; turned so the glyph stands upright on both.
      face.center.set(0.5, 0.5);
      face.rotation = -Math.PI / 2;
      // A faded coin is the same coin, mostly seen through.
      made = [1, 1 - FADE].map((opacity) => {
        const finish = { metalness: 0, roughness: 0.7, transparent: opacity < 1, depthWrite: opacity === 1, opacity };
        const edge = new THREE.MeshStandardMaterial({ color: p.foreground, ...finish });
        const cap = new THREE.MeshStandardMaterial({ map: face, ...finish });
        return [edge, cap, cap];
      });
      coinMaterials[kind] = made;
    }
    return made;
  }
  function dropCoinMaterials() {
    for (const pair of Object.values(coinMaterials))
      for (const [edge, cap] of pair) {
        edge?.dispose();
        if (cap) dispose(cap);
      }
    coinMaterials = {};
  }

  // Lines of text on one sprite. `height` > 0 gives it that height in world units, so it shrinks
  // with distance; otherwise it keeps a constant size on screen, times `shrink` on narrow screens.
  // `chain` puts that chain's mark before the last line.
  function textSprite(lines: Text[], p: Palette, height = 0, shrink = 1, chain = "") {
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const pitch = 38;
    const mark = chain ? pitch : 0;
    if (ctx) {
      const widths = lines.map(([text, font, , spacing = 0]) => {
        ctx.font = font;
        ctx.letterSpacing = `${spacing}px`;
        return ctx.measureText(text).width;
      });
      canvas.width = Math.ceil(Math.max(...widths)) + 16 + mark;
      canvas.height = pitch * lines.length + 6;
      ctx.textAlign = "center";
      ctx.lineJoin = "round";
      lines.forEach(([text, font, color, spacing = 0], i) => {
        ctx.font = font;
        ctx.letterSpacing = `${spacing}px`;
        ctx.lineWidth = 6;
        ctx.strokeStyle = p.background;
        ctx.strokeText(text, (canvas.width + mark) / 2 + spacing / 2, pitch * i + 29);
        ctx.fillStyle = color;
        ctx.fillText(text, (canvas.width + mark) / 2 + spacing / 2, pitch * i + 29);
      });
      if (chain) {
        ctx.translate(4, pitch * (lines.length - 1) + 4);
        drawChain(ctx, chain, pitch - 6, p);
      }
    }
    return sprite(canvas, height || (lines.length > 1 ? 0.05 : 0.019) * shrink, height > 0);
  }

  // The number of a hop on the selected path: paper on an ink dot with an orange edge.
  function hopSprite(n: number, p: Palette) {
    const canvas = Object.assign(document.createElement("canvas"), { width: 64, height: 64 });
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.arc(32, 32, 27, 0, 2 * Math.PI);
      ctx.fillStyle = p.foreground;
      ctx.fill();
      ctx.lineWidth = 5;
      ctx.strokeStyle = PATH;
      ctx.stroke();
      ctx.fillStyle = p.cardBg;
      ctx.font = fonts.hop;
      ctx.textAlign = "center";
      ctx.fillText(String(n), 32, 43);
    }
    const s = sprite(canvas, 0.038, false);
    s.center.set(0.5, 0.5);
    return s;
  }

  const positions = new Map<string, THREE.Vector3>(); // address -> place, kept across updates so the picture stays put
  let nodes: Node[] = [];
  let index = new Map<string, number>();
  let segments: [number, number][] = []; // per transfer edge, then per guess
  let w: World | null = null;
  let heat = 0; // > 0 while the layout is still settling
  let faded: Set<number> | null = null; // node indices kept bright while a path is shown
  let born = 0; // when the entrance started; 0 once it is over
  let selected = -1;
  const facing = new THREE.Quaternion(); // the camera rotation the solids were last turned to
  const at = (i: number) => nodes[i]!; // indices come from `index`, so the node is there

  function layoutStep() {
    const n = nodes.length;
    const move = nodes.map(() => new THREE.Vector3());
    const d = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        d.subVectors(at(i).p, at(j).p);
        const dist = Math.max(d.length(), 0.5);
        if (dist > 5 * K) continue; // short-range, or separate clusters push each other out of sight
        d.multiplyScalar((K * K) / (dist * dist)); // repulsion k²/d along the unit vector
        move[i]!.add(d);
        move[j]!.sub(d);
      }
      move[i]!.addScaledVector(at(i).p, -0.05); // gravity pulls separate clusters together
      move[i]!.y -= at(i).p.y * FLATTEN;
    }
    for (const [i, j] of segments) {
      d.subVectors(at(i).p, at(j).p);
      d.multiplyScalar(d.length() / K); // attraction d²/k
      move[i]!.sub(d);
      move[j]!.add(d);
    }
    for (let i = 0; i < n; i++) at(i).p.addScaledVector(move[i]!.clampLength(0, heat), 1);
    heat *= 0.97;
    if (heat < 0.05) heat = 0;
  }

  // Nodes: every solid faces the viewer, every coin turns to the viewer on its own axis.
  function placeNodes(now: number) {
    if (!w || !look) return;
    const m = new THREE.Matrix4();
    const s = new THREE.Vector3();
    const color = new THREE.Color();
    const age = born ? now - born : Infinity;
    const grown = ease(age / REVEAL.rest);
    facing.copy(camera.quaternion);
    for (const { mesh, ids } of w.solids) {
      ids.forEach((i, k) => {
        const node = at(i);
        // Off the shown path a solid shrinks and pales, so it cannot stand in front of the line.
        const off = faded !== null && !faded.has(i);
        mesh.setMatrixAt(k, m.compose(node.p, facing, s.setScalar(node.size * grown * (off ? 0.45 : 1))));
        mesh.setColorAt(k, color.set(node.color).lerp(paper, off ? FADE : 1 - node.dim));
      });
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.computeBoundingSphere();
    }
    nodes.forEach((node, i) => w?.hit.setMatrixAt(i, m.makeTranslation(node.p).scale(s.setScalar(node.size * 1.15))));
    w.hit.instanceMatrix.needsUpdate = true;
    w.hit.computeBoundingSphere();
    const p = look.palette;
    w.coins.forEach(({ coin, i, kind }, k) => {
      // Struck one after another: each coin spins up from its edge.
      const t = ease((age - k * REVEAL.stagger) / REVEAL.coin);
      const node = at(i);
      coin.position.copy(node.p);
      coin.scale.setScalar(COIN * t);
      coin.rotation.y = Math.atan2(camera.position.x - node.p.x, camera.position.z - node.p.z) + (1 - t) * 2.5 * Math.PI;
      coin.material = coinMaterial(kind, p)[faded && !faded.has(i) ? 1 : 0]!;
    });
    for (const [label, i] of w.nodeLabels) label.position.copy(at(i).p).add({ x: 0, y: at(i).size + 0.8, z: 0 });
    if (selected >= 0) halo.position.copy(at(selected).p);
    halo.quaternion.copy(facing);
    if (born && age > REVEAL.rest && age > REVEAL.coin + w.coins.length * REVEAL.stagger) born = 0;
  }

  function placeLines() {
    if (!w) return;
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const s = new THREE.Vector3();
    const dir = new THREE.Vector3();
    for (const { obj, pairs } of [w.edgeLines, w.stems, ...w.guessLines]) {
      const pos = obj.geometry.getAttribute("position") as THREE.BufferAttribute;
      pairs.forEach(([i, j], k) => {
        const a = at(i);
        if (j === undefined) {
          // A stem drops from a coin's rim to the plinth.
          pos.setXYZ(2 * k, a.p.x, a.p.y - a.size, a.p.z);
          pos.setXYZ(2 * k + 1, a.p.x, plinth.position.y, a.p.z);
          return;
        }
        // Any other line runs from rim to rim, so a busy wallet's coin is not buried in lines.
        const b = at(j);
        dir.subVectors(b.p, a.p);
        const reach = dir.length() > a.size + b.size ? 1 : 0;
        dir.normalize();
        pos.setXYZ(2 * k, a.p.x + dir.x * a.size * reach, a.p.y + dir.y * a.size * reach, a.p.z + dir.z * a.size * reach);
        pos.setXYZ(2 * k + 1, b.p.x - dir.x * b.size * reach, b.p.y - dir.y * b.size * reach, b.p.z - dir.z * b.size * reach);
      });
      pos.needsUpdate = true;
      obj.geometry.computeBoundingSphere();
    }
    const up = new THREE.Vector3(0, 1, 0);
    const one = new THREE.Vector3(1, 1, 1);
    w.edgeLines.pairs.forEach(([i, j = i], k) => {
      // The arrowhead sits just outside the receiving node, pointing at it.
      dir.subVectors(at(j).p, at(i).p).normalize();
      m.compose(s.copy(at(j).p).addScaledVector(dir, -at(j).size - 0.9), q.setFromUnitVectors(up, dir), one);
      w?.arrows.setMatrixAt(k, m);
    });
    w.arrows.instanceMatrix.needsUpdate = true;
    w.arrows.computeBoundingSphere();
    for (const { obj } of w.guessLines) obj.computeLineDistances();
    for (const [label, i, j] of w.linkLabels) label.position.lerpVectors(at(i).p, at(j).p, 0.5);
  }

  // The plinth lies under the lowest node and spans the whole graph.
  function ground() {
    if (!nodes.length) return;
    const box = new THREE.Box3().setFromPoints(nodes.map((node) => node.p));
    const center = box.getCenter(new THREE.Vector3());
    plinth.position.set(center.x, box.min.y - 6, center.z);
    plinth.scale.setScalar(Math.max(30, Math.hypot(box.max.x - box.min.x, box.max.z - box.min.z) * 0.62));
  }

  function place() {
    ground();
    placeNodes(performance.now());
    placeLines();
  }

  // Every camera move goes through here. Unless `instant` (or reduced motion), the camera
  // flies: position and target ease to the new framing over FLIGHT ms, see frame().
  function fit(points: THREE.Vector3[], instant = false) {
    if (!points.length) return;
    const center = points.reduce((sum, p) => sum.add(p), new THREE.Vector3()).divideScalar(points.length);
    const radius = Math.max(15, ...points.map((p) => p.distanceTo(center)));
    const dir = camera.position.clone().sub(controls.target).normalize();
    const eye = center.clone().addScaledVector(dir, (radius * 2.7) / Math.min(camera.aspect, 1)); // a narrow screen stands further back
    if (instant || still) {
      flight = null;
      controls.target.copy(center);
      camera.position.copy(eye);
      if (instant) controls.update();
      return;
    }
    flight = { t0: performance.now(), eye0: camera.position.clone(), eye, target0: controls.target.clone(), target: center };
  }

  function fly(now: number) {
    if (!flight) return;
    const t = Math.min((now - flight.t0) / FLIGHT, 1);
    const k = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2; // ease in and out
    camera.position.lerpVectors(flight.eye0, flight.eye, k);
    controls.target.lerpVectors(flight.target0, flight.target, k);
    if (t === 1) flight = null;
  }

  // The path draws itself hop by hop: each hop's parts appear HOP ms after the previous one.
  let traced = 0;
  function trace(now: number) {
    let pending = false;
    for (const part of pathGroup.children) {
      const hop = part.userData.hop as number | undefined;
      const due = !hop || now - traced >= (hop - 1) * HOP;
      part.visible = due;
      pending ||= !due;
    }
    if (!pending) traced = 0;
  }

  function select(i: number | undefined) {
    selected = i ?? -1;
    halo.visible = selected >= 0;
    if (selected >= 0) halo.scale.setScalar(at(selected).size + 1.3);
  }

  function setData(graph: Graph, { minConf }: { minConf: number }) {
    if (!look) return;
    const { palette: p, t } = look;
    clear(world);
    const old = index;
    const chosen = selected >= 0 ? at(selected).n.address : undefined;
    nodes = graph.nodes.map((n) => {
      const kind = kindOf(n);
      const color = kindColor(kind, p);
      const more = n.folded ? ` · +${n.folded}` : ""; // counterparties folded into it
      const tag = (n.public ? t("tracker:badge.public") : t("tracker:badge.private")).toLocaleUpperCase();
      return {
        n,
        color,
        shape: kindShape(kind),
        size: n.mine ? COIN : n.category ? 2.6 : 0.9,
        dim: !n.mine && !n.synced_at && !n.category ? 0.55 : 1, // not expanded yet
        label: n.mine
          ? [[tag, fonts.tag, n.public ? p.bitcoinText : color, 3], [title(n).toLocaleUpperCase() + more, fonts.name, p.foreground, 0]]
          : n.category || n.folded != null
            ? [[`${title(n)}${n.category ? ` · ${typeOf(n, t)}` : ""}${more}`, fonts.text, p.muted]]
            : null,
        p: new THREE.Vector3(),
      };
    });
    index = new Map(nodes.map((node, i) => [node.n.address, i]));
    select(chosen === undefined ? undefined : index.get(chosen));
    const ends = (a: string, b: string): [number, number] | null => {
      const i = index.get(a);
      const j = index.get(b);
      return i === undefined || j === undefined ? null : [i, j];
    };
    const edges = graph.edges.flatMap((e) => {
      const pair = ends(e.src, e.dst);
      return pair ? [pair] : [];
    });
    const guesses = graph.links.filter((k) => k.confidence >= minConf && ends(k.a, k.b));
    const guessPairs = guesses.map((k) => ends(k.a, k.b)!);
    segments = [...edges, ...guessPairs];

    // New nodes start next to a neighbour that already has a place.
    const near = new Map<number, number>();
    for (const [i, j] of segments) {
      near.set(i, j);
      near.set(j, i);
    }
    let grew = false;
    nodes.forEach((node, i) => {
      const kept = positions.get(node.n.address);
      if (kept) {
        node.p = kept;
        return;
      }
      grew = true;
      const k = near.get(i);
      const anchor = k === undefined ? undefined : positions.get(at(k).n.address);
      node.p.randomDirection().multiplyScalar(anchor ? K : 60 * Math.random());
      if (anchor) node.p.add(anchor);
      positions.set(node.n.address, node.p);
    });

    // Picking uses one unseen sphere per node, so a ring is as easy to hit as a cube.
    const hit = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 10, 8), new THREE.MeshBasicMaterial({ visible: false }), Math.max(nodes.length, 1));
    hit.count = nodes.length;
    world.add(hit);

    const solidMeshes = Object.entries(shapes).flatMap(([shape, geometry]) => {
      const ids = nodes.flatMap((node, i) => (!node.n.mine && node.shape === shape ? [i] : []));
      if (!ids.length) return [];
      const mesh = new THREE.InstancedMesh(geometry, matte, ids.length);
      mesh.userData.shared = true;
      mesh.setColorAt(0, new THREE.Color());
      return [{ mesh, ids }];
    });
    const coins = nodes.flatMap((node, i) => {
      if (!node.n.mine) return [];
      const kind = kindOf(node.n);
      const coin = new THREE.Mesh<THREE.BufferGeometry, THREE.Material[]>(coinGeometry, coinMaterial(kind, p)[0]!);
      coin.userData.shared = true;
      return [{ coin, i, kind }];
    });
    // add() without arguments complains: a graph may have no solids, or no labels.
    if (solidMeshes.length + coins.length) world.add(...solidMeshes.map((x) => x.mesh), ...coins.map((x) => x.coin));

    const line = (pairs: Pair[], colors: number[] | null, material: THREE.LineBasicMaterial | THREE.LineDashedMaterial): Lines => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pairs.length * 6), 3));
      if (colors) geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(colors), 3));
      const obj = new THREE.LineSegments(geometry, material);
      world.add(obj);
      return { obj, pairs };
    };
    // Each transfer line runs faint at the sender and strong at the receiver, and ends in an arrowhead.
    const c = new THREE.Color();
    const gradient = [p.cardBorder, p.foreground].flatMap((hex) => c.set(hex).toArray());
    const edgeLines = line(
      edges,
      edges.flatMap(() => gradient),
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: LINE }),
    );
    const arrows = new THREE.InstancedMesh(
      new THREE.ConeGeometry(0.4, 1.5, 8),
      new THREE.MeshBasicMaterial({ color: p.foreground, transparent: true, opacity: LINE }),
      Math.max(edges.length, 1),
    );
    arrows.count = edges.length;
    world.add(arrows);
    const stems = line(coins.map(({ i }) => [i]), null, new THREE.LineBasicMaterial({ color: p.foreground, transparent: true, opacity: 0.2 }));
    // One set of dashed lines per kind of guess: each kind has its own colour and dash.
    const guessLines = [...new Set(guesses.map((k) => k.kind))].map((kind) => {
      const [dash = 4, gap = 4] = linkDash(kind);
      const own = guesses.flatMap((k, i) => (k.kind === kind ? [[k, guessPairs[i]!] as const] : []));
      return line(
        own.map((x) => x[1]),
        // Confidence shows as strength of colour, and as the number on the label.
        own.flatMap(([k]) => {
          c.set(linkColor(kind, p)).lerp(paper, 0.6 * (1 - k.confidence));
          return [c.r, c.g, c.b, c.r, c.g, c.b];
        }),
        new THREE.LineDashedMaterial({ vertexColors: true, dashSize: dash * 0.28, gapSize: gap * 0.28, transparent: true }),
      );
    });

    // shortcut: labels for the 60 strongest guesses only, a sprite each; draw them in one
    // texture atlas if graphs with thousands of guesses need all of them.
    const linkLabels = guesses
      .map((k, i) => [k, i] as const)
      .sort((a, b) => b[0].confidence - a[0].confidence)
      .slice(0, 60)
      .map(([k, i]): [THREE.Sprite, number, number] => [textSprite([[`${k.kind} ${pct(k.confidence)}`, fonts.mono, linkColor(k.kind, p)]], p, 1.6), ...guessPairs[i]!]);
    const shrink = Math.min(1, Math.max(0.6, el.clientWidth / 900));
    const nodeLabels = nodes.flatMap((node, i): [THREE.Sprite, number][] => (node.label ? [[textSprite(node.label, p, 0, shrink, chainOf(node.n)), i]] : []));
    if (linkLabels.length + nodeLabels.length) world.add(...[...linkLabels, ...nodeLabels].map((x) => x[0]));
    w = { hit, solids: solidMeshes, coins, edgeLines, stems, guessLines, arrows, linkLabels, nodeLabels };

    if (grew) {
      heat = 6;
      // Without motion the layout settles before the first frame; otherwise it settles on screen.
      if (still || (old.size === 0 && nodes.length <= 400)) while (heat) layoutStep();
      if (!still && old.size === 0) born = performance.now();
    }
    highlight([]);
    if (grew && nodes.length) fit(nodes.map((node) => node.p), true);
  }

  function highlight(paths: string[][]) {
    clear(pathGroup);
    if (!w || !look) return;
    const p = look.palette;
    const runs = paths.map((path) => path.map((a) => index.get(a)).filter((i): i is number => i !== undefined));
    const ids = runs.flat();
    faded = ids.length > 1 ? new Set(ids) : null;
    w.edgeLines.obj.material.opacity = faded ? 0.08 : LINE;
    (w.arrows.material as THREE.MeshBasicMaterial).opacity = faded ? 0.08 : LINE;
    for (const { obj } of w.guessLines) obj.material.opacity = faded ? 0.12 : 1;
    w.stems.obj.material.opacity = faded ? 0.05 : 0.2;
    for (const [label, i, j] of w.linkLabels) label.visible = !faded || (faded.has(i) && faded.has(j));
    if (faded) {
      const material = new THREE.MeshBasicMaterial({ color: PATH });
      const up = new THREE.Vector3(0, 1, 0);
      const dir = new THREE.Vector3();
      for (const run of runs) run.slice(1).forEach((id, k) => {
        // A fat orange bar from one node's rim to the next, an arrowhead at its end, its number halfway.
        // Every part carries userData.hop (1, 2, ...): trace() reveals them in that order.
        const from = at(run[k]!);
        const to = at(id);
        dir.subVectors(to.p, from.p).normalize();
        const a = from.p.clone().addScaledVector(dir, from.size + 0.4);
        const tipAt = to.p.clone().addScaledVector(dir, -to.size - 0.4);
        const b = tipAt.clone().addScaledVector(dir, -2.4);
        const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.75, Math.max(a.distanceTo(b), 0.1), 12), material);
        bar.position.lerpVectors(a, b, 0.5);
        const head = new THREE.Mesh(new THREE.ConeGeometry(1.7, 2.4, 16), material);
        head.position.lerpVectors(b, tipAt, 0.5);
        bar.quaternion.setFromUnitVectors(up, dir);
        head.quaternion.copy(bar.quaternion);
        const parts: THREE.Object3D[] = [bar, head];
        if (runs.length === 1) {
          // Several paths (a group's links) have no one order to number.
          const number = hopSprite(k + 1, p);
          number.position.lerpVectors(a, b, 0.5);
          parts.push(number);
        }
        for (const part of parts) part.userData.hop = k + 1;
        pathGroup.add(...parts);
      });
      for (const i of faded) {
        if (at(i).label) continue; // strangers on the path get a name while it is shown
        const label = textSprite([[title(at(i).n), fonts.text, p.foreground]], p);
        label.position.copy(at(i).p).add({ x: 0, y: at(i).size + 0.8, z: 0 });
        pathGroup.add(label);
      }
      heat = 0; // the bars are placed once, so the layout must not move under them
      fit(ids.map((i) => at(i).p));
      if (!still) {
        traced = performance.now();
        trace(traced);
      }
    }
    place();
  }

  function focus(address: string) {
    const i = index.get(address);
    if (i === undefined) return;
    touched = true; // the camera was sent somewhere: it stops following the layout
    select(i);
    fit([at(i).p, ...segments.filter((s) => s.includes(i)).map(([a, b]) => at(a === i ? b : a).p)]);
  }

  // Picking: a click that is not the end of a drag selects the node under the pointer.
  const raycaster = new THREE.Raycaster();
  const pick = (event: PointerEvent) => {
    if (!w) return undefined;
    const rect = renderer.domElement.getBoundingClientRect();
    raycaster.setFromCamera(new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1), camera);
    return raycaster.intersectObject(w.hit)[0]?.instanceId;
  };
  let down: [number, number] | null = null;
  renderer.domElement.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 6) {
      const i = pick(e);
      select(i);
      onSelect(i === undefined ? null : at(i).n);
    }
    down = null;
  });
  renderer.domElement.addEventListener("pointermove", (e) => {
    const i = e.buttons ? undefined : pick(e);
    const node = i === undefined ? undefined : at(i);
    renderer.domElement.style.cursor = node ? "pointer" : "grab";
    tip.hidden = !node;
    if (node && look) {
      tip.textContent = [title(node.n), typeOf(node.n, look.t), chainLabel(chainOf(node.n))].filter(Boolean).join(" · ");
      const box = el.getBoundingClientRect(); // the tip is placed within the graph's box
      tip.style.left = `${e.clientX - box.left + 14}px`;
      tip.style.top = `${e.clientY - box.top + 14}px`;
    }
  });
  renderer.domElement.addEventListener("pointerleave", () => (tip.hidden = true));

  const resize = new ResizeObserver(() => {
    const { clientWidth: width, clientHeight: height } = el;
    if (!width || !height) return;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
  });
  resize.observe(el);

  function frame() {
    timer.update();
    const now = performance.now();
    fly(now);
    if (traced) trace(now);
    if (heat) for (let i = 0; i < (nodes.length > 400 ? 1 : 3) && heat; i++) layoutStep();
    controls.update(timer.getDelta());
    if (heat) {
      // Until the camera is touched it keeps the whole graph in view while the layout spreads.
      if (!touched && !faded) fit(nodes.map((node) => node.p), true);
      place();
    } else if (nodes.length && (born || !facing.equals(camera.quaternion))) placeNodes(now);
    // From afar only my wallets are named; counterparties get their names as the camera nears.
    for (const [label, i] of w?.nodeLabels ?? []) label.visible = faded ? faded.has(i) : Boolean(at(i).n.mine) || camera.position.distanceTo(at(i).p) < NEAR;
    fog.density = 0.32 / camera.position.distanceTo(controls.target); // same fade at any zoom
    renderer.render(scene, camera);
  }

  return {
    setLook(next) {
      look = next;
      const p = next.palette;
      const { sans, mono } = canvasFonts();
      fonts = { name: `700 30px ${sans}`, tag: `500 18px ${mono}`, text: `500 26px ${sans}`, mono: `400 24px ${mono}`, hop: `500 30px ${mono}` };
      paper.set(p.background);
      fog.color.set(p.background);
      sky.groundColor.set(p.cardBg);
      scene.environment?.dispose();
      scene.environment = environment(renderer, p);
      dropCoinMaterials();
    },
    setData,
    highlight,
    focus,
    setActive(on) {
      if (on && renderer.domElement.hidden) fadeIn(renderer.domElement);
      renderer.domElement.hidden = !on;
      renderer.setAnimationLoop(on ? frame : null);
    },
    destroy() {
      renderer.setAnimationLoop(null);
      resize.disconnect();
      controls.dispose();
      timer.dispose();
      clear(world);
      clear(pathGroup);
      dropCoinMaterials();
      for (const geometry of [...Object.values(shapes), coinGeometry, circle, halo.geometry]) geometry.dispose();
      for (const material of [matte, faint, halo.material]) material.dispose();
      scene.environment?.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      tip.hidden = true;
    },
  };
}
