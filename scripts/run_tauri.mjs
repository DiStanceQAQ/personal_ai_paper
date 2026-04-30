#!/usr/bin/env node

import { existsSync, readdirSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEV_SERVER_PORT = 1420;
const ROOT_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const command = process.platform === 'win32' ? 'tauri.cmd' : 'tauri';
const env = { ...process.env };
const localPython = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python';
const pythonCommand = env.PAPER_ENGINE_PYTHON || (existsSync(localPython) ? localPython : 'python');
const binaryExtension = process.platform === 'win32' ? '.exe' : '';

if (
  args[0] === 'build' &&
  !args.includes('--ci') &&
  !args.includes('-h') &&
  !args.includes('--help')
) {
  args.push('--ci');
}

if (args[0] === 'build' && !env.CI) {
  env.CI = 'true';
}

function sidecarBuildForArgs(tauriArgs) {
  if (env.PAPER_ENGINE_SKIP_SIDECAR_BUILD === '1') {
    return null;
  }
  if (tauriArgs[0] === 'dev') {
    const target = mcpSidecarExists() ? 'desktop' : 'all';
    return {
      command: pythonCommand,
      args: ['scripts/build_sidecars.py', '--target', target],
    };
  }
  if (tauriArgs[0] === 'build') {
    return {
      command: pythonCommand,
      args: ['scripts/build_sidecars.py', '--target', 'all'],
    };
  }
  return null;
}

function hostTriple() {
  const result = spawnSync('rustc', ['--print', 'host-tuple'], {
    env,
    encoding: 'utf8',
    shell: false,
  });
  if (result.status === 0 && result.stdout.trim()) {
    return result.stdout.trim();
  }
  return null;
}

function mcpSidecarExists() {
  const triple = hostTriple();
  if (triple) {
    return existsSync(
      join(
        ROOT_DIR,
        'src-tauri',
        'binaries',
        `paper-engine-mcp-${triple}${binaryExtension}`,
      ),
    );
  }
  try {
    return readdirSync(join(ROOT_DIR, 'src-tauri', 'binaries')).some(
      (entry) =>
        entry.startsWith('paper-engine-mcp-') && entry.endsWith(binaryExtension),
    );
  } catch {
    return false;
  }
}

function listeningPids(port) {
  if (process.platform === 'win32') {
    return [];
  }
  const result = spawnSync(
    'lsof',
    ['-nP', `-tiTCP:${port}`, '-sTCP:LISTEN'],
    {
      env,
      encoding: 'utf8',
      shell: false,
    },
  );
  if (result.status !== 0 && !result.stdout.trim()) {
    return [];
  }
  return result.stdout
    .split(/\s+/)
    .map((value) => value.trim())
    .filter(Boolean);
}

function processCommand(pid) {
  if (process.platform === 'win32') {
    return '';
  }
  const result = spawnSync('ps', ['-p', pid, '-o', 'command='], {
    env,
    encoding: 'utf8',
    shell: false,
  });
  return result.status === 0 ? result.stdout.trim() : '';
}

function processRows() {
  if (process.platform === 'win32') {
    return [];
  }
  const result = spawnSync('ps', ['-axo', 'pid=,ppid=,command='], {
    env,
    encoding: 'utf8',
    shell: false,
  });
  if (result.status !== 0) {
    return [];
  }
  return result.stdout
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(/^(\d+)\s+(\d+)\s+(.+)$/);
      if (!match) {
        return null;
      }
      return { pid: match[1], ppid: match[2], command: match[3] };
    })
    .filter(Boolean);
}

function wait(milliseconds) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function isProjectViteCommand(commandText) {
  return (
    commandText.includes(ROOT_DIR) &&
    commandText.includes('frontend/node_modules/.bin/vite') &&
    commandText.includes(`--port ${DEV_SERVER_PORT}`)
  );
}

function isProjectApiSidecarCommand(commandText) {
  return (
    commandText.includes(ROOT_DIR) &&
    commandText.includes('src-tauri/target/debug/paper-engine-api')
  );
}

function isProjectWorkerSidecarCommand(commandText) {
  return (
    commandText.includes(ROOT_DIR) &&
    commandText.includes('src-tauri/target/debug/paper-engine-worker')
  );
}

