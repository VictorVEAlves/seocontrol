#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "..");

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: "inherit",
    shell: false,
    env: {
      ...process.env,
      PIP_DISABLE_PIP_VERSION_CHECK: process.env.PIP_DISABLE_PIP_VERSION_CHECK || "1"
    }
  });

  if (result.error) {
    console.error(`Failed to run ${command}: ${result.error.message}`);
    process.exit(1);
  }

  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function probePython(candidate) {
  const result = spawnSync(
    candidate.command,
    [...candidate.args, "-c", "import sys; print(sys.executable)"],
    {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      shell: false
    }
  );

  if (result.status !== 0) {
    return null;
  }

  const executable = String(result.stdout || "").trim();
  return executable ? { command: executable, args: [] } : candidate;
}

function findPython() {
  if (process.env.PYTHON_BIN) {
    return { command: process.env.PYTHON_BIN, args: [] };
  }

  const candidates = process.platform === "win32"
    ? [
        { command: "python", args: [] },
        { command: "py", args: ["-3"] },
        { command: "python3", args: [] }
      ]
    : [
        { command: "python3", args: [] },
        { command: "python", args: [] }
      ];

  for (const candidate of candidates) {
    const python = probePython(candidate);
    if (python) {
      return python;
    }
  }

  console.error("Python 3 was not found. Install Python 3 or set PYTHON_BIN.");
  process.exit(1);
}

const python = findPython();

function runPython(args) {
  run(python.command, [...python.args, ...args]);
}

function install() {
  runPython(["-m", "pip", "install", "-r", "requirements.txt"]);
}

function build() {
  install();
  runPython([
    "-m",
    "compileall",
    "-q",
    "app.py",
    "config.py",
    "run.py",
    "modules",
    "collectors",
    "analyzers",
    "actions",
    "dashboard"
  ]);
}

function start() {
  runPython(["app.py"]);
}

const action = process.argv[2];

if (action === "install") {
  install();
} else if (action === "build") {
  build();
} else if (action === "start") {
  start();
} else {
  console.error("Usage: node scripts/npm-python.js <install|build|start>");
  process.exit(1);
}
