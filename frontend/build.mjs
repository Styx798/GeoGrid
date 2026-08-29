import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(here, "../src/geogrid/web/vendor");

await mkdir(target, { recursive: true });
await copyFile(resolve(here, "node_modules/konva/konva.min.js"), resolve(target, "konva.min.js"));
await copyFile(resolve(here, "node_modules/konva/LICENSE"), resolve(target, "KONVA-LICENSE.txt"));

console.log(`Konva 10.3.1 copied to ${target}`);
