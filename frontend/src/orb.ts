import * as THREE from "three";

/** GPU particle sphere driven exclusively by the assistant playback analyser. */
export class VoiceOrb {
  private renderer: THREE.WebGLRenderer | null = null;
  private material: THREE.ShaderMaterial | null = null;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(40, 1, 0.1, 20);
  private energy = 0;
  private lastFrame = 0;
  private readonly reduced = matchMedia("(prefers-reduced-motion: reduce)");
  private readonly mobile = matchMedia("(pointer: coarse)").matches;
  private frame = 0;

  constructor(
    private canvas: HTMLCanvasElement,
    private readAssistant: () => number,
  ) {
    try {
      this.renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: true,
        antialias: false,
        powerPreference: "low-power",
      });
      this.renderer.setPixelRatio(
        Math.min(window.devicePixelRatio, this.mobile ? 1.25 : 1.5),
      );
      this.camera.position.z = 3.65;
      const count = this.mobile ? 8000 : 16000;
      const positions = new Float32Array(count * 3);
      const seeds = new Float32Array(count);
      const angle = Math.PI * (3 - Math.sqrt(5));
      for (let i = 0; i < count; i++) {
        const y = 1 - (i / (count - 1)) * 2;
        const r = Math.sqrt(1 - y * y);
        positions.set(
          [Math.cos(i * angle) * r, y, Math.sin(i * angle) * r],
          i * 3,
        );
        seeds[i] = ((i * 127.1) % 173) / 173;
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute(
        "position",
        new THREE.BufferAttribute(positions, 3),
      );
      geometry.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
      this.material = new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        blending: THREE.NormalBlending,
        uniforms: {
          uTime: { value: 0 },
          uEnergy: { value: 0 },
          uColor: { value: new THREE.Color("#38c6f6") },
          uRatio: { value: this.renderer.getPixelRatio() },
          uDark: { value: 1 },
        },
        vertexShader: `
          uniform float uTime; uniform float uEnergy; uniform float uRatio;
          attribute float aSeed; varying float vDepth; varying float vSeed; varying float vRim;
          void main() {
            vec3 p = position;
            float wave = sin(p.y * 6.0 + uTime * 0.47) * cos(p.x * 5.0 - uTime * 0.31) * sin(p.z * 5.0 + uTime * 0.28);
            float voice = sin(p.y * 11.0 + uTime * 2.1) * cos(p.x * 9.0 - uTime * 1.7);
            p *= 1.0 + wave * 0.07 + uEnergy * (0.07 + voice * 0.08);
            float a = uTime * 0.075; float c = cos(a); float s = sin(a);
            p = vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z);
            vec4 mv = modelViewMatrix * vec4(p, 1.0);
            vDepth = clamp((p.z + 1.1) / 2.2, 0.0, 1.0); vSeed = aSeed;
            vRim = pow(1.0 - abs(normalize(p).z), 4.0);
            gl_PointSize = uRatio * mix(1.15, 2.25, vDepth) * (3.3 / -mv.z);
            gl_Position = projectionMatrix * mv;
          }`,
        fragmentShader: `
          uniform vec3 uColor; uniform float uEnergy; uniform float uDark;
          varying float vDepth; varying float vSeed; varying float vRim;
          void main() {
            float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard;
            float alpha = (1.0 - smoothstep(0.10, 0.5, d)) * mix(0.2, 0.92, vDepth);
            alpha *= mix(0.8, 1.0, vSeed) * (0.9 + uEnergy * 0.1);
            vec3 color = uColor * (mix(1.0, 1.9, vDepth) + vRim * 2.0);
            gl_FragColor = vec4(color, alpha);
            #include <tonemapping_fragment>
            #include <colorspace_fragment>
          }`,
      });
      this.scene.add(new THREE.Points(geometry, this.material));
      new ResizeObserver(() => this.resize()).observe(canvas.parentElement!);
      canvas.addEventListener("webglcontextlost", (event) => {
        event.preventDefault();
        this.fallback();
      });
      this.reduced.addEventListener("change", () => {
        this.lastFrame = 0;
        this.draw(performance.now());
      });
      document.addEventListener("visibilitychange", () => {
        cancelAnimationFrame(this.frame);
        if (!document.hidden) {
          this.lastFrame = 0;
          this.draw(performance.now());
        }
      });
      this.themeChanged();
      this.resize();
      this.draw(performance.now());
    } catch {
      this.fallback();
    }
  }

  themeChanged(): void {
    if (!this.material) return;
    const styles = getComputedStyle(document.documentElement);
    this.material.uniforms.uColor.value.set(
      styles.getPropertyValue("--orb-blue").trim(),
    );
    this.material.blending =
      styles.getPropertyValue("--bg").trim() === "#0b1018"
        ? THREE.AdditiveBlending
        : THREE.NormalBlending;
    this.material.needsUpdate = true;
    this.render();
  }
  private resize(): void {
    const width = this.canvas.parentElement!.clientWidth;
    this.renderer?.setSize(width, width, false);
    this.render();
  }
  private fallback(): void {
    cancelAnimationFrame(this.frame);
    this.canvas.parentElement!.dataset.fallback = "true";
    this.renderer?.dispose();
    this.renderer = null;
  }
  private render(): void {
    if (this.renderer) this.renderer.render(this.scene, this.camera);
  }
  private draw = (now: number): void => {
    cancelAnimationFrame(this.frame);
    if (!this.renderer || document.hidden) return;
    if (now - this.lastFrame >= 1000 / (this.mobile ? 30 : 60)) {
      this.energy += (this.readAssistant() - this.energy) * 0.14;
      if (this.material) {
        this.material.uniforms.uTime.value = this.reduced.matches
          ? 0
          : now / 1000;
        this.material.uniforms.uEnergy.value = this.reduced.matches
          ? 0
          : this.energy;
      }
      this.render();
      this.lastFrame = now;
    }
    if (!this.reduced.matches) this.frame = requestAnimationFrame(this.draw);
  };
}
