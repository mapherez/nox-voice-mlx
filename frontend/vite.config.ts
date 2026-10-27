import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    {
      name: "third-party-notices",
      generateBundle() {
        const notices = [
          ["Three.js", "./node_modules/three/LICENSE"],
          [
            "Inter Variable",
            "./node_modules/@fontsource-variable/inter/LICENSE",
          ],
        ]
          .map(
            ([name, path]) =>
              `${name}\n${"=".repeat(name.length)}\n${readFileSync(new URL(path, import.meta.url), "utf8")}`,
          )
          .join("\n\n");
        this.emitFile({
          type: "asset",
          fileName: "assets/third-party-licenses.txt",
          source: notices,
        });
      },
    },
  ],
  build: { target: "es2022", sourcemap: false },
  server: { strictPort: true },
});
