# Start the complete project

Install Python 3.12 and Node.js 22 or 24. From this directory run:

```sh
npm run setup
npm run dev
```

On Windows, `START_PROJECT.cmd` performs setup if dependencies are missing and starts the complete app. Keep its window open. The site serves on port 5500. See `README.md` for MetaMask test accounts and the three-role workflow.

For a startup-only check:

```sh
python scripts/dev.py --check --no-browser
```

The check verifies blockchain, deployed contract, API, encryption, and an actual encrypted IPFS upload/download, then stops only the services it started.
