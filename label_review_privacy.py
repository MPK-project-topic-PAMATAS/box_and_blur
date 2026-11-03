"""Local face and plate proposals from complementary detectors."""
import math
import os
from pathlib import Path
from time import perf_counter

from PIL import Image
from label_review_preannotation import configure_local_ultralytics


MODELS = Path(os.environ.get(
    'BOX_AND_BLUR_PRIVACY_MODELS', str(Path(__file__).resolve().parent / 'models' / 'privacy')
)).expanduser()
DEFAULT_THRESHOLDS = {
    'Dashcam YOLOv8': .15, 'YuNet': .50, 'OpenCV SSD face': .35,
    'YOLOv9 plate': .25, 'YOLOv9-s plate': .25,
}


def merge_regions(detections, width, height, thresholds):
    merged = []
    for kind, coordinates, score, source in sorted(detections, key=lambda item: -item[2]):
        if not all(math.isfinite(value) for value in (*coordinates, score)):
            continue
        x1, y1, x2, y2 = coordinates
        rect = [max(0, x1), max(0, y1), min(width, x2), min(height, y2)]
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            continue
        sources = {source}
        strong = score >= min(.95, thresholds[source] + .15)
        for previous in merged[:]:
            if previous['kind'] != kind:
                continue
            a, b, c, d = previous['rect']
            x, y, right, bottom = rect
            overlap = max(0, min(c, right)-max(a, x)) * max(0, min(d, bottom)-max(b, y))
            if overlap < .55 * min((c-a)*(d-b), (right-x)*(bottom-y)):
                continue
            rect = [min(a, x), min(b, y), max(c, right), max(d, bottom)]
            sources.update(previous['sources'])
            strong = strong or previous['strong']
            score = max(score, previous['confidence'])
            merged.remove(previous)
        merged.append(dict(kind=kind, rect=rect, confidence=score, sources=sorted(sources), strong=strong))
    regions = []
    for region in merged:
        strong = region.pop('strong')
        region['weak'] = len(region['sources']) == 1 and not strong
        x, y, right, bottom = region.pop('rect')
        pad_x = max(2, (right-x)*.12)
        pad_y = max(2, (bottom-y)*.12)
        left, top = max(0, math.floor(x-pad_x)), max(0, math.floor(y-pad_y))
        w = min(width, math.ceil(right+pad_x)) - left
        h = min(height, math.ceil(bottom+pad_y)) - top
        region.update(bbox=[left, top, w, h], radius=min(150, max(18, round(min(w,h)*.35))))
        regions.append(region)
    return regions


