const fs = require("node:fs");
const path = require("node:path");
const solc = require("solc");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "contracts/HealthRecords.sol"), "utf8");
const output = JSON.parse(solc.compile(JSON.stringify({
  language: "Solidity",
  sources: { "HealthRecords.sol": { content: source } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: "cancun",
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } },
  },
})));
for (const error of output.errors || []) console.error(error.formattedMessage);
if (output.errors?.some((error) => error.severity === "error")) process.exit(1);
const compiled = output.contracts["HealthRecords.sol"].HealthRecords;
fs.mkdirSync(path.join(root, "artifacts"), { recursive: true });
fs.writeFileSync(path.join(root, "artifacts/HealthRecords.json"), JSON.stringify({
  compiler: solc.version(), abi: compiled.abi, bytecode: "0x" + compiled.evm.bytecode.object,
  deployedBytecode: "0x" + compiled.evm.deployedBytecode.object,
}, null, 2) + "\n");
console.log("HealthRecords compiled successfully with Solidity " + solc.version());
