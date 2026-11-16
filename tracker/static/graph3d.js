// 3D wallet graph. Same interface as graph2d.js: setData, highlight, focus, setActive.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { KINDS, LINKS, kindOf, pct, title } from "./kinds.js";

const K = 14; // ideal distance between linked nodes, world units
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;

// `world` > 0 gives the label that height in world units, so it shrinks with distance;
// otherwise it keeps a constant size on screen.
function textSprite(text, color, world = 0) {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  const font = "600 28px system-ui, sans-serif";
  ctx.font = font;
  canvas.width = Math.ceil(ctx.measureText(text).width) + 16;
  canvas.height = 40;
  ctx.font = font;
  ctx.lineWidth = 6;
  ctx.strokeStyle = "#0d1117";
  ctx.strokeText(text, 8, 29);
  ctx.fillStyle = color;
  ctx.fillText(text, 8, 29);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    // Drawn over the scene: a label must stay readable.
    new THREE.SpriteMaterial({ map: texture, sizeAttenuation: world > 0, depthTest: false, transparent: true })
  );
  const height = world || 0.024;
  sprite.scale.set((canvas.width / canvas.height) * height, height, 1);
  sprite.renderOrder = 2;
  return sprite;
}

function clear(group) {
  for (const child of [...group.children]) {
    group.remove(child);
    child.geometry?.dispose();
    child.material?.map?.dispose();
    child.material?.dispose();
  }
}

