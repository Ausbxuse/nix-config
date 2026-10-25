import { build } from "esbuild";
import { cp, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

await mkdir("static/vendor", { recursive: true });
const common = { bundle: true, minify: true, target: ["chrome121", "firefox123", "safari17.4"], legalComments: "external", logLevel: "info" };
await build({ ...common, entryPoints: ["frontend/markdown.js"], outfile: "static/vendor/markdown.js", format: "iife", external: ["/vendor/diagram.js"] });
await build({ ...common, entryPoints: { diagram: "frontend/diagram.js" }, outdir: "static/vendor", format: "esm", splitting: true, chunkNames: "chunks/[name]-[hash]" });
await cp("node_modules/katex/dist/katex.min.css", "static/vendor/katex.min.css");
await cp("node_modules/katex/dist/fonts", "static/vendor/fonts", { recursive: true });
await mkdir("static/vendor/licenses", { recursive: true });
const packages = JSON.parse(await readFile("package-lock.json", "utf8")).packages;
const notices = [];
for (const [directory, value] of Object.entries(packages)) {
  if (!directory || value.dev) continue;
  const name = directory.split("node_modules/").at(-1);
  notices.push(`${name} ${value.version} — ${value.license || "see package license"}`);
  for (const file of await readdir(directory)) if (/^(license|copying|notice)(\.|$)/i.test(file)) {
    await cp(path.join(directory, file), `static/vendor/licenses/${name.replaceAll("/", "_")}-${file}`);
  }
}
await writeFile("static/vendor/THIRD-PARTY.txt", notices.sort().join("\n") + "\n");
