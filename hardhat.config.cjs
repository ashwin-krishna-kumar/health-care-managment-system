// Compilation uses the pinned npm solc package; no external compiler download.
module.exports = {
  solidity: "0.8.36",
  networks: { hardhat: { chainId: 31337, hardfork: "cancun" } },
};
