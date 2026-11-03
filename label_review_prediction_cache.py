"""Persistent prediction cache and cooperative inference scheduling."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from threading import Condition
from time import monotonic
from types import SimpleNamespace

from label_review_preannotation import DEFAULT_WEIGHTS
from label_review_privacy import MODELS


CACHE_FOLDER = 'prediction_cache'
LOAD_LIMIT = 70


def file_state(path):
    try:
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns]
    except FileNotFoundError:
        return None


def system_has_headroom():
    try:
        import psutil
    except ImportError:
        return False
    if psutil.cpu_percent(interval=.12) >= LOAD_LIMIT or psutil.virtual_memory().percent >= LOAD_LIMIT:
        return False
    if os.environ.get('BOX_AND_BLUR_COMPUTE') == 'cpu':
        return True
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, check=True, timeout=2,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        gpu, used, total = map(float, result.stdout.splitlines()[0].split(','))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        # Without reliable GPU telemetry, leave inference to foreground requests.
        return False
    return gpu < LOAD_LIMIT and total > 0 and 100 * used / total < LOAD_LIMIT


class PredictionCache:
    def __init__(self):
        self.condition = Condition()
        self.running = False
        self.waiting = 0
        self.next_background = 0

    def run(self, dataset, entry, kind, payload, detector, atomic, inside):
        background = payload.get('background') is True
        image = entry['path']
        original = file_state(image)
        options = {key: payload[key] for key in ('weights', 'confidence', 'thresholds') if key in payload}
        model_state = []
        if kind == 'road':
            weights = DEFAULT_WEIGHTS if not options.get('weights') else Path(options['weights'])
            weights = weights.expanduser().resolve()
            model_state = [[str(weights), file_state(weights)]]
        else:
            model_state = [[name, file_state(MODELS / name)] for name in
                           ('dashcam.pt', 'yunet.onnx', 'plates.onnx', 'plates_s.onnx',
                            'face_ssd.prototxt', 'face_ssd.caffemodel')]
        categories = [dict(category) for category in dataset.categories]
        removed = set(dataset.removed_classes)
        relative = image.relative_to(dataset.root).as_posix()
        fingerprint = json.dumps([1, relative, original, kind, options, model_state,
                                  categories if kind == 'road' else None,
                                  sorted(removed) if kind == 'road' else None], sort_keys=True)
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()
        path = inside(dataset.root / CACHE_FOLDER / kind / (digest + '.json'), dataset.root)

        def read_cache():
            try:
                return json.loads(path.read_text(encoding='utf-8'))['result']
            except (OSError, ValueError, KeyError):
                return None

        cached = read_cache()
        if cached is not None:
            return {**cached, 'cached': True}
        if background:
            with self.condition:
                if self.running or self.waiting or monotonic() < self.next_background:
                    return {'deferred': True}
            if not system_has_headroom():
                return {'deferred': True}
        with self.condition:
            if background:
                if self.running or self.waiting or monotonic() < self.next_background:
                    return {'deferred': True}
            else:
                self.waiting += 1
                try:
                    self.condition.wait_for(lambda: not self.running)
                finally:
                    self.waiting -= 1
            self.running = True
        started = monotonic()
        try:
            cached = read_cache()
            if cached is not None:
                return {**cached, 'cached': True}
            if file_state(image) != original:
                raise ValueError('The image changed while waiting for inference.')
            snapshot = SimpleNamespace(by_id={payload['id']: entry}, revision=lambda _: 'snapshot',
                                       categories=categories, removed_classes=removed)
            result = detector.predict(snapshot, {**options, 'id': payload['id'], 'revision': 'snapshot'})
            if file_state(image) != original:
                raise ValueError('The image changed during inference. Reload it before reviewing suggestions.')
            if not result.get('warnings'):
                record = {'image': relative, 'kind': kind, 'settings': options,
                          'fingerprint': digest, 'result': result}
                atomic(path, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            return result
        finally:
            with self.condition:
                if background:
                    self.next_background = monotonic() + max(.15, (monotonic() - started) * 3 / 7)
                self.running = False
                self.condition.notify_all()
