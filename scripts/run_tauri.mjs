#!/usr/bin/env node

import { existsSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';

const args = process.argv.slice(2);
const command = process.platform === 'win32' ? 'tauri.cmd' : 'tauri';
const env = { ...process.env };
const localPython = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python';
const pythonCommand = env.PAPER_ENGINE_PYTHON || (existsSync(localPython) ? localPython : 'python');

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
    return {
      command: pythonCommand,
      args: ['scripts/build_sidecars.py', '--target', 'api'],
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

const sidecarBuild = sidecarBuildForArgs(args);

if (env.PAPER_ENGINE_TAURI_DRY_RUN === '1') {
  console.log(JSON.stringify({
    sidecarBuild,
    tauri: {
      command,
      args,
    },
  }));
  process.exit(0);
}

if (sidecarBuild) {
  const result = spawnSync(sidecarBuild.command, sidecarBuild.args, {
    env,
    stdio: 'inherit',
    shell: false,
  });

  if (result.error) {
    console.error(`Failed to build API sidecar: ${result.error.message}`);
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
