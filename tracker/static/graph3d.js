// 3D wallet graph. Same interface as graph2d.js: setData, highlight, focus, setActive.
// My wallets are coins standing over a faint plinth; every other address is a small solid
// whose silhouette is its glyph in kinds.js.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { KINDS, PATH, STONE, drawGlyph, kindOf, linkColor, linkDash, pct, title } from "./kinds.js";

const K = 14; // ideal distance between linked nodes, world units
const FLATTEN = 0.12; // extra pull towards the plinth's plane, so the graph reads as a table of coins
const LINE = 0.3; // opacity of plain transfer lines: many of them cross
const COIN = 6;
const NEAR = 170; // counterparties are named within this distance of the camera, world units // radius of a wallet's coin, world units
const REVEAL = { coin: 900, stagger: 110, rest: 700 }; // the entrance, in milliseconds
const FONT = { name: "600 30px Cinzel, serif", tag: "500 19px Jost, sans-serif", text: "500 26px Jost, sans-serif", mono: "400 24px 'JetBrains Mono', monospace" };
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const ease = (t) => 1 - (1 - Math.min(Math.max(t, 0), 1)) ** 3;

// Lines of text on one sprite. `world` > 0 gives it that height in world units, so it shrinks
// with distance; otherwise it keeps a constant size on screen, times `shrink` on narrow screens.
function textSprite(lines, world = 0, shrink = 1) {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  const pitch = 38;
  const widths = lines.map(([text, font, , spacing = 0]) => {
    ctx.font = font;
    ctx.letterSpacing = `${spacing}px`;
    return ctx.measureText(text).width;
  });
  canvas.width = Math.ceil(Math.max(...widths)) + 16;
  canvas.height = pitch * lines.length + 6;
  ctx.textAlign = "center";
  ctx.lineJoin = "round";
  lines.forEach(([text, font, color, spacing = 0], i) => {
    ctx.font = font;
    ctx.letterSpacing = `${spacing}px`;
    ctx.lineWidth = 6;
    ctx.strokeStyle = STONE.stone;
    ctx.strokeText(text, canvas.width / 2 + spacing / 2, pitch * i + 29);
    ctx.fillStyle = color;
    ctx.fillText(text, canvas.width / 2 + spacing / 2, pitch * i + 29);
  });
  return sprite(canvas, world || (lines.length > 1 ? 0.05 : 0.019) * shrink, world > 0);
}

// The number of a hop on the selected path, in a ring.
function hopSprite(n) {
  const canvas = Object.assign(document.createElement("canvas"), { width: 64, height: 64 });
  const ctx = canvas.getContext("2d");
  ctx.arc(32, 32, 27, 0, 2 * Math.PI);
  ctx.fillStyle = STONE.stone;
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = ctx.fillStyle = PATH;
  ctx.stroke();
  ctx.font = "400 30px 'JetBrains Mono', monospace";
  ctx.textAlign = "center";
  ctx.fillText(String(n), 32, 43);
  const s = sprite(canvas, 0.034, false);
  s.center.set(0.5, 0.5);
  return s;
}

function sprite(canvas, height, attenuate) {
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  // Drawn over the scene: a label must stay readable.
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, sizeAttenuation: attenuate, depthTest: false, transparent: true }));
  s.scale.set((canvas.width / canvas.height) * height, height, 1);
  s.center.set(0.5, 0); // it stands on its anchor, so it never covers what it names
  s.renderOrder = 2;
  return s;
}

function clear(group) {
  for (const child of [...group.children]) {
    group.remove(child);
    if (child.isInstancedMesh) child.dispose();
    if (child.userData.shared) continue; // its geometry and materials outlive a rebuild
    child.geometry?.dispose();
    for (const material of [child.material].flat()) {
      material?.map?.dispose();
      material?.dispose();
    }
  }
}

// What the metals reflect: a pale sky over dark stone, and one bright window.
function environment(renderer) {
  const canvas = Object.assign(document.createElement("canvas"), { width: 4, height: 128 });
  const ctx = canvas.getContext("2d");
  const sky = ctx.createLinearGradient(0, 0, 0, 128);
  [[0, "#f4f4f4"], [0.42, "#7a7a7a"], [0.56, "#2a2a2a"], [1, "#0e0e0e"]].forEach(([at, color]) => sky.addColorStop(at, color));
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, 4, 128);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const room = new THREE.Scene();
  const dome = new THREE.Mesh(new THREE.SphereGeometry(50, 32, 16), new THREE.MeshBasicMaterial({ map: texture, side: THREE.BackSide }));
  const window = new THREE.Mesh(new THREE.PlaneGeometry(26, 26), new THREE.MeshBasicMaterial({ color: new THREE.Color(5, 5, 5) }));
  window.position.set(-24, 30, 26);
  window.lookAt(0, 0, 0);
  room.add(dome, window);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const map = pmrem.fromScene(room, 0.03).texture;
  pmrem.dispose();
  for (const mesh of [dome, window]) {
    mesh.geometry.dispose();
    mesh.material.dispose();
  }
  texture.dispose();
  return map;
}