class PrivacyDetector:
    def __init__(self):
        self.dashcam = None
        self.face = None
        self.plate = {}
        self.face_ssd = None

    def dashcam_detections(self, image, threshold):
        configure_local_ultralytics()
        import torch
        from ultralytics import YOLO
        if self.dashcam is None:
            weights = MODELS / 'dashcam.pt'
            if not weights.is_file():
                raise FileNotFoundError('Fork checkpoint dashcam.pt is missing.')
            self.dashcam = YOLO(str(weights), task='detect')
        result = self.dashcam.predict(image, imgsz=960, conf=threshold,
                                      device=0 if torch.cuda.is_available() else 'cpu',
                                      max_det=100, verbose=False)[0]
        for x1, y1, x2, y2, score, class_id in result.boxes.data.cpu().tolist():
            name = result.names[int(class_id)].lower()
            kind = 'face' if 'face' in name else 'plate' if 'plate' in name else None
            if kind:
                yield kind, (x1,y1,x2,y2), score, 'Dashcam YOLOv8'

    def face_detections(self, bgr, threshold):
        import cv2
        if self.face is None:
            self.face = cv2.FaceDetectorYN.create(str(MODELS / 'yunet.onnx'), '', (320,320), .5, .3, 1000)
        height, width = bgr.shape[:2]
        scale = min(1, 1280 / max(width, height))
        resized = cv2.resize(bgr, (max(1, round(width*scale)), max(1, round(height*scale)))) if scale < 1 else bgr
        rh, rw = resized.shape[:2]
        self.face.setInputSize((rw, rh))
        self.face.setScoreThreshold(threshold)
        _, faces = self.face.detect(resized)
        for row in faces if faces is not None else ():
            x,y,w,h = map(float, row[:4])
            yield 'face', (x*width/rw,y*height/rh,(x+w)*width/rw,(y+h)*height/rh), float(row[-1]), 'YuNet'

    def ssd_face_detections(self, bgr, threshold):
        import cv2
        if self.face_ssd is None:
            self.face_ssd = cv2.dnn.readNetFromCaffe(
                str(MODELS / 'face_ssd.prototxt'), str(MODELS / 'face_ssd.caffemodel'))
        height, width = bgr.shape[:2]
        crops = [(0, 0, width, height)]
        if min(width, height) >= 1000:
            crops.extend((x, y, right, bottom)
                         for x, right in ((0, int(width*.6)), (int(width*.4), width))
                         for y, bottom in ((0, int(height*.6)), (int(height*.4), height)))
        for left, top, right, bottom in crops:
            pixels = bgr[top:bottom, left:right]
            self.face_ssd.setInput(cv2.dnn.blobFromImage(
                pixels, size=(300,300), mean=(104,177,123)))
            for row in self.face_ssd.forward().reshape(-1, 7):
                score = float(row[2])
                if score < threshold:
                    continue
                x1, y1, x2, y2 = map(float, row[3:])
                w, h = right-left, bottom-top
                yield 'face', (left+x1*w,top+y1*h,left+x2*w,top+y2*h), score, 'OpenCV SSD face'

    def plate_detections(self, bgr, threshold, filename='plates.onnx', name='YOLOv9 plate'):
        if filename not in self.plate:
            import onnxruntime
            from open_image_models import create_detector
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            self.plate[filename] = create_detector(MODELS / filename, backend='yolo_v9',
                                                   class_labels=['plate'], conf_thresh=threshold,
                                                   providers=['CPUExecutionProvider'], sess_options=options)
        model = self.plate[filename]
        model.conf_thresh = threshold
        for result in model.predict(bgr):
            box = result.bounding_box
            yield 'plate', (box.x1,box.y1,box.x2,box.y2), float(result.confidence), name

    def predict(self, dataset, payload):
        import numpy as np
        requested = payload.get('thresholds', {})
        thresholds = {name: float(requested.get(name, default)) for name, default in DEFAULT_THRESHOLDS.items()}
        if not all(math.isfinite(value) and .01 <= value <= .99 for value in thresholds.values()):
            raise ValueError('Confidence thresholds must be between 1% and 99%.')
        entry = dataset.by_id[payload['id']]
        if dataset.revision(entry) != payload.get('revision'):
            raise ValueError('Reload the image before generating blur suggestions.')
        with Image.open(entry['path']) as source:
            if source.getexif().get(274, 1) not in (None, 1):
                raise ValueError('Normalize the image’s EXIF orientation before suggesting blur.')
            image = source.convert('RGB')
        bgr = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
        started = perf_counter()
        detections, warnings, completed = [], [], []
        for name, detector, pixels in (
            ('Dashcam YOLOv8', self.dashcam_detections, image),
            ('YuNet', self.face_detections, bgr),
            ('OpenCV SSD face', self.ssd_face_detections, bgr),
            ('YOLOv9 plate', self.plate_detections, bgr),
            ('YOLOv9-s plate', lambda pixels, threshold:
                self.plate_detections(pixels, threshold, 'plates_s.onnx', 'YOLOv9-s plate'), bgr),
        ):
            try:
                detections.extend(list(detector(pixels, thresholds[name])))
                completed.append(name)
            except Exception as error:
                warnings.append(f'{name}: {error}')
        return {'regions': merge_regions(detections, *image.size, thresholds),
                'detectors': completed,
                'warnings': warnings, 'seconds': round(perf_counter()-started, 2)}
