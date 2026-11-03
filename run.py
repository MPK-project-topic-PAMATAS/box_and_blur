"""Create the local environment and launch Box & Blur."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / 'requirements.txt'
TORCH_REQUIREMENTS = ('torch==2.7.1', 'torchvision==0.22.1')


def has_nvidia():
    if platform.system() not in ('Windows', 'Linux') or not shutil.which('nvidia-smi'):
        return False
    try:
        return subprocess.run(['nvidia-smi', '-L'], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prepare(compute):
    folder = ROOT / ('.venv-cuda' if compute == 'cuda' else '.venv')
    python = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    stamp = folder / '.requirements-stamp'
    digest = hashlib.sha256(REQUIREMENTS.read_bytes() + Path(__file__).read_bytes()).hexdigest()
    if python.is_file() and stamp.is_file() and stamp.read_text() == digest:
        return python
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(folder)
    pip = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check']
    if platform.system() in ('Windows', 'Linux'):
        index = 'cu126' if compute == 'cuda' else 'cpu'
        subprocess.run([*pip, '--index-url', f'https://download.pytorch.org/whl/{index}',
                        *TORCH_REQUIREMENTS], check=True)
    subprocess.run([*pip, '-r', str(REQUIREMENTS)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    if compute == 'cuda':
        subprocess.run([str(python), '-c',
                        'import torch; assert torch.cuda.is_available(); torch.zeros(1, device="cuda")'],
                       check=True)
    stamp.write_text(digest)
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compute', choices=['auto', 'cpu', 'cuda'], default='auto')
    parser.add_argument('--setup-only', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--format', choices=['auto', 'yolo', 'coco'], default='auto')
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--road-weights', type=Path)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('Use 64-bit Python 3.12 for the pinned dependencies.')
    if platform.system() == 'Darwin' and platform.machine() != 'arm64':
        parser.error('This dependency set supports native Apple Silicon macOS.')
    if args.compute == 'cuda' and platform.system() not in ('Windows', 'Linux'):
        parser.error('CUDA mode requires Windows or Linux with an NVIDIA driver.')
    compute = ('cuda' if has_nvidia() else 'cpu') if args.compute == 'auto' else args.compute
    try:
        python = prepare(compute)
    except (OSError, subprocess.CalledProcessError) as error:
        if args.compute != 'auto' or compute != 'cuda':
            parser.exit(1, f'Dependency setup failed: {error}\n')
        print('CUDA setup failed. Trying the CPU environment.', flush=True)
        compute = 'cpu'
        try:
            python = prepare(compute)
        except (OSError, subprocess.CalledProcessError) as cpu_error:
            parser.exit(1, f'CPU dependency setup failed: {cpu_error}\n')
    print(f'Box & Blur environment ready ({compute}).', flush=True)
    if args.setup_only:
        return 0
    config_path = ROOT / '.box_and_blur_local.json'
    try:
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
    except (OSError, ValueError) as error:
        parser.exit(1, f'Cannot read local model settings: {error}\n')
    environment = dict(os.environ, BOX_AND_BLUR_COMPUTE=compute)
    for key, value in [('BOX_AND_BLUR_PRIVACY_MODELS', args.model_dir or config.get('model_dir')),
                       ('BOX_AND_BLUR_ROAD_WEIGHTS', args.road_weights or config.get('road_weights'))]:
        if value:
            environment[key] = str(Path(value).expanduser().resolve())
    command = [str(python), '-B', str(ROOT / 'label_review.py'), '--port', str(args.port), '--format', args.format]
    if args.dataset:
        command += ['--dataset', str(args.dataset.resolve())]
    if not args.no_browser:
        command.append('--open')
    try:
        return subprocess.call(command, cwd=ROOT, env=environment)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
