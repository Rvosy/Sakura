import { build } from "esbuild";
await build({ entryPoints: ["components.jsx"], bundle: true, outdir: "dist", format: "esm", jsx: "automatic", minify: true, define: { "process.env.NODE_ENV": '"production"' }, logLevel: "info" });
