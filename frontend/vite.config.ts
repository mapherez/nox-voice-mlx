import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { defineConfig, lazyPlugins } from "vite-plus";

export default defineConfig({
  lint: {
    ignorePatterns: ["node_modules/**", "dist/**", ".vite/**", ".vitest/**"],
    plugins: ["typescript", "unicorn", "oxc", "react", "jsx-a11y"],
    jsPlugins: [{ name: "vite-plus", specifier: "vite-plus/oxlint-plugin" }],
    rules: {
      // Composite canvas images and live paragraphs intentionally use ARIA roles.
      "jsx-a11y/prefer-tag-over-role": "off",
      "vite-plus/prefer-vite-plus-imports": "error",
      "react/rules-of-hooks": "error",
      "react/exhaustive-deps": "error",
      "react/jsx-key": "error",
      "react/button-has-type": "error",
      "jsx-a11y/label-has-associated-control": ["error", { assert: "either" }],
    },
    options: { typeAware: true, typeCheck: true },
  },
  fmt: {
    printWidth: 80,
    bracketSameLine: true,
    sortPackageJson: false,
    overrides: [
      {
        files: ["*.html"],
        options: {
          htmlWhitespaceSensitivity: "ignore",
          bracketSameLine: true,
        },
      },
    ],
    ignorePatterns: ["dist/**", "node_modules/**", ".vite/**", ".vitest/**"],
  },
  plugins: lazyPlugins(() => [
    react(),
    {
      name: "third-party-notices",
      generateBundle() {
        const notices = [
          ["React", "./node_modules/react/LICENSE"],
          ["React DOM", "./node_modules/react-dom/LICENSE"],
          ["Scheduler", "./node_modules/scheduler/LICENSE"],
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
  ]),
  build: { target: "es2022", sourcemap: false },
  server: { strictPort: true },
});
