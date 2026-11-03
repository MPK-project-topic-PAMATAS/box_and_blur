"""Persistent prediction cache and cooperative inference scheduling."""
import hashlib
import json
from pathlib import Path
from threading import Condition
from types import SimpleNamespace

from label_review_preannotation import DEFAULT_WEIGHTS
from label_review_privacy import MODELS


CACHE_FOLDER = 'prediction_cache'


def file_state(path):
    try:
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns]
    except FileNotFoundError:
        return None


class PredictionCache:
    def __init__(self):
        self.condition = Condition()
        self.running = False
        self.waiting = 0

    def run(self, dataset, entry, kind, payload, detector, atomic, inside):
        background = payload.get('background') is True
        image = entry['path']
        original = file_state(image)
        options = {key: payload[key] for key in ('weights', 'confidence', 'thresholds') if key in payload}
        if kind == 'privacy' and payload.get('refine_plate_edges') is True:
            options['refine_plate_edges'] = True
        if kind == 'privacy' and payload.get('detect_printed_plates') is True:
            options['detect_printed_plates'] = True
        model_state = []
        if kind == 'road':
            weights = DEFAULT_WEIGHTS if not options.get('weights') else Path(options['weights'])
            weights = weights.expanduser().resolve()
            model_state = [[str(weights), file_state(weights)]]
        else:
            model_state = [[name, file_state(MODELS / name)] for name in
                           ('dashcam.pt', 'yunet.onnx', 'plates.onnx', 'plates_s.onnx',
                            'face_ssd.prototxt', 'face_ssd.caffemodel', 'context.pt', 'heads.pt')]
        categories = [dict(category) for category in dataset.categories]
        removed = set(dataset.removed_classes)
        relative = image.relative_to(dataset.root).as_posix()
        fingerprint = json.dumps([5 if kind == 'privacy' else 1, relative, original, kind, options, model_state,
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
        if payload.get('cache_only') is True:
            return None
        with self.condition:
            if background:
                if self.running or self.waiting:
                    return {'deferred': True, 'reason': 'Waiting for inference capacity'}
            else:
                self.waiting += 1
                try:
                    self.condition.wait_for(lambda: not self.running)
                finally:
                    self.waiting -= 1
            self.running = True
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
                self.running = False
                self.condition.notify_all()
