# Third-party notices

- The original PersonaPlex MLX port is retained under the root MIT license and
  Git history. Original inference/model code is moved into `mac/engine/`.
- NVIDIA PersonaPlex reference code is a pinned submodule at
  `vendor/personaplex/`, with its own license files. It is excluded from the
  production webapp image. Model weights are separately subject to the NVIDIA
  model access terms/license on Hugging Face.
- Three.js is distributed under its MIT license through the frontend dependency.
- React, React DOM and Scheduler are distributed under their MIT licenses in
  the compiled frontend. Their notices, together with Three.js and Inter, are
  emitted as `assets/third-party-licenses.txt` in the frontend build.
- Vite+ and the React build/test tools are development dependencies. Their
  executables and Node.js are not included in the deployed Python runtime.
- Inter Variable is distributed by `@fontsource-variable/inter` under SIL OFL
  1.1; the font and license are included with that dependency. Fonts are served
  locally, not requested from a font CDN at runtime.
- The sphere is procedural rendering implemented in the frontend. No external
  reference artwork is redistributed as an application asset.
