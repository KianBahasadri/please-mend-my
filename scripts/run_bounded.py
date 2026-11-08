#!/usr/bin/env python3
"""Run one local build/check with a kernel memory cap and a free-RAM reserve."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

GIB = 1024 ** 3
ROOT = Path(__file__).resolve().parents[1]


def available_memory() -> int:
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise RuntimeError('Cannot establish available RAM; refusing an uncapped job')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-mib', type=int, default=2048)
    parser.add_argument('--reserve-gib', type=int, default=8)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('provide a command after --')
    if not 256 <= args.memory_mib <= 3072 or args.reserve_gib < 8:
        parser.error('memory cap must be 256–3072 MiB, with at least 8 GiB reserved')
    executable = shutil.which(command[0])
    if not executable:
        parser.error('command not found: ' + command[0])
    command[0] = executable
    cap = args.memory_mib * 1024 ** 2
    reserve = args.reserve_gib * GIB
    if available_memory() < reserve + cap:
        parser.error('insufficient available RAM to preserve the reserve; no job started')
    # Separate agent shells share the same lock. Only one heavy tree can run.
    with open(f'/tmp/please-mend-my-heavy-{os.getuid()}.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('another bounded build/check is running; wait for it to finish')
        unit = 'mend-bounded-' + uuid.uuid4().hex[:12]
        launch = [
            'systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
            '--service-type=exec', '--unit=' + unit,
            '--property=WorkingDirectory=' + str(Path.cwd()),
            '--property=MemoryMax=' + str(cap),
            '--property=MemoryHigh=' + str(cap * 3 // 4),
            '--property=MemorySwapMax=0', '--property=OOMPolicy=stop',
            '--property=TasksMax=128', '--property=CPUQuota=200%',
            '--setenv=GOMAXPROCS=2', '--setenv=GOMEMLIMIT=512MiB',
            '--setenv=GOFLAGS=-p=1', '--setenv=NODE_OPTIONS=--max-old-space-size=768',
            '--setenv=OMP_NUM_THREADS=1', '--setenv=OPENBLAS_NUM_THREADS=1',
            '--setenv=MKL_NUM_THREADS=1', '--setenv=NUMEXPR_NUM_THREADS=1',
            '--setenv=PATH=' + os.environ.get('PATH', '/usr/bin:/bin'),
            '--', *command,
        ]
        started = time.monotonic()
        print(f'Bounded job: {args.memory_mib} MiB max, 2 CPUs, {args.reserve_gib} GiB RAM reserve.', file=sys.stderr, flush=True)
        child = subprocess.Popen(launch)
        peak_memory = 0
        group_root = Path('/sys/fs/cgroup/user.slice') / f'user-{os.getuid()}.slice' / f'user@{os.getuid()}.service/app.slice' / (unit + '.service')
        try:
            while child.poll() is None:
                try:
                    peak_memory = max(peak_memory, int((group_root / 'memory.peak').read_text()))
                except (OSError, ValueError):
                    pass
                if available_memory() < reserve:
                    print('RAM reserve reached; stopping only this job.', file=sys.stderr, flush=True)
                    subprocess.run(['systemctl', '--user', 'stop', unit], check=False, stdout=subprocess.DEVNULL)
                    child.wait(timeout=15)
                    return 75
                time.sleep(0.5)
            return child.returncode
        except BaseException:
            subprocess.run(['systemctl', '--user', 'stop', unit], check=False, stdout=subprocess.DEVNULL)
            child.wait(timeout=15)
            raise
        finally:
            print(f'Bounded job finished in {time.monotonic() - started:.1f}s; observed peak {peak_memory/1024**2:.0f} MiB; available RAM {available_memory()/GIB:.1f} GiB.', file=sys.stderr, flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
