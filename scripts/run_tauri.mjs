#!/usr/bin/env node

import { spawn } from 'node:child_process';

const args = process.argv.slice(2);
const command = process.platform === 'win32' ? 'tauri.cmd' : 'tauri';
const env = { ...process.env };

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
