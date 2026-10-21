const fs = require("node:fs");
const path = require("node:path");
const { JsonRpcProvider, ContractFactory, Contract } = require("ethers");

async function main() {
  const root = path.resolve(__dirname, "..");
  const artifact = JSON.parse(fs.readFileSync(path.join(root, "artifacts/HealthRecords.json")));
  const provider = new JsonRpcProvider("http://127.0.0.1:8545");
  if ((await provider.getNetwork()).chainId !== 31337n) throw new Error("Deployment only supports the local development chain (31337).");
  const signer = await provider.getSigner(0);
  const genesis = (await provider.getBlock(0)).hash;
  const deploymentPath = path.join(root, ".local/deployment.json");
  let saved;
  try { saved = JSON.parse(fs.readFileSync(deploymentPath)); } catch (error) { if (error.code !== "ENOENT") throw error; }
  if (saved?.genesis === genesis && await provider.getCode(saved.address) === artifact.deployedBytecode) {
    const contract = new Contract(saved.address, artifact.abi, provider);
    await contract.admin();
    console.log("Using existing HealthRecords deployment: " + saved.address);
    return;
  }
  const contract = await new ContractFactory(artifact.abi, artifact.bytecode, signer).deploy();
  await contract.waitForDeployment();
  const deployment = { address: await contract.getAddress(), admin: await contract.admin(), chainId: 31337, genesis };
  fs.mkdirSync(path.dirname(deploymentPath), { recursive: true });
  fs.writeFileSync(deploymentPath, JSON.stringify(deployment, null, 2) + "\n");
  console.log("HealthRecords deployed: " + deployment.address);
  console.log("Administrator wallet: " + deployment.admin);
}
main().catch((error) => { console.error(error.message); process.exitCode = 1; });