function cleanupStaleProjectSidecars() {
  if (args[0] !== 'dev' || env.PAPER_ENGINE_SKIP_SIDECAR_CLEANUP === '1') {
    return;
  }
  const rows = processRows();
  const orphanParents = rows.filter(
    (row) =>
      row.ppid === '1' &&
      (isProjectApiSidecarCommand(row.command) ||
        isProjectWorkerSidecarCommand(row.command)),
  );
  if (orphanParents.length === 0) {
    return;
  }

  const orphanParentPids = new Set(orphanParents.map((row) => row.pid));
  const staleRows = [
    ...rows.filter(
      (row) =>
        orphanParentPids.has(row.ppid) &&
        (isProjectApiSidecarCommand(row.command) ||
          isProjectWorkerSidecarCommand(row.command)),
    ),
    ...orphanParents,
  ];

  for (const row of staleRows) {
    console.log(`Stopping stale backend sidecar (PID ${row.pid})`);
    try {
      process.kill(Number(row.pid), 'SIGTERM');
    } catch (error) {
      console.warn(`Unable to stop PID ${row.pid}: ${error.message}`);
    }
  }
  wait(500);
}

function ensureDevPortAvailable() {
  if (args[0] !== 'dev' || env.PAPER_ENGINE_SKIP_PORT_CLEANUP === '1') {
    return;
  }
  const pids = listeningPids(DEV_SERVER_PORT);
  for (const pid of pids) {
    const commandText = processCommand(pid);
    if (!isProjectViteCommand(commandText)) {
      console.error(
        `Port ${DEV_SERVER_PORT} is already in use by PID ${pid}: ${commandText}`,
      );
      console.error(
        'Set PAPER_ENGINE_SKIP_PORT_CLEANUP=1 to bypass this check, or stop that process manually.',
      );
      process.exit(1);
    }
    console.log(`Stopping stale Vite dev server on port ${DEV_SERVER_PORT} (PID ${pid})`);
    try {
      process.kill(Number(pid), 'SIGTERM');
    } catch (error) {
      console.warn(`Unable to stop PID ${pid}: ${error.message}`);
    }
  }
  if (pids.length > 0) {
    wait(500);
  }
}

function pdfAdvancedInstallForArgs(tauriArgs) {
  if (tauriArgs[0] === 'dev' || tauriArgs[0] === 'build') {
    return {
      command: pythonCommand,
      args: ['scripts/ensure_pdf_advanced.py', '--if-missing'],
    };
  }
  return null;
}

function doclingModelDownloadForArgs(tauriArgs) {
  if (tauriArgs[0] === 'dev' || tauriArgs[0] === 'build') {
    return {
      command: pythonCommand,
      args: ['scripts/ensure_docling_models.py', '--if-missing'],
    };
  }
  return null;
}

function modelDownloadForArgs(tauriArgs) {
  if (env.PAPER_ENGINE_SKIP_MODEL_DOWNLOAD === '1') {
    return null;
  }
  if (tauriArgs[0] === 'dev' || tauriArgs[0] === 'build') {
    return {
      command: pythonCommand,
      args: ['scripts/download_embedding_model.py', '--if-missing'],
    };
  }
  return null;
}

const pdfAdvancedInstall = pdfAdvancedInstallForArgs(args);
const doclingModelDownload = doclingModelDownloadForArgs(args);
const modelDownload = modelDownloadForArgs(args);
const sidecarBuild = sidecarBuildForArgs(args);

if (env.PAPER_ENGINE_TAURI_DRY_RUN === '1') {
  console.log(JSON.stringify({
    pdfAdvancedInstall,
    doclingModelDownload,
    modelDownload,
    sidecarBuild,
    tauri: {
      command,
      args,
    },
  }));
  process.exit(0);
}

cleanupStaleProjectSidecars();
ensureDevPortAvailable();

if (pdfAdvancedInstall) {
  const result = spawnSync(pdfAdvancedInstall.command, pdfAdvancedInstall.args, {
    env,
    stdio: 'inherit',
    shell: false,
  });

  if (result.error) {
    console.error(`Failed to prepare Docling dependency: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (doclingModelDownload) {
  const result = spawnSync(doclingModelDownload.command, doclingModelDownload.args, {
    env,
    stdio: 'inherit',
    shell: false,
  });

  if (result.error) {
    console.error(`Failed to prepare Docling models: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (modelDownload) {
  const result = spawnSync(modelDownload.command, modelDownload.args, {
    env,
    stdio: 'inherit',
    shell: false,
  });

  if (result.error) {
    console.error(`Failed to prepare embedding model: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (sidecarBuild) {
  const result = spawnSync(sidecarBuild.command, sidecarBuild.args, {
    env,
    stdio: 'inherit',
    shell: false,
  });

  if (result.error) {
    console.error(`Failed to build backend sidecars: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

const child = spawn(command, args, {
  env,
  stdio: 'inherit',
  shell: false,
});

child.on('error', (error) => {
  console.error(`Failed to run Tauri CLI: ${error.message}`);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  if (signal) {
    console.error(`Tauri CLI exited from signal ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
