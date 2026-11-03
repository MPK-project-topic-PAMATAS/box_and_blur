"""On-demand suggestions from the project's local Ultralytics detector."""
import math
import os
from pathlib import Path
import re
from time import perf_counter

from PIL import Image


DEFAULT_WEIGHTS = Path(os.environ.get(
    'BOX_AND_BLUR_ROAD_WEIGHTS', str(Path(__file__).resolve().parent / 'models' / 'road.pt')
)).expanduser()


def configure_local_ultralytics():
    config_dir = Path(__file__).resolve().parent / '.label_review' / 'ultralytics'
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('YOLO_CONFIG_DIR', str(config_dir))
    os.environ.setdefault('YOLO_OFFLINE', 'true')
    os.environ.setdefault('YOLO_AUTOINSTALL', 'false')


def class_key(name):
    name = re.sub(r'^\d+[\s:._-]+', '', str(name).casefold())
    key = re.sub(r'[^a-z0-9]', '', name)
    return {'speedreducingbump': 'speedbump', 'manholecover': 'manhole'}.get(key, key)


class Preannotator:
    def __init__(self):
        self.model = None
        self.model_key = None

    def predict(self, dataset, payload):
        entry = dataset.by_id[payload['id']]
        if dataset.revision(entry) != payload.get('revision'):
            raise ValueError('This image changed on disk. Reopen it before running the model.')
        weights = Path(payload.get('weights') or DEFAULT_WEIGHTS).expanduser().resolve()
        if weights.suffix.lower() != '.pt' or not weights.is_file():
            raise ValueError('Select a local Ultralytics detector checkpoint (.pt).')
        confidence = float(payload.get('confidence', .25))
        if not math.isfinite(confidence) or not .01 <= confidence <= .99:
            raise ValueError('Confidence must be between 1% and 99%.')
        configure_local_ultralytics()
        try:
            import torch
            from ultralytics import YOLO
        except ImportError as error:
            raise ValueError('Install Ultralytics in the review tool’s Python environment.') from error

        device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
        started = perf_counter()
        stat = weights.stat()
        model_key = (weights, stat.st_mtime_ns, stat.st_size)
        if self.model_key != model_key:
            self.model = None
            self.model_key = None
            try:
                self.model = YOLO(str(weights), task='detect')
            except Exception as error:
                raise ValueError(f'Could not load this detector checkpoint: {error}') from error
            self.model_key = model_key
        with Image.open(entry['path']) as source:
            if source.getexif().get(274, 1) not in (None, 1):
                raise ValueError('Normalize this image’s EXIF orientation before preannotation.')
            image = source.convert('RGB')
        try:
            result = self.model.predict(image, imgsz=640, conf=confidence, device=device,
                                        half=False, max_det=100, verbose=False)[0]
        except RuntimeError as error:
            raise ValueError(f'Model inference failed on {device}: {error}') from error
        if result.boxes is None:
            raise ValueError('This checkpoint did not return detection boxes.')
        width, height = image.size
        categories = {}
        for category in dataset.categories:
            if category['id'] not in dataset.removed_classes:
                categories.setdefault(class_key(category['name']), []).append(category['id'])
        predictions = []
        for x1, y1, x2, y2, score, model_class in result.boxes.data.cpu().tolist():
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2, score, model_class)):
                continue
            x1, x2 = max(0, x1), min(width, x2)
            y1, y2 = max(0, y1), min(height, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            name = str(result.names[int(model_class)])
            matches = categories.get(class_key(name), [])
            predictions.append({'name': name, 'category': matches[0] if len(matches) == 1 else None,
                                'confidence': score, 'bbox': [x1, y1, x2-x1, y2-y1]})
        return {'predictions': predictions, 'weights': weights.name,
                'device': torch.cuda.get_device_name(0) if device != 'cpu' else 'CPU',
                'seconds': round(perf_counter() - started, 2)}