export function create3d(el, { onSelect }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true }); // throws without WebGL
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  el.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1117);
  scene.fog = new THREE.FogExp2(0x0d1117, 0); // far nodes fade: a depth cue (density set per frame)
  const camera = new THREE.PerspectiveCamera(40, 1, 1, 5000);
  camera.position.set(0, 40, 160);
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(1, 2, 3);
  scene.add(sun, new THREE.AmbientLight(0xffffff, 1.1));
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = !still;
  const timer = new THREE.Timer();
  timer.connect(document);

  const world = new THREE.Group(); // rebuilt on every setData
  const pathGroup = new THREE.Group();
  scene.add(world, pathGroup);

  const positions = new Map(); // address -> Vector3, kept across updates so the picture stays put
  let nodes = [];
  let index = new Map();
  let segments = []; // [i, j] per transfer edge, then per guess
  let spheres, edgeLines, linkLines, arrows, linkLabels, nodeLabels, rings;
  let heat = 0; // > 0 while the layout is still settling
  let faded = null; // Set of node indices kept bright while a path is shown

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

  function place() {
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const s = new THREE.Vector3();
    const color = new THREE.Color();
    nodes.forEach((node, i) => {
      m.compose(node.p, q, s.setScalar(node.size));
      spheres.setMatrixAt(i, m);
      color.set(node.color).multiplyScalar(faded && !faded.has(i) ? 0.12 : node.dim);
      spheres.setColorAt(i, color);
    });
    spheres.instanceMatrix.needsUpdate = true;
    spheres.instanceColor.needsUpdate = true;
    spheres.computeBoundingSphere();
    for (const lines of [edgeLines, linkLines]) {
      const pos = lines.geometry.attributes.position;
      lines.userData.pairs.forEach(([i, j], k) => {
        pos.setXYZ(2 * k, nodes[i].p.x, nodes[i].p.y, nodes[i].p.z);
        pos.setXYZ(2 * k + 1, nodes[j].p.x, nodes[j].p.y, nodes[j].p.z);
      });
      pos.needsUpdate = true;
      lines.geometry.computeBoundingSphere();
    }
    const dir = new THREE.Vector3();
    const up = new THREE.Vector3(0, 1, 0);
    edgeLines.userData.pairs.forEach(([i, j], k) => {
      // The arrowhead sits just outside the receiving node, pointing at it.
      dir.subVectors(nodes[j].p, nodes[i].p).normalize();
      m.compose(s.copy(nodes[j].p).addScaledVector(dir, -nodes[j].size - 0.9), q.setFromUnitVectors(up, dir), new THREE.Vector3(1, 1, 1));
      arrows.setMatrixAt(k, m);
    });
    arrows.instanceMatrix.needsUpdate = true;
    arrows.computeBoundingSphere();
    linkLines.computeLineDistances();
    linkLabels.forEach(([sprite, i, j]) => sprite.position.lerpVectors(nodes[i].p, nodes[j].p, 0.5));
    nodeLabels.forEach(([sprite, i]) => sprite.position.copy(nodes[i].p).add({ x: 0, y: nodes[i].size + 1.5, z: 0 }));
    rings.forEach(([ring, i]) => ring.position.copy(nodes[i].p));
  }

  function fit(points, instant) {
    const center = points.reduce((sum, p) => sum.add(p), new THREE.Vector3()).divideScalar(points.length);
    const radius = Math.max(15, ...points.map((p) => p.distanceTo(center)));
    const dir = camera.position.clone().sub(controls.target).normalize();
    controls.target.copy(center);
    camera.position.copy(center).addScaledVector(dir, radius * 2.7);
    if (instant) controls.update();
  }

  function setData(graph, { minConf }) {
    clear(world);
    const old = index;
    nodes = graph.nodes.map((n) => {
      const kind = kindOf(n);
      return {
        n,
        color: KINDS[kind][0],
        size: n.mine ? 3 : n.category ? 2 : 1.1,
        dim: !n.mine && !n.synced_at && !n.category ? 0.55 : 1, // not expanded yet
        label: n.mine ? title(n) : n.category ? `${title(n)} · ${KINDS[kind][2]}` : null,
      };
    });
    index = new Map(nodes.map((node, i) => [node.n.address, i]));
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

    spheres = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1, 20, 14),
      new THREE.MeshLambertMaterial(),
      Math.max(nodes.length, 1)
    );
    spheres.count = nodes.length;
    spheres.setColorAt(0, new THREE.Color());
    world.add(spheres);

    const line = (pairs, colors, material) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pairs.length * 6), 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(colors), 3));
      const lines = new THREE.LineSegments(geometry, material);
      lines.userData.pairs = pairs;
      world.add(lines);
      return lines;
    };
    // Each transfer line runs dark at the sender and bright at the receiver, and ends in an arrowhead.
    edgeLines = line(
      edges,
      edges.flatMap(() => [0.16, 0.18, 0.21, 0.72, 0.76, 0.82]),
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true })
    );
    arrows = new THREE.InstancedMesh(
      new THREE.ConeGeometry(0.55, 1.8, 8),
      new THREE.MeshBasicMaterial({ color: 0xb8c1cc, transparent: true }),
      Math.max(edges.length, 1)
    );
    arrows.count = edges.length;
    world.add(arrows);
    const c = new THREE.Color();
    linkLines = line(
      guessPairs,
      // Confidence shows as brightness, and as the number on the label.
      guesses.flatMap((k) => {
        c.set(LINKS[k.kind] || "#ffffff").multiplyScalar(0.35 + 0.65 * k.confidence);
        return [c.r, c.g, c.b, c.r, c.g, c.b];
      }),
      new THREE.LineDashedMaterial({ vertexColors: true, dashSize: 1.6, gapSize: 1, transparent: true })
    );

    // shortcut: labels for the 60 strongest guesses only, a sprite each; draw them in one
    // texture atlas if graphs with thousands of guesses need all of them.
    linkLabels = guesses
      .map((k, i) => [k, i])
      .sort((a, b) => b[0].confidence - a[0].confidence)
      .slice(0, 60)
      .map(([k, i]) => [textSprite(`${k.kind} ${pct(k.confidence)}`, LINKS[k.kind] || "#fff", 1.6), ...guessPairs[i]]);
    nodeLabels = nodes.flatMap((node, i) => (node.label ? [[textSprite(node.label, "#e6edf3"), i]] : []));
    rings = nodes.flatMap((node, i) => {
      if (!node.n.mine || !node.n.public) return [];
      // Public wallets wear a white ring, so the split does not rest on colour alone.
      const ring = new THREE.Mesh(new THREE.TorusGeometry(4.3, 0.35, 8, 40), new THREE.MeshBasicMaterial({ color: 0xffffff }));
      ring.onBeforeRender = () => ring.quaternion.copy(camera.quaternion); // always faces the viewer
      return [[ring, i]];
    });
    world.add(...[...linkLabels, ...nodeLabels, ...rings].map((x) => x[0]));

    if (grew) {
      heat = 6;
      // Without motion the layout settles before the first frame; otherwise it settles on screen.
      if (still || (old.size === 0 && nodes.length <= 400)) while (heat) layoutStep();
    }
    highlight([]);
    if (grew && nodes.length) fit(nodes.map((node) => node.p), true);
  }

  function highlight(path) {
    clear(pathGroup);
    const ids = path.map((a) => index.get(a)).filter((i) => i !== undefined);
    faded = ids.length > 1 ? new Set(ids) : null;
    for (const lines of [edgeLines, linkLines, arrows]) lines.material.opacity = faded ? 0.12 : 1;
    for (const [sprite, i, j] of linkLabels) sprite.visible = !faded || (faded.has(i) && faded.has(j));
    for (const [sprite, i] of nodeLabels) sprite.visible = !faded || faded.has(i);
    if (faded) {
      const material = new THREE.MeshBasicMaterial({ color: 0xf0883e });
      const up = new THREE.Vector3(0, 1, 0);
      ids.slice(1).forEach((id, k) => {
        const a = nodes[ids[k]].p;
        const b = nodes[id].p;
        const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, a.distanceTo(b), 8), material);
        bar.position.lerpVectors(a, b, 0.5);
        bar.quaternion.setFromUnitVectors(up, b.clone().sub(a).normalize());
        pathGroup.add(bar);
      });
      for (const i of ids) {
        if (nodes[i].label) continue; // strangers on the path get a name while it is shown
        const sprite = textSprite(title(nodes[i].n), "#e6edf3");
        sprite.position.copy(nodes[i].p).add({ x: 0, y: nodes[i].size + 1.5, z: 0 });
        pathGroup.add(sprite);
      }
      heat = 0; // the bars are placed once, so the layout must not move under them
      fit(ids.map((i) => nodes[i].p));
    }
    place();
  }

  function focus(address) {
    const i = index.get(address);
    if (i !== undefined) fit([nodes[i].p, ...segments.filter((s) => s.includes(i)).map(([a, b]) => nodes[a === i ? b : a].p)]);
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
    return nodes[raycaster.intersectObject(spheres)[0]?.instanceId];
  };
  let down = null;
  renderer.domElement.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 6) onSelect(pick(e)?.n || null);
    down = null;
  });
  renderer.domElement.addEventListener("pointermove", (e) => {
    const node = e.buttons ? null : pick(e);
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
    if (heat) {
      for (let i = 0; i < (nodes.length > 400 ? 1 : 3) && heat; i++) layoutStep();
      place();
    }
    controls.update(timer.getDelta());
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
