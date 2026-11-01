import { cpSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const portArgument = process.argv.indexOf("--port");
const port = portArgument >= 0 ? process.argv[portArgument + 1] : (process.env.PORT ?? "3000");
if (!port || !/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
  throw new Error("Port must be an integer between 1 and 65535");
}
cpSync(resolve(root, ".next", "static"), resolve(root, ".next", "standalone", ".next", "static"), {
  recursive: true,
});
const server = spawn(process.execPath, [resolve(root, ".next", "standalone", "server.js")], {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, HOSTNAME: "127.0.0.1", PORT: port },
});
process.on("SIGTERM", () => { server.kill("SIGTERM"); });
process.on("SIGINT", () => { server.kill("SIGINT"); });
server.on("exit", (code) => { process.exit(code ?? 1); });