// One solid per glyph, about one unit in radius, modelled facing +z: it is turned to the viewer.
function solids() {
  const slab = (points, depth) => new THREE.ExtrudeGeometry(new THREE.Shape(points.map(([x, y]) => new THREE.Vector2(x, y))), { depth, bevelEnabled: false }).translate(0, 0, -depth / 2);
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
  };
}

export function create3d(el, { onSelect }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); // throws without WebGL
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0); // the stage's own gradient shows through
  el.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x161616, 0); // far nodes sink into the mist: a depth cue (density set per frame)
  scene.environment = environment(renderer);
  scene.environmentIntensity = 0.9;
  const camera = new THREE.PerspectiveCamera(40, 1, 1, 5000);
  camera.position.set(0, 55, 160);
  const key = new THREE.DirectionalLight(0xffffff, 1.7);
  key.position.set(-2, 3, 3);
  scene.add(key, new THREE.HemisphereLight(0xffffff, 0x1a1a1a, 0.9));
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = !still;
  // The scene turns slowly until it is first touched.
  controls.autoRotate = !still;
  controls.autoRotateSpeed = 0.35;
  let touched = false; // the camera is the viewer's from the first touch on
  controls.addEventListener("start", () => {
    touched = true;
    controls.autoRotate = false;
  });
  const timer = new THREE.Timer();
  timer.connect(document);

  const world = new THREE.Group(); // rebuilt on every setData
  const pathGroup = new THREE.Group();
  const plinth = new THREE.Group(); // three faint rings under everything
  const circle = new THREE.BufferGeometry().setFromPoints(Array.from({ length: 96 }, (_, i) => new THREE.Vector3(Math.cos((i * Math.PI) / 48), 0, Math.sin((i * Math.PI) / 48))));
  const faint = new THREE.LineBasicMaterial({ color: STONE.ink, transparent: true, opacity: 0.1 });
  for (const r of [0.38, 0.7, 1]) {
    const ring = new THREE.LineLoop(circle, faint);
    ring.scale.setScalar(r);
    plinth.add(ring);
  }
  const halo = new THREE.Mesh(new THREE.TorusGeometry(1, 0.035, 8, 64), new THREE.MeshBasicMaterial({ color: STONE.ink })); // around the selected node
  halo.visible = false;
  scene.add(world, pathGroup, plinth, halo);

  const shapes = solids();
  const mineral = new THREE.MeshStandardMaterial({ metalness: 0.25, roughness: 0.45 });
  const coinGeometry = new THREE.CylinderGeometry(1, 1, 0.18, 56).rotateX(Math.PI / 2);
  const coinMaterials = {}; // kind -> [bright, faded], each [edge, face, face]
  function coinMaterial(kind) {
    if (!coinMaterials[kind]) {
      const [color, shape] = KINDS[kind];
      const canvas = Object.assign(document.createElement("canvas"), { width: 256, height: 256 });
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 256, 256);
      drawGlyph(ctx, shape, color, 256);
      const face = new THREE.CanvasTexture(canvas);
      face.colorSpace = THREE.SRGBColorSpace;
      face.anisotropy = 4;
      // The caps are mapped for a lying cylinder; turned so the glyph stands upright on both.
      face.center.set(0.5, 0.5);
      face.rotation = -Math.PI / 2;
      coinMaterials[kind] = [1, 0.12].map((light) => {
        const edge = new THREE.MeshStandardMaterial({ color: new THREE.Color(color).multiplyScalar(light), metalness: 0.85, roughness: 0.3 });
        const cap = new THREE.MeshStandardMaterial({ map: face, color: new THREE.Color().setScalar(light), metalness: 0.6, roughness: 0.38 });
        return [edge, cap, cap];
      });
    }
    return coinMaterials[kind];
  }

  const positions = new Map(); // address -> Vector3, kept across updates so the picture stays put
  let nodes = [];
  let index = new Map();
  let segments = []; // [i, j] per transfer edge, then per guess
  let hit, solidMeshes, coins, edgeLines, guessLines, arrows, stems, linkLabels, nodeLabels;
  let heat = 0; // > 0 while the layout is still settling
  let faded = null; // Set of node indices kept bright while a path is shown
  let born = 0; // when the entrance started; 0 once it is over
  let selected = -1;
  const facing = new THREE.Quaternion(); // the camera rotation the solids were last turned to

  function layoutStep() {
    const n = nodes.length;
    const move = nodes.map(() => new THREE.Vector3());
    const d = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        d.subVectors(nodes[i].p, nodes[j].p);
        const dist = Math.max(d.length(), 0.5);
        if (dist > 5 * K) continue; // short-range, or separate clusters push each other out of sight
        d.multiplyScalar((K * K) / (dist * dist)); // repulsion k²/d along the unit vector
        move[i].add(d);
        move[j].sub(d);
      }
      move[i].addScaledVector(nodes[i].p, -0.05); // gravity pulls separate clusters together
      move[i].y -= nodes[i].p.y * FLATTEN;
    }
    for (const [i, j] of segments) {
      d.subVectors(nodes[i].p, nodes[j].p);
      d.multiplyScalar(d.length() / K); // attraction d²/k
      move[i].sub(d);
      move[j].add(d);
    }
    for (let i = 0; i < n; i++) nodes[i].p.addScaledVector(move[i].clampLength(0, heat), 1);
    heat *= 0.97;
    if (heat < 0.05) heat = 0;
  }

  // Nodes: every solid faces the viewer, every coin turns to the viewer on its own axis.
  function placeNodes(now) {
    const m = new THREE.Matrix4();
    const s = new THREE.Vector3();
    const color = new THREE.Color();
    const age = born ? now - born : Infinity;
    const grown = ease(age / REVEAL.rest);
    facing.copy(camera.quaternion);
    for (const mesh of solidMeshes) {
      mesh.userData.ids.forEach((i, k) => {
        const node = nodes[i];
        mesh.setMatrixAt(k, m.compose(node.p, facing, s.setScalar(node.size * grown)));
        mesh.setColorAt(k, color.set(node.color).multiplyScalar(faded && !faded.has(i) ? 0.12 : node.dim));
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.instanceColor.needsUpdate = true;
      mesh.computeBoundingSphere();
    }
    nodes.forEach((node, i) => hit.setMatrixAt(i, m.makeTranslation(node.p).scale(s.setScalar(node.size * 1.15))));
    hit.instanceMatrix.needsUpdate = true;
    hit.computeBoundingSphere();
    coins.forEach(([coin, i], k) => {
      // Struck one after another: each coin spins up from its edge.
      const t = ease((age - k * REVEAL.stagger) / REVEAL.coin);
      coin.position.copy(nodes[i].p);
      coin.scale.setScalar(COIN * t);
      coin.rotation.y = Math.atan2(camera.position.x - nodes[i].p.x, camera.position.z - nodes[i].p.z) + (1 - t) * 2.5 * Math.PI;
      coin.material = coinMaterial(coin.userData.kind)[faded && !faded.has(i) ? 1 : 0];
    });
    nodeLabels.forEach(([label, i]) => label.position.copy(nodes[i].p).add({ x: 0, y: nodes[i].size + 0.8, z: 0 }));
    if (selected >= 0) halo.position.copy(nodes[selected].p);
    halo.quaternion.copy(facing);
    if (born && age > REVEAL.rest && age > REVEAL.coin + coins.length * REVEAL.stagger) born = 0;
  }

  function placeLines() {
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const s = new THREE.Vector3();
    const dir = new THREE.Vector3();
    for (const lines of [edgeLines, stems, ...guessLines]) {
      const pos = lines.geometry.attributes.position;
      lines.userData.pairs.forEach(([i, j], k) => {
        const a = nodes[i];
        const b = nodes[j];
        if (!b) {
          // A stem drops from a coin's rim to the plinth.
          pos.setXYZ(2 * k, a.p.x, a.p.y - a.size, a.p.z);
          pos.setXYZ(2 * k + 1, a.p.x, plinth.position.y, a.p.z);
          return;
        }
        // Any other line runs from rim to rim, so a busy wallet's coin is not buried in lines.
        dir.subVectors(b.p, a.p);
        const reach = dir.length() > a.size + b.size ? 1 : 0;
        dir.normalize();
        pos.setXYZ(2 * k, a.p.x + dir.x * a.size * reach, a.p.y + dir.y * a.size * reach, a.p.z + dir.z * a.size * reach);
        pos.setXYZ(2 * k + 1, b.p.x - dir.x * b.size * reach, b.p.y - dir.y * b.size * reach, b.p.z - dir.z * b.size * reach);
      });
      pos.needsUpdate = true;
      lines.geometry.computeBoundingSphere();
    }
    const up = new THREE.Vector3(0, 1, 0);
    edgeLines.userData.pairs.forEach(([i, j], k) => {
      // The arrowhead sits just outside the receiving node, pointing at it.
      dir.subVectors(nodes[j].p, nodes[i].p).normalize();
      m.compose(s.copy(nodes[j].p).addScaledVector(dir, -nodes[j].size - 0.9), q.setFromUnitVectors(up, dir), new THREE.Vector3(1, 1, 1));
      arrows.setMatrixAt(k, m);
    });
    arrows.instanceMatrix.needsUpdate = true;
    arrows.computeBoundingSphere();
    for (const lines of guessLines) lines.computeLineDistances();
    linkLabels.forEach(([label, i, j]) => label.position.lerpVectors(nodes[i].p, nodes[j].p, 0.5));
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
  const FLIGHT = 700;
  let flight = null;
  function fit(points, instant) {
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

  function fly(now) {
    const t = Math.min((now - flight.t0) / FLIGHT, 1);
    const k = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2; // ease in and out
    camera.position.lerpVectors(flight.eye0, flight.eye, k);
    controls.target.lerpVectors(flight.target0, flight.target, k);
    if (t === 1) flight = null;
  }

  // The path draws itself hop by hop: each hop's parts appear HOP ms after the previous one.
  const HOP = 320;
  let traced = 0;
  function trace(now) {
    let pending = false;
    for (const part of pathGroup.children) {
      const due = !part.userData.hop || now - traced >= (part.userData.hop - 1) * HOP;
      part.visible = due;
      pending ||= !due;
    }
    if (!pending) traced = 0;
  }

  function select(i) {
    selected = i ?? -1;
    halo.visible = selected >= 0;
    if (selected >= 0) halo.scale.setScalar(nodes[selected].size + 1.3);
  }

  function setData(graph, { minConf }) {
    clear(world);
    const old = index;
    const chosen = nodes[selected]?.n.address;
    nodes = graph.nodes.map((n) => {
      const [color, shape, word] = KINDS[kindOf(n)];
      return {
        n,
        color,
        shape,
        size: n.mine ? COIN : n.category ? 2.6 : 0.9,
        dim: !n.mine && !n.synced_at && !n.category ? 0.55 : 1, // not expanded yet
        label: n.mine
          ? [[n.public ? "PUBLIC" : "PRIVATE", FONT.tag, color, 3], [title(n).toUpperCase(), FONT.name, STONE.ink, 2]]
          : n.category
            ? [[`${title(n)} · ${word}`, FONT.text, STONE.quiet]]
            : null,
      };
    });
    index = new Map(nodes.map((node, i) => [node.n.address, i]));
    select(index.get(chosen));
    const edges = graph.edges.map((e) => [index.get(e.src), index.get(e.dst)]);
    const guesses = graph.links.filter((k) => k.confidence >= minConf);
    const guessPairs = guesses.map((k) => [index.get(k.a), index.get(k.b)]);
    segments = [...edges, ...guessPairs];

    // New nodes start next to a neighbour that already has a place.
    const near = new Map();
    for (const [i, j] of segments) {
      near.set(i, j);
      near.set(j, i);
    }
    let grew = false;
    nodes.forEach((node, i) => {
      node.p = positions.get(node.n.address);
      if (node.p) return;
      grew = true;
      const anchor = positions.get(nodes[near.get(i)]?.n.address);
      node.p = new THREE.Vector3().randomDirection().multiplyScalar(anchor ? K : 60 * Math.random());
      if (anchor) node.p.add(anchor);
      positions.set(node.n.address, node.p);
    });

    // Picking uses one unseen sphere per node, so a ring is as easy to hit as a cube.
    hit = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 10, 8), new THREE.MeshBasicMaterial({ visible: false }), Math.max(nodes.length, 1));
    hit.count = nodes.length;
    world.add(hit);

    solidMeshes = Object.entries(shapes).flatMap(([shape, geometry]) => {
      const ids = nodes.flatMap((node, i) => (!node.n.mine && node.shape === shape ? [i] : []));
      if (!ids.length) return [];
      const mesh = new THREE.InstancedMesh(geometry, mineral, ids.length);
      mesh.userData = { ids, shared: true };
      mesh.setColorAt(0, new THREE.Color());
      return [mesh];
    });
    coins = nodes.flatMap((node, i) => {
      if (!node.n.mine) return [];
      const coin = new THREE.Mesh(coinGeometry, coinMaterial(kindOf(node.n))[0]);
      coin.userData = { kind: kindOf(node.n), shared: true };
      return [[coin, i]];
    });
    world.add(...solidMeshes, ...coins.map((x) => x[0]));

    const line = (pairs, colors, material) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pairs.length * 6), 3));
      if (colors) geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(colors), 3));
      const lines = new THREE.LineSegments(geometry, material);
      lines.userData.pairs = pairs;
      world.add(lines);
      return lines;
    };
    // Each transfer line runs dark at the sender and bright at the receiver, and ends in an arrowhead.
    edgeLines = line(
      edges,
      edges.flatMap(() => [0.14, 0.14, 0.14, 0.62, 0.62, 0.62]),
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: LINE })
    );
    arrows = new THREE.InstancedMesh(
      new THREE.ConeGeometry(0.4, 1.5, 8),
      new THREE.MeshBasicMaterial({ color: STONE.quiet, transparent: true, opacity: LINE }),
      Math.max(edges.length, 1)
    );
    arrows.count = edges.length;
    world.add(arrows);
    stems = line(coins.map(([, i]) => [i]), null, new THREE.LineBasicMaterial({ color: STONE.ink, transparent: true, opacity: 0.16 }));
    // One set of dashed lines per kind of guess: each kind has its own colour and dash.
    const c = new THREE.Color();
    guessLines = [...new Set(guesses.map((k) => k.kind))].map((kind) => {
      const [dash, gap] = linkDash(kind);
      const own = guesses.flatMap((k, i) => (k.kind === kind ? [[k, guessPairs[i]]] : []));
      return line(
        own.map((x) => x[1]),
        // Confidence shows as brightness, and as the number on the label.
        own.flatMap(([k]) => {
          c.set(linkColor(kind)).multiplyScalar(0.4 + 0.6 * k.confidence);
          return [c.r, c.g, c.b, c.r, c.g, c.b];
        }),
        new THREE.LineDashedMaterial({ vertexColors: true, dashSize: dash * 0.28, gapSize: gap * 0.28, transparent: true })
      );
    });

    // shortcut: labels for the 60 strongest guesses only, a sprite each; draw them in one
    // texture atlas if graphs with thousands of guesses need all of them.
    linkLabels = guesses
      .map((k, i) => [k, i])
      .sort((a, b) => b[0].confidence - a[0].confidence)
      .slice(0, 60)
      .map(([k, i]) => [textSprite([[`${k.kind} ${pct(k.confidence)}`, FONT.mono, linkColor(k.kind)]], 1.6), ...guessPairs[i]]);
    const shrink = Math.min(1, Math.max(0.6, el.clientWidth / 900));
    nodeLabels = nodes.flatMap((node, i) => (node.label ? [[textSprite(node.label, 0, shrink), i]] : []));
    world.add(...[...linkLabels, ...nodeLabels].map((x) => x[0]));

    if (grew) {
      heat = 6;
      // Without motion the layout settles before the first frame; otherwise it settles on screen.
      if (still || (old.size === 0 && nodes.length <= 400)) while (heat) layoutStep();
      if (!still && old.size === 0) born = performance.now();
    }
    highlight([]);
    if (grew && nodes.length) fit(nodes.map((node) => node.p), true);
  }

  function highlight(path) {
    clear(pathGroup);
    const ids = path.map((a) => index.get(a)).filter((i) => i !== undefined);
    faded = ids.length > 1 ? new Set(ids) : null;
    for (const lines of [edgeLines, arrows]) lines.material.opacity = faded ? 0.08 : LINE;
    for (const lines of guessLines) lines.material.opacity = faded ? 0.12 : 1;
    stems.material.opacity = faded ? 0.04 : 0.16;
    for (const [label, i, j] of linkLabels) label.visible = !faded || (faded.has(i) && faded.has(j));
    if (faded) {
      const material = new THREE.MeshBasicMaterial({ color: PATH });
      const up = new THREE.Vector3(0, 1, 0);
      const dir = new THREE.Vector3();
      ids.slice(1).forEach((id, k) => {
        // A thick bar from one node's rim to the next, an arrowhead at its end, its number halfway.
        // Every part carries userData.hop (1, 2, …): trace() reveals them in that order.
        const from = nodes[ids[k]];
        const to = nodes[id];
        dir.subVectors(to.p, from.p).normalize();
        const a = from.p.clone().addScaledVector(dir, from.size + 0.4);
        const tip = to.p.clone().addScaledVector(dir, -to.size - 0.4);
        const b = tip.clone().addScaledVector(dir, -2.4);
        const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.4, 0.4, Math.max(a.distanceTo(b), 0.1), 10), material);
        bar.position.lerpVectors(a, b, 0.5);
        const head = new THREE.Mesh(new THREE.ConeGeometry(1.1, 2.4, 14), material);
        head.position.lerpVectors(b, tip, 0.5);
        bar.quaternion.setFromUnitVectors(up, dir);
        head.quaternion.copy(bar.quaternion);
        const number = hopSprite(k + 1);
        number.position.lerpVectors(a, b, 0.5);
        for (const part of [bar, head, number]) part.userData.hop = k + 1;
        pathGroup.add(bar, head, number);
      });
      for (const i of ids) {
        if (nodes[i].label) continue; // strangers on the path get a name while it is shown
        const label = textSprite([[title(nodes[i].n), FONT.text, STONE.ink]]);
        label.position.copy(nodes[i].p).add({ x: 0, y: nodes[i].size + 0.8, z: 0 });
        pathGroup.add(label);
      }
      heat = 0; // the bars are placed once, so the layout must not move under them
      fit(ids.map((i) => nodes[i].p));
      if (!still) {
        traced = performance.now();
        trace(traced);
      }
    }
    place();
  }

  function focus(address) {
    const i = index.get(address);
    if (i === undefined) return;
    touched = true; // the camera was sent somewhere: it stops following the layout
    select(i);
    fit([nodes[i].p, ...segments.filter((s) => s.includes(i)).map(([a, b]) => nodes[a === i ? b : a].p)]);
  }

  // Picking: a click that is not the end of a drag selects the node under the pointer.
  const raycaster = new THREE.Raycaster();
  const tip = document.getElementById("tip");
  const pick = (event) => {
    const rect = renderer.domElement.getBoundingClientRect();
    raycaster.setFromCamera(
      new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1),
      camera
    );
    return hit ? raycaster.intersectObject(hit)[0]?.instanceId : undefined;
  };
  let down = null;
  renderer.domElement.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 6) {
      const i = pick(e);
      select(i);
      onSelect(nodes[i]?.n || null);
    }
    down = null;
  });
  renderer.domElement.addEventListener("pointermove", (e) => {
    const node = e.buttons ? null : nodes[pick(e)];
    renderer.domElement.style.cursor = node ? "pointer" : "grab";
    tip.hidden = !node;
    if (node) {
      tip.textContent = `${title(node.n)} · ${KINDS[kindOf(node.n)][2]}`;
      tip.style.left = `${e.clientX + 14}px`;
      tip.style.top = `${e.clientY + 14}px`;
    }
  });
  renderer.domElement.addEventListener("pointerleave", () => (tip.hidden = true));

  new ResizeObserver(() => {
    const { clientWidth: w, clientHeight: h } = el;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }).observe(el);

  function frame() {
    timer.update();
    if (flight) fly(performance.now());
    if (traced) trace(performance.now());
    if (heat) for (let i = 0; i < (nodes.length > 400 ? 1 : 3) && heat; i++) layoutStep();
    controls.update(timer.getDelta());
    if (heat) {
      // Until the camera is touched it keeps the whole graph in view while the layout spreads.
      if (!touched && !faded) fit(nodes.map((node) => node.p), true);
      place();
    }
    else if (nodes.length && (born || !facing.equals(camera.quaternion))) placeNodes(performance.now());
    // From afar only my wallets are named; counterparties get their names as the camera nears.
    for (const [label, i] of nodeLabels) label.visible = faded ? faded.has(i) : Boolean(nodes[i].n.mine) || camera.position.distanceTo(nodes[i].p) < NEAR;
    scene.fog.density = 0.5 / camera.position.distanceTo(controls.target); // same fade at any zoom
    renderer.render(scene, camera);
  }

  return {
    setData,
    highlight,
    focus,
    setActive(on) {
      renderer.domElement.hidden = !on;
      renderer.setAnimationLoop(on ? frame : null);
    },
  };
}
