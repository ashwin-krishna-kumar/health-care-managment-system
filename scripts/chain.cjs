const path = require("node:path");
const { spawn } = require("node:child_process");
const root = path.resolve(__dirname, "..");
const child = spawn(process.execPath, [path.join(root, "node_modules/hardhat/internal/cli/cli.js"), "node", "--hostname", "127.0.0.1"], {
  cwd: root, stdio: "inherit", env: { ...process.env,
    XDG_CACHE_HOME: path.join(root, ".cache"), XDG_CONFIG_HOME: path.join(root, ".local/config"),
    XDG_DATA_HOME: path.join(root, ".local/share"), HARDHAT_DISABLE_TELEMETRY_PROMPT: "true" },
});
child.on("exit", (code) => { process.exitCode = code || 0; });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
