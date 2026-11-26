// The cinematic 3D view: the rocket, built from its own outline, flying over terrain in WebGL.
//
// World axes are east-north-up in the flight file; three.js is y-up, so a point
// (e, n, u) is drawn at (e, u, -n). Everything is a pure function of playback
// time, so scrubbing backwards rebuilds the same smoke, canopies and panels.
// Motion is calm by design: cameras ease towards their goals, nothing flashes.

import * as THREE from "three";
import { OrbitControls } from "../vendor/OrbitControls.js";
import { phaseKind } from "./flight.js";
import { fmt } from "./format.js";

const DEG = Math.PI / 180;
const UP = new THREE.Vector3(0, 1, 0);
const MODES = ["auto", "chase", "ground", "orbit"];
const SMOKE_DT = 0.012; // s between smoke puffs along the powered ascent
const TERRAIN_SIZE = 36000;

const toWorld = (e, n, u, out = new THREE.Vector3()) => out.set(e, u, -n);
const damp = (dt, rate) => 1 - Math.exp(-dt * rate);
const smooth = (x) => x * x * (3 - 2 * x);
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

// --- Procedural terrain ---------------------------------------------------------------

function hash(x, y) {
  const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

function valueNoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = smooth(xf), v = smooth(yf);
  const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

function fbm(x, y) {
  let total = 0, amp = 0.5, f = 1;
  for (let o = 0; o < 5; o++) {
    total += amp * valueNoise(x * f, y * f);
    f *= 2.03;
    amp *= 0.5;
  }
  return total;
}

/** Height of the ground [m] at a point; flat around the pad, a mountain range far out. */
function groundHeight(x, z) {
  const r = Math.hypot(x, z);
  const ridge = 1 - Math.abs(fbm(x / 5200 + 7.3, z / 5200 - 2.1) * 2 - 1);
  const far = smooth(clamp((r - 4500) / 9000, 0, 1));
  const near = smooth(clamp((r - 3500) / 3000, 0, 1));
  return far * ridge * ridge * 1150 + near * (fbm(x / 1400, z / 1400) - 0.45) * 9;
}

// --- Shaders --------------------------------------------------------------------------

const skyShader = {
  uniforms: {
    zenith: { value: new THREE.Color() },
    horizon: { value: new THREE.Color() },
    ground: { value: new THREE.Color() },
    sunDir: { value: new THREE.Vector3() },
    sunColor: { value: new THREE.Color() },
  },
  vertexShader: `
    varying vec3 vDir;
    void main() {
      vDir = normalize(position);
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }`,
  fragmentShader: `
    uniform vec3 zenith, horizon, ground, sunDir, sunColor;
    varying vec3 vDir;
    void main() {
      float h = vDir.y;
      vec3 sky = mix(horizon, zenith, pow(clamp(h, 0.0, 1.0), 0.55));
      vec3 col = h > 0.0 ? sky : mix(horizon, ground, clamp(-h * 6.0, 0.0, 1.0));
      float s = max(dot(normalize(vDir), normalize(sunDir)), 0.0);
      col += sunColor * (pow(s, 900.0) * 1.4 + pow(s, 24.0) * 0.18);
      gl_FragColor = vec4(col, 1.0);
    }`,
};

const plumeShader = {
  uniforms: { time: { value: 0 }, strength: { value: 1 }, core: { value: new THREE.Color() }, edge: { value: new THREE.Color() } },
  vertexShader: `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `
    uniform float time, strength;
    uniform vec3 core, edge;
    varying vec2 vUv;
    void main() {
      float along = 1.0 - vUv.y;            // 1 at the nozzle, 0 at the tail
      float around = abs(vUv.x - 0.5) * 2.0;
      float ripple = 0.92 + 0.08 * sin(along * 26.0 - time * 7.0); // slow, gentle, never a flash
      float a = pow(along, 1.6) * (1.0 - around * 0.35) * ripple * strength;
      vec3 c = mix(edge, core, pow(along, 3.0));
      gl_FragColor = vec4(c * a, a);
    }`,
};

const smokeShader = {
  uniforms: { color: { value: new THREE.Color() }, scale: { value: 1 } },
  vertexShader: `
    attribute float size;
    attribute float alpha;
    uniform float scale;
    varying float vAlpha;
    void main() {
      vAlpha = alpha;
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = clamp(size * scale / -mv.z, 0.0, 420.0);
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: `
    uniform vec3 color;
    varying float vAlpha;
    void main() {
      vec2 d = gl_PointCoord - 0.5;
      float r = dot(d, d) * 4.0;
      float a = vAlpha * smoothstep(1.0, 0.0, r);
      if (a < 0.003) discard;
      gl_FragColor = vec4(color, a);
    }`,
};

const glowShader = {
  uniforms: { color: { value: new THREE.Color() }, size: { value: 14 } },
  vertexShader: `
    uniform float size;
    void main() { gl_PointSize = size; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `
    uniform vec3 color;
    void main() {
      vec2 d = gl_PointCoord - 0.5;
      float r = length(d) * 2.0;
      float a = smoothstep(1.0, 0.15, r) * 0.55 + smoothstep(0.35, 0.0, r) * 0.45;
      gl_FragColor = vec4(color, a);
    }`,
};

// --- Themes ---------------------------------------------------------------------------

const THEMES = {
  light: {
    zenith: "#3f6fae", horizon: "#d9e4ee", groundSky: "#b9b2a2", fog: "#d2dce5",
    sun: "#fff4dc", sunDir: [0.55, 0.42, 0.3], sunIntensity: 2.4, hemiSky: "#dfe9f5", hemiGround: "#8a7f68", hemi: 1.1,
    low: "#b7a58a", mid: "#a39176", high: "#8b8172", peak: "#b9b0a2", flare: "#ffc68a",
    smoke: "#f7f5f0", body: "#f4f3ef", accent: "#d9481c", fin: "#24272c", metal: "#9aa1aa",
    plumeCore: "#fff6d8", plumeEdge: "#ff7a2e", exposure: 0.95,
  },
  dark: {
    zenith: "#122247", horizon: "#6a5f78", groundSky: "#221f29", fog: "#4a4459",
    sun: "#ffb27a", sunDir: [-0.6, 0.06, 0.45], sunIntensity: 1.9, hemiSky: "#8796c2", hemiGround: "#2a2630", hemi: 1.05,
    low: "#57504b", mid: "#615955", high: "#6e6663", peak: "#9a94a0", flare: "#ffb978",
    smoke: "#c9cbd3", body: "#e9e7e2", accent: "#ff6b3d", fin: "#1b1d22", metal: "#8c949e",
    plumeCore: "#fff3d0", plumeEdge: "#ff6420", exposure: 1.12,
  },
};

function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set) return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
  } catch {
    return false;
  }
}

// --- The view -------------------------------------------------------------------------

export class Cinema {
  constructor(canvas, palette, { reducedMotion, labels, onModeChange }) {
    this.canvas = canvas;
    this.palette = palette;
    this.reducedMotion = reducedMotion;
    this.labelsEl = labels;
    this.onModeChange = onModeChange;
    this.dirty = true;
    this.flight = null;
    this.state = null;
    this.mode = "auto";
    this.time = 0;
    this.clock = 0;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, logarithmicDepthBuffer: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer = renderer;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.05, 60000);
    this.camera.position.set(-30, 4, 30);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = !reducedMotion;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = 88 * DEG;
    this.controls.minDistance = 2;
    this.controls.maxDistance = 30000;
    this.controls.enabled = false;
    this.controls.addEventListener("start", () => {
      if (this.mode !== "orbit") this.setMode("orbit", { fromUser: true });
    });
    this.controls.addEventListener("change", () => (this.dirty = true));

    this._buildWorld();
    this.applyTheme();
    canvas.addEventListener("dblclick", () => this.resetView());
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();
  }

  // --- World ------------------------------------------------------------------------

  _buildWorld() {
    const s = this.scene;
    this.sky = new THREE.Mesh(
      new THREE.SphereGeometry(40000, 48, 24),
      new THREE.ShaderMaterial({ ...skyShader, uniforms: THREE.UniformsUtils.clone(skyShader.uniforms), side: THREE.BackSide, depthWrite: false })
    );
    this.sky.frustumCulled = false;
    this.sky.renderOrder = -1;
    s.add(this.sky);
    s.fog = new THREE.Fog(0xffffff, 1200, 15000);

    this.hemi = new THREE.HemisphereLight(0xffffff, 0x444444, 1);
    this.sun = new THREE.DirectionalLight(0xffffff, 2);
    // a soft light from the camera keeps the airframe readable against any sky
    this.key = new THREE.DirectionalLight(0xffffff, 0.55);
    s.add(this.hemi, this.sun, this.sun.target, this.key, this.key.target);

    // stars for the dark theme: fixed and dim, no twinkle
    const starPos = [];
    for (let i = 0; i < 2600; i++) {
      const u = hash(i, 1), v = hash(i, 2);
      const theta = u * Math.PI * 2, y = 0.04 + v * 0.96;
      const r = Math.sqrt(1 - y * y);
      starPos.push(Math.cos(theta) * r * 30000, y * 30000, Math.sin(theta) * r * 30000);
    }
    const starGeo = new THREE.BufferGeometry();
    starGeo.setAttribute("position", new THREE.Float32BufferAttribute(starPos, 3));
    this.stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xdfe6ff, size: 1.3, sizeAttenuation: false, transparent: true, opacity: 0.7, fog: false, depthWrite: false }));
    this.stars.frustumCulled = false;
    s.add(this.stars);

    // terrain
    const geo = new THREE.PlaneGeometry(TERRAIN_SIZE, TERRAIN_SIZE, 220, 220);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) pos.setY(i, groundHeight(pos.getX(i), pos.getZ(i)));
    geo.computeVertexNormals();
    geo.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array(pos.count * 3), 3));
    this.terrain = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.95, metalness: 0 }));
    s.add(this.terrain);

    // launch pad: concrete apron, blast plate and rail
    this.pad = new THREE.Group();
    const concrete = new THREE.MeshStandardMaterial({ color: 0x9c9a94, roughness: 0.9 });
    const apron = new THREE.Mesh(new THREE.CylinderGeometry(6, 6.4, 0.3, 48), concrete);
    apron.position.y = 0.15;
    const plate = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.04, 1.2), new THREE.MeshStandardMaterial({ color: 0x55585e, metalness: 0.7, roughness: 0.4 }));
    plate.position.y = 0.32;
    this.pad.add(apron, plate);
    this.railMat = new THREE.MeshStandardMaterial({ color: 0x6d737b, metalness: 0.8, roughness: 0.35 });
    s.add(this.pad);

    // flight path: everything flown so far, solid; the rest, faint
    this.pathPast = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9 }));
    this.pathFuture = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ transparent: true, opacity: 0.22, depthWrite: false }));
    this.pathPast.frustumCulled = this.pathFuture.frustumCulled = false;
    s.add(this.pathPast, this.pathFuture);

    // a soft glow that keeps the rocket findable from kilometres away
    const glowGeo = new THREE.BufferGeometry();
    glowGeo.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0], 3));
    this.glow = new THREE.Points(glowGeo, new THREE.ShaderMaterial({ ...glowShader, uniforms: THREE.UniformsUtils.clone(glowShader.uniforms), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    this.glow.frustumCulled = false;
    s.add(this.glow);

    // target-apogee halo (airbrake flights)
    this.targetRing = new THREE.Mesh(
      new THREE.RingGeometry(0.985, 1, 128),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.55, side: THREE.DoubleSide, depthWrite: false, fog: false })
    );
    this.targetDisc = new THREE.Mesh(
      new THREE.CircleGeometry(1, 96),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.035, side: THREE.DoubleSide, depthWrite: false, fog: false })
    );
    for (const m of [this.targetRing, this.targetDisc]) {
      m.rotation.x = -Math.PI / 2;
      m.visible = false;
      s.add(m);
    }

    // landing marker
    this.landing = new THREE.Mesh(new THREE.RingGeometry(3.2, 4, 48), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.8, side: THREE.DoubleSide }));
    this.landing.rotation.x = -Math.PI / 2;
    s.add(this.landing);
  }

  applyTheme() {
    const T = THEMES[currentTheme()];
    this.themeName = currentTheme();
    const u = this.sky.material.uniforms;
    u.zenith.value.set(T.zenith);
    u.horizon.value.set(T.horizon);
    u.ground.value.set(T.groundSky);
    u.sunColor.value.set(T.sun);
    const sunDir = new THREE.Vector3(...T.sunDir).normalize();
    u.sunDir.value.copy(sunDir);
    this.scene.fog.color.set(T.fog);
    this.hemi.color.set(T.hemiSky);
    this.hemi.groundColor.set(T.hemiGround);
    this.hemi.intensity = T.hemi;
    this.sun.color.set(T.sun);
    this.sun.intensity = T.sunIntensity;
    this.sun.position.copy(sunDir).multiplyScalar(1000);
    this.renderer.toneMappingExposure = T.exposure;

    // terrain colours by height and slope
    const geo = this.terrain.geometry;
    const pos = geo.attributes.position, nrm = geo.attributes.normal, col = geo.attributes.color;
    const low = new THREE.Color(T.low), mid = new THREE.Color(T.mid), high = new THREE.Color(T.high), peak = new THREE.Color(T.peak);
    const c = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const h = pos.getY(i), steep = 1 - nrm.getY(i);
      c.copy(low).lerp(mid, clamp(h / 300 + steep * 1.5, 0, 1)).lerp(high, clamp((h - 300) / 600, 0, 1)).lerp(peak, clamp((h - 1150) / 350, 0, 1) * (1 - steep) * 0.8);
      const jitter = 0.96 + hash(i, 3) * 0.08;
      col.setXYZ(i, c.r * jitter, c.g * jitter, c.b * jitter);
    }
    col.needsUpdate = true;
    this.stars.visible = this.themeName === "dark";
    if (this.flare) this.flare.material.uniforms.color.value.set(T.flare);

    const P = this.palette.colors;
    this.pathFuture.material.color.set(P.ink3 || "#888");
    this.glow.material.uniforms.color.value.set(T.accent);
    this.targetRing.material.color.set(T.accent);
    this.targetDisc.material.color.set(T.accent);
    this.landing.material.color.set(P.main || "#2c8466");
    if (this.smoke) this.smoke.material.uniforms.color.value.set(T.smoke);
    if (this.plume) {
      this.plume.material.uniforms.core.value.set(T.plumeCore);
      this.plume.material.uniforms.edge.value.set(T.plumeEdge);
    }
    if (this.rocketMats) {
      this.rocketMats.body.color.set(T.body);
      this.rocketMats.accent.color.set(T.accent);
      this.rocketMats.fin.color.set(T.fin);
      this.rocketMats.metal.color.set(T.metal);
    }
    if (this.flight) this._colorPath();
    this.dirty = true;
  }

  // --- A flight ---------------------------------------------------------------------

  setFlight(flight) {
    this.flight = flight;
    if (this.rocket) this.scene.remove(this.rocket);
    if (this.rail) this.pad.remove(this.rail);
    for (const c of this.canopies || []) this.scene.remove(c.group);
    if (this.smoke) this.scene.remove(this.smoke);

    this._buildRocket(flight);
    this._buildRail(flight);
    this._buildPath(flight);
    this._buildSmoke(flight);
    this._buildCanopies(flight);

    const last = flight.n - 1;
    toWorld(flight.east[last], flight.north[last], 0.35, this.landing.position);
    const ab = flight.airbrakes;
    for (const m of [this.targetRing, this.targetDisc]) m.visible = !!ab;
    if (ab) {
      const i = flight.index(flight.apogeeTime);
      const r = Math.max(260, flight.summary.apogee * 0.07);
      for (const m of [this.targetRing, this.targetDisc]) {
        toWorld(flight.east[i], flight.north[i], ab.target, m.position);
        m.scale.setScalar(r);
      }
    }
    this._buildLabels(flight);
    this.groundCam = this._groundCamPosition(flight);
    this.chaseOffset = null;
    this.blend = null;
    this.lastTime = null;
    this.applyTheme();
    this.resetView();
  }

  _buildRocket(f) {
    const o = f.rocket.outline;
    const T = THEMES[currentTheme()];
    const mats = {
      body: new THREE.MeshStandardMaterial({ color: T.body, roughness: 0.32, metalness: 0.05 }),
      accent: new THREE.MeshStandardMaterial({ color: T.accent, roughness: 0.4, metalness: 0.05 }),
      fin: new THREE.MeshStandardMaterial({ color: T.fin, roughness: 0.45, metalness: 0.2 }),
      metal: new THREE.MeshStandardMaterial({ color: T.metal, roughness: 0.28, metalness: 0.85 }),
      band: new THREE.MeshStandardMaterial({ color: 0x1a1c20, roughness: 0.6 }),
    };
    this.rocketMats = mats;
    const g = new THREE.Group();
    const cg = o.cg_liftoff;
    const yOf = (x) => cg - x; // local +y runs to the nose; origin at the liftoff CG

    if (o.nose) {
      const pts = o.nose.profile.map(([x, r]) => new THREE.Vector2(Math.max(r, 1e-4), yOf(x)));
      pts[0].x = 0.0001;
      const nose = new THREE.Mesh(new THREE.LatheGeometry(pts, 48), mats.accent);
      g.add(nose);
    }
    for (const b of o.bodies) {
      const r = b.diameter / 2;
      const tube = new THREE.Mesh(new THREE.CylinderGeometry(r, r, b.length, 48, 1, true), mats.body);
      tube.position.y = yOf(b.start + b.length / 2);
      g.add(tube);
      // a thin joint line at each section break, like a real airframe
      const joint = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.003, r * 1.003, 0.004, 48, 1, true), mats.band);
      joint.position.y = yOf(b.start);
      g.add(joint);
    }
    // aft closure and nozzle
    const tailR = o.diameter / 2;
    const tail = new THREE.Mesh(new THREE.CircleGeometry(tailR, 48), mats.band);
    tail.rotation.x = Math.PI / 2;
    tail.position.y = yOf(o.length);
    const nozzle = new THREE.Mesh(new THREE.CylinderGeometry(tailR * 0.42, tailR * 0.3, 0.05, 32, 1, true), mats.metal);
    nozzle.position.y = yOf(o.length) - 0.02;
    g.add(tail, nozzle);

    for (const fin of o.fins) {
      const shape = new THREE.Shape();
      shape.moveTo(0, 0);
      shape.lineTo(fin.span, -fin.sweep);
      shape.lineTo(fin.span, -fin.sweep - fin.tip_chord);
      shape.lineTo(0, -fin.root_chord);
      shape.closePath();
      const geom = new THREE.ExtrudeGeometry(shape, { depth: 0.004, bevelEnabled: true, bevelThickness: 0.0012, bevelSize: 0.0015, bevelSegments: 1 });
      geom.translate(0, 0, -0.002);
      const r = o.diameter / 2;
      for (let k = 0; k < fin.count; k++) {
        const m = new THREE.Mesh(geom, mats.fin);
        const pivot = new THREE.Group();
        m.position.set(r - 0.002, yOf(fin.start), 0);
        pivot.add(m);
        pivot.rotation.y = (k / fin.count) * Math.PI * 2;
        g.add(pivot);
      }
    }

    this.brakePanels = [];
    const ab = o.airbrakes;
    if (ab) {
      const r = o.diameter / 2;
      const y = yOf(ab.start + ab.length * 0.35);
      const slot = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.002, r * 1.002, 0.012, 48, 1, true), mats.band);
      slot.position.y = y;
      g.add(slot);
      const panelGeo = new THREE.BoxGeometry(ab.panel_width, 0.006, ab.panel_height);
      for (let k = 0; k < ab.panels; k++) {
        const pivot = new THREE.Group();
        pivot.rotation.y = (k / ab.panels) * Math.PI * 2 + Math.PI / ab.panels;
        const panel = new THREE.Mesh(panelGeo, mats.metal);
        panel.position.set(0, y, r - ab.panel_height / 2);
        pivot.add(panel);
        g.add(pivot);
        this.brakePanels.push({ panel, base: r - ab.panel_height / 2, travel: ab.panel_height });
      }
    }

    // the exhaust plume, hanging off the nozzle
    const plumeLen = 1;
    const plumeGeo = new THREE.ConeGeometry(tailR * 0.95, plumeLen, 32, 12, true);
    plumeGeo.rotateX(Math.PI); // apex down, wide end up at the nozzle
    plumeGeo.translate(0, -plumeLen / 2, 0);
    const plume = new THREE.Mesh(
      plumeGeo,
      new THREE.ShaderMaterial({
        ...plumeShader,
        uniforms: THREE.UniformsUtils.clone(plumeShader.uniforms),
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
      })
    );
    plume.position.y = yOf(o.length) - 0.04;
    this.plume = plume;
    this.plumeBase = plume.position.y;
    const flareGeo = new THREE.BufferGeometry();
    flareGeo.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0], 3));
    flareGeo.setAttribute("size", new THREE.Float32BufferAttribute([1.6], 1));
    flareGeo.setAttribute("alpha", new THREE.Float32BufferAttribute([0], 1));
    this.flare = new THREE.Points(
      flareGeo,
      new THREE.ShaderMaterial({ ...smokeShader, uniforms: THREE.UniformsUtils.clone(smokeShader.uniforms), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending })
    );
    this.flare.material.uniforms.color.value.set(T.flare);
    this.flare.position.y = yOf(o.length) - 0.25;
    this.flare.frustumCulled = false;
    g.add(this.flare);
    this.plumeLight = new THREE.PointLight(0xff8a3c, 0, 40, 2);
    this.plumeLight.position.y = yOf(o.length) - 0.4;
    g.add(plume, this.plumeLight);

    this.rocketLength = o.length;
    this.rocket = g;
    this.scene.add(g);
  }

  _buildRail(f) {
    const site = f.site;
    const len = Math.max(site.rail_length, 1) + 0.6;
    const rail = new THREE.Group();
    const beam = new THREE.Mesh(new THREE.BoxGeometry(0.04, len, 0.04), this.railMat);
    beam.position.y = len / 2;
    beam.position.x = -(f.rocket.outline.diameter / 2 + 0.03);
    rail.add(beam);
    const tilt = (site.tilt || 0) * DEG, heading = (site.heading || 0) * DEG;
    // lean `tilt` from vertical towards compass `heading`
    const dir = toWorld(Math.sin(tilt) * Math.sin(heading), Math.sin(tilt) * Math.cos(heading), Math.cos(tilt)).normalize();
    rail.quaternion.setFromUnitVectors(UP, dir);
    rail.position.y = 0.32;
    this.rail = rail;
    this.pad.add(rail);
  }

  _buildPath(f) {
    const pts = new Float32Array(f.n * 3);
    const v = new THREE.Vector3();
    for (let i = 0; i < f.n; i++) {
      toWorld(f.east[i], f.north[i], f.up[i], v);
      pts.set([v.x, v.y, v.z], i * 3);
    }
    for (const line of [this.pathPast, this.pathFuture]) {
      line.geometry.dispose();
      line.geometry = new THREE.BufferGeometry();
      line.geometry.setAttribute("position", new THREE.BufferAttribute(pts, 3));
    }
    this.pathPast.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(f.n * 3), 3));
    this._colorPath();
  }

  _colorPath() {
    const f = this.flight, P = this.palette.colors;
    const colors = { boost: new THREE.Color(P.boost), coast: new THREE.Color(P.coast), drogue: new THREE.Color(P.drogue), main: new THREE.Color(P.main) };
    const attr = this.pathPast.geometry.attributes.color;
    for (let i = 0; i < f.n; i++) {
      const c = colors[phaseKind(f.phaseAt(f.t[i]))] || colors.coast;
      attr.setXYZ(i, c.r, c.g, c.b);
    }
    attr.needsUpdate = true;
  }

  _buildSmoke(f) {
    const burn = f.rocket.motor ? f.rocket.motor.burn_time : 0;
    const times = [];
    for (let t = 0; t <= burn + 0.15; t += SMOKE_DT) times.push(t);
    // a little lingering exhaust on the pad before liftoff clears the rail
    const n = times.length;
    const base = new Float32Array(n * 3);
    const v = new THREE.Vector3();
    times.forEach((t, k) => {
      const st = f.sample(t);
      // puffs start just behind the nozzle
      const back = this.rocketLength * 0.9;
      toWorld(st.east - st.axis[0] * back, st.north - st.axis[1] * back, Math.max(0.5, st.up - st.axis[2] * back), v);
      base.set([v.x, v.y, v.z], k * 3);
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(base), 3));
    geo.setAttribute("size", new THREE.BufferAttribute(new Float32Array(n), 1));
    geo.setAttribute("alpha", new THREE.BufferAttribute(new Float32Array(n), 1));
    const T = THEMES[currentTheme()];
    const mat = new THREE.ShaderMaterial({ ...smokeShader, uniforms: THREE.UniformsUtils.clone(smokeShader.uniforms), transparent: true, depthWrite: false });
    mat.uniforms.color.value.set(T.smoke);
    this.smoke = new THREE.Points(geo, mat);
    this.smoke.frustumCulled = false;
    this.smokeData = { times, base, seeds: times.map((_, k) => hash(k, 11)) };
    const w = f.site.wind;
    const from = (w.direction || 0) * DEG;
    // wind blows *from* `direction`, so the air moves the other way
    this.windVec = toWorld(-Math.sin(from) * w.speed * 1.3, -Math.cos(from) * w.speed * 1.3, 0);
    this.scene.add(this.smoke);
  }

  _buildCanopies(f) {
    this.canopies = [];
    const chutes = f.events.filter((e) => / opens$/.test(e.name) && !/^Airbrakes/.test(e.name));
    chutes.forEach((e, k) => {
      const isMain = /main/i.test(e.name) || k > 0;
      const radius = isMain ? 0.9 : 0.32;
      const group = new THREE.Group();
      const gores = 12;
      const geo = new THREE.SphereGeometry(radius, gores * 2, 10, 0, Math.PI * 2, 0, Math.PI * 0.46);
      const colors = [];
      const pos = geo.attributes.position;
      const a = new THREE.Color(isMain ? this.palette.colors.boost : "#f0eee8");
      const b = new THREE.Color(isMain ? "#f2efe8" : this.palette.colors.drogue);
      for (let i = 0; i < pos.count; i++) {
        const ang = Math.atan2(pos.getZ(i), pos.getX(i)) + Math.PI;
        const c = Math.floor((ang / (Math.PI * 2)) * gores) % 2 ? a : b;
        colors.push(c.r, c.g, c.b);
      }
      geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
      const canopy = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85, side: THREE.DoubleSide }));
      const lines = [];
      const lineLen = radius * 3.2;
      for (let j = 0; j < 12; j++) {
        const ang = (j / 12) * Math.PI * 2;
        const rimY = Math.cos(Math.PI * 0.46) * radius;
        const rimR = Math.sin(Math.PI * 0.46) * radius;
        lines.push(Math.cos(ang) * rimR, rimY, Math.sin(ang) * rimR, 0, -lineLen, 0);
      }
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.Float32BufferAttribute(lines, 3));
      const shrouds = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: 0x8a8d93, transparent: true, opacity: 0.8 }));
      group.add(canopy, shrouds);
      group.visible = false;
      this.scene.add(group);
      this.canopies.push({ group, time: e.time, isMain, lineLen, cord: isMain ? 7 : 4.5, sway: hash(k, 5) * 6 });
    });
  }

  _buildLabels(f) {
    const el = this.labelsEl;
    if (!el) return;
    el.innerHTML = "";
    const add = (text, world, cls = "") => {
      const d = document.createElement("span");
      d.className = `scene-label ${cls}`;
      d.textContent = text;
      el.append(d);
      return { el: d, world };
    };
    const i = f.index(f.apogeeTime);
    const last = f.n - 1;
    this.labels = [
      add("Pad", toWorld(0, 0, 0)),
      add(`Apogee ${fmt(f.summary.apogee, 0)} m`, toWorld(f.east[i], f.north[i], f.summary.apogee), "apogee"),
      add(`Landing ${fmt(f.summary.landing_distance, 0)} m`, toWorld(f.east[last], f.north[last], 0)),
    ];
    if (f.airbrakes) {
      this.labels.push(add(`Target ${fmt(f.airbrakes.target, 0)} m`, toWorld(f.east[i], f.north[i], f.airbrakes.target).add(new THREE.Vector3(this.targetRing.scale.x, 0, 0)), "target"));
    }
  }

  // --- Cameras --------------------------------------------------------------------------

  /** A tracking camera on the ground, off to the side of the flight like a range camera. */
  _groundCamPosition(f) {
    const last = f.n - 1;
    let dx = f.east[last], dz = -f.north[last];
    const len = Math.hypot(dx, dz);
    if (len < 1) { dx = 1; dz = 0; } else { dx /= len; dz /= len; }
    // stand perpendicular to the drift, 420 m out
    const x = -dz * 420 - dx * 60, z = dx * 420 - dz * 60;
    return new THREE.Vector3(x, groundHeight(x, z) + 1.6, z);
  }

  setMode(mode, { fromUser = false } = {}) {
    if (!MODES.includes(mode)) return;
    if (mode === this.mode && !fromUser) return;
    this._startBlend();
    this.mode = mode;
    this.controls.enabled = mode === "orbit";
    if (mode === "orbit" && !fromUser) this._frameOrbit();
    if (mode === "orbit") this.blend = null; // the user is steering
    this.onModeChange?.(mode);
    this.dirty = true;
  }

  resetView() {
    this.setMode("auto");
    this.chaseOffset = null;
    if (this.reducedMotion) this.blend = null;
    this.dirty = true;
  }

  _frameOrbit() {
    const f = this.flight;
    if (!f) return;
    const b = f.bounds;
    const center = toWorld((b.minE + b.maxE) / 2, (b.minN + b.maxN) / 2, b.maxU * 0.45);
    const span = Math.max(b.maxU, b.maxE - b.minE, b.maxN - b.minN);
    this.controls.target.copy(center);
    const dir = this.camera.position.clone().sub(center);
    dir.y = Math.max(dir.y, span * 0.2);
    dir.setLength(span * 1.5);
    this.camera.position.copy(center).add(dir);
    this.camera.fov = 40;
    this.camera.updateProjectionMatrix();
  }

  _startBlend() {
    if (this.reducedMotion) return;
    this.blend = {
      from: this.camera.position.clone(),
      look: this._look ? this._look.clone() : this.controls.target.clone(),
      fov: this.camera.fov,
      t: 0,
      duration: 1.8,
    };
  }

  _directedMode(t) {
    const f = this.flight;
    const burn = f.rocket.motor ? f.rocket.motor.burn_time : 0;
    if (t < burn + 1.6) return "ground";
    return "chase";
  }

  /** Camera pose for a mode at this instant: {pos, look, fov}. */
  _pose(mode, rocketPos, axis, dt, snap) {
    const f = this.flight;
    if (mode === "ground") {
      const pos = this.groundCam.clone();
      const dist = pos.distanceTo(rocketPos);
      // frame the rocket and some sky: telephoto at liftoff, wider as it climbs
      const frame = 16 + rocketPos.y * 0.06;
      const fov = clamp((2 * Math.atan(frame / 2 / dist)) / DEG, 1.2, 50);
      return { pos, look: rocketPos.clone().add(new THREE.Vector3(0, frame * 0.12, 0)), fov };
    }
    // chase: an offset behind and beside the rocket that eases round as it turns
    const st = this.state;
    const descending = st && st.t > f.apogeeTime;
    const back = descending ? 30 : 7 + Math.min(st ? st.speed : 0, 300) * 0.012;
    const goal = new THREE.Vector3();
    if (descending) {
      goal.set(-0.65, 0.18, 0.75).setLength(back);
    } else {
      goal.copy(axis).multiplyScalar(-back);
      const side = new THREE.Vector3().crossVectors(axis, UP);
      if (side.lengthSq() < 1e-4) side.set(1, 0, 0);
      side.normalize().multiplyScalar(back * 0.42);
      goal.add(side).add(new THREE.Vector3(0, back * 0.1, 0));
    }
    if (!this.chaseOffset || snap) this.chaseOffset = goal.clone();
    else this.chaseOffset.lerp(goal, this.reducedMotion ? 1 : damp(dt, 1.6));
    this._chaseMoving = this.chaseOffset.distanceTo(goal) > 0.02;
    const look = rocketPos.clone().add(axis.clone().multiplyScalar(descending ? -3 : this.rocketLength * 0.25));
    if (descending) look.y += 4;
    return { pos: rocketPos.clone().add(this.chaseOffset), look, fov: descending ? 38 : 42 };
  }

  // --- Per-frame ------------------------------------------------------------------------

  setState(state) {
    this.state = state;
    this.dirty = true;
  }

  tick(dt) {
    if (!this.flight || !this.state) return false;
    this.clock += dt;
    const st = this.state;
    const snap = this.lastTime == null || Math.abs(st.t - this.lastTime) > 1.0;
    this.lastTime = st.t;

    // rocket pose
    const f = this.flight;
    const pos = toWorld(st.east, st.north, st.up);
    let axis = toWorld(st.axis[0], st.axis[1], st.axis[2]).normalize();
    // hanging under a canopy, ease the airframe over to horizontal
    const firstChute = this.canopies.length ? this.canopies[0].time : Infinity;
    const hang = clamp((st.t - firstChute) / 2.5, 0, 1);
    if (hang > 0) {
      const flat = new THREE.Vector3(axis.x, 0, axis.z);
      if (flat.lengthSq() < 1e-6) flat.set(1, 0, 0);
      flat.normalize();
      axis = axis.lerp(flat, smooth(hang)).normalize();
    }
    this.sky.position.copy(this.camera.position);
    this.stars.position.copy(this.camera.position);
    this.rocket.position.copy(pos);
    this.rocket.quaternion.setFromUnitVectors(UP, axis);
    this.glow.position.copy(pos);

    // plume: steady while thrusting, fading out over a third of a second after burnout
    const burn = f.rocket.motor ? f.rocket.motor.burn_time : 0;
    const strength = st.t < burn ? clamp(st.t / 0.08, 0, 1) : clamp(1 - (st.t - burn) / 0.35, 0, 1);
    this.plume.visible = strength > 0.001;
    if (this.plume.visible) {
      const len = (1.6 + Math.min(st.accel, 25) * 0.24) * strength;
      this.plume.scale.set(1, Math.max(len, 0.01), 1);
      this.plume.material.uniforms.time.value = this.reducedMotion ? 0 : this.clock;
      this.plume.material.uniforms.strength.value = strength;
    }
    this.plumeLight.intensity = strength * 18;
    this.flare.geometry.attributes.alpha.array[0] = strength * 0.7;
    this.flare.geometry.attributes.alpha.needsUpdate = true;
    this.flare.visible = strength > 0.001;

    // airbrake panels slide out with the deployment
    for (const p of this.brakePanels) p.panel.position.z = p.base + p.travel * st.brake;

    this._updateSmoke(st.t);
    this._updateCanopies(st, pos, dt);

    // the target halo fades once the rocket has topped out
    if (f.airbrakes) {
      const fade = 1 - clamp((st.t - f.apogeeTime - 2) / 3, 0, 1);
      this.targetRing.material.opacity = 0.55 * fade;
      this.targetDisc.material.opacity = 0.035 * fade;
      this.targetRing.visible = this.targetDisc.visible = fade > 0;
    }

    // flight path drawn up to now
    const i = f.index(st.t) + 1;
    this.pathPast.geometry.setDrawRange(0, i + 1);
    this.pathFuture.geometry.setDrawRange(i, f.n - i);

    // camera
    let busy = false;
    if (this.mode === "orbit") {
      busy = this.controls.update(dt) || false;
      this.key.position.copy(this.camera.position);
      this.key.target.position.copy(this.controls.target);
      this._look = this.controls.target.clone();
      if (this.controls.enableDamping) busy = true;
    } else {
      const mode = this.mode === "auto" ? this._directedMode(st.t) : this.mode;
      if (mode !== this._lastDirected) {
        if (this._lastDirected) this._startBlend();
        this._lastDirected = mode;
      }
      const pose = this._pose(mode, pos, axis, dt, snap);
      let camPos = pose.pos, look = pose.look, fov = pose.fov;
      if (this.blend) {
        const b = this.blend;
        b.t += dt;
        const k = smooth(clamp(b.t / b.duration, 0, 1));
        camPos = b.from.clone().lerp(pose.pos, k);
        look = b.look.clone().lerp(pose.look, k);
        fov = b.fov + (pose.fov - b.fov) * k;
        if (k >= 1) this.blend = null;
        busy = true;
      }
      // keep the camera above the ground
      const floor = groundHeight(camPos.x, camPos.z) + 1.2;
      if (camPos.y < floor) camPos.y = floor;
      this.camera.position.copy(camPos);
      this.camera.lookAt(look);
      this.key.position.copy(camPos);
      this.key.target.position.copy(look);
      this._look = look;
      if (Math.abs(this.camera.fov - fov) > 0.01) {
        this.camera.fov = fov;
        this.camera.updateProjectionMatrix();
      }
      this.controls.target.copy(look);
      busy = busy || (mode === "chase" && this._chaseMoving);
    }

    // glow: strong far away, gone up close where the model itself is visible
    const camDist = this.camera.position.distanceTo(pos);
    const pxPerM = this.height / (2 * Math.tan((this.camera.fov * DEG) / 2) * camDist);
    const onScreen = this.rocketLength * pxPerM;
    this.glow.material.uniforms.size.value = clamp(30 - onScreen, 0, 22) * this.renderer.getPixelRatio();
    this.glow.visible = onScreen < 30;
    const scale = this.height * this.renderer.getPixelRatio() / (2 * Math.tan((this.camera.fov * DEG) / 2));
    if (this.smoke) this.smoke.material.uniforms.scale.value = scale;
    this.flare.material.uniforms.scale.value = scale;
    this.dirty = true;
    return busy || this.plume.visible;
  }

  _updateSmoke(t) {
    const d = this.smokeData;
    if (!d) return;
    const geo = this.smoke.geometry;
    const pos = geo.attributes.position, size = geo.attributes.size, alpha = geo.attributes.alpha;
    const wind = this.windVec;
    for (let k = 0; k < d.times.length; k++) {
      const age = t - d.times[k];
      if (age <= 0) {
        alpha.array[k] = 0;
        continue;
      }
      const s = d.seeds[k];
      const grow = 1 - Math.exp(-age / 6);
      size.array[k] = 1.8 + 30 * grow * (0.75 + 0.5 * s);
      // fade in over the first fraction of a second, then dissolve slowly
      alpha.array[k] = clamp(age / 0.2, 0, 1) * Math.exp(-age / 50) * 0.62;
      const rise = Math.min(age, 60) * 0.25;
      pos.array[k * 3] = d.base[k * 3] + wind.x * age + (s - 0.5) * 4 * grow;
      pos.array[k * 3 + 1] = d.base[k * 3 + 1] + rise;
      pos.array[k * 3 + 2] = d.base[k * 3 + 2] + wind.z * age + (d.seeds[(k + 7) % d.seeds.length] - 0.5) * 4 * grow;
    }
    pos.needsUpdate = size.needsUpdate = alpha.needsUpdate = true;
  }

  _updateCanopies(st, rocketPos) {
    let lift = 0;
    for (const c of this.canopies) {
      const age = st.t - c.time;
      c.group.visible = age >= 0;
      if (!c.group.visible) continue;
      // ease open over ~1.4 s, no bounce
      const open = this.reducedMotion ? 1 : 1 - Math.pow(1 - clamp(age / 1.4, 0, 1), 3);
      const attach = rocketPos.clone();
      const height = c.cord + c.lineLen + lift;
      c.group.position.copy(attach).add(new THREE.Vector3(c.isMain ? 0 : 1.2, height * (0.35 + 0.65 * open), c.isMain ? 0 : -0.8));
      c.group.scale.set(0.15 + 0.85 * open, 0.4 + 0.6 * open, 0.15 + 0.85 * open);
      const sway = this.reducedMotion ? 0 : Math.sin((st.t + c.sway) * (2 * Math.PI / 7)) * 3.5 * DEG * open;
      c.group.rotation.set(sway, 0, sway * 0.6);
      lift = c.isMain ? lift : 0;
    }
  }

  // --- Drawing --------------------------------------------------------------------------

  resize() {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.width = r.width;
    this.height = r.height;
    this.renderer.setSize(r.width, r.height, false);
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  draw() {
    if (!this.dirty || !this.flight) return;
    if (this.themeName !== currentTheme()) this.applyTheme();
    this.renderer.render(this.scene, this.camera);
    this._placeLabels();
    this.dirty = false;
  }

  _placeLabels() {
    if (!this.labels) return;
    const v = new THREE.Vector3();
    for (const l of this.labels) {
      v.copy(l.world).project(this.camera);
      const visible = v.z < 1 && Math.abs(v.x) < 1.05 && Math.abs(v.y) < 1.05;
      l.el.style.opacity = visible ? "1" : "0";
      if (!visible) continue;
      const x = (v.x * 0.5 + 0.5) * this.width;
      const y = (-v.y * 0.5 + 0.5) * this.height;
      l.el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
    }
  }
}
