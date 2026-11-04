"""Local face and plate proposals from complementary detectors."""
import math
import re
import os
from pathlib import Path
from time import perf_counter

from PIL import Image
from label_review_preannotation import configure_local_ultralytics, predict_transient


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
        geometry_rank = (source in {'YOLOv9 plate', 'YOLOv9-s plate'}, score)
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
            if kind != 'plate':
                rect = [min(a, x), min(b, y), max(c, right), max(d, bottom)]
            elif previous['geometry_rank'] > geometry_rank:
                rect = previous['rect']
                geometry_rank = previous['geometry_rank']
            sources.update(previous['sources'])
            strong = strong or previous['strong']
            score = max(score, previous['confidence'])
            merged.remove(previous)
        merged.append(dict(kind=kind, rect=rect, confidence=score, sources=sorted(sources),
                           strong=strong, geometry_rank=geometry_rank))
    regions = []
    for region in merged:
        strong = region.pop('strong')
        region.pop('geometry_rank')
        region['weak'] = len(region['sources']) == 1 and not strong
        x, y, right, bottom = region.pop('rect')
        fraction, minimum = (.04, 1) if region['kind'] == 'plate' else (.12, 2)
        pad_x = max(minimum, (right-x)*fraction)
        pad_y = max(minimum, (bottom-y)*fraction)
        left, top = max(0, math.floor(x-pad_x)), max(0, math.floor(y-pad_y))
        w = min(width, math.ceil(right+pad_x)) - left
        h = min(height, math.ceil(bottom+pad_y)) - top
        region.update(bbox=[left, top, w, h], radius=min(150, max(18, round(min(w,h)*.35))))
        regions.append(region)
    return regions


def refine_plate_box(pixels, bbox):
    """Tighten a plate proposal only when its crop contains a clear outline."""
    import cv2

    x, y, w, h = bbox
    if w < 24 or h < 12:
        return bbox
    gray = cv2.cvtColor(pixels[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, .025 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        area = cv2.contourArea(polygon)
        left, top, width, height = cv2.boundingRect(polygon)
        if (area < .5*w*h or area < .65*width*height
                or not 1.15 <= width/height <= 7
                or abs(left + width/2 - w/2) > .12*w
                or abs(top + height/2 - h/2) > .12*h):
            continue
        candidates.append((area, left, top, width, height))
    if not candidates:
        return bbox
    _, left, top, width, height = max(candidates)
    pad_x, pad_y = max(1, math.ceil(.04*width)), max(1, math.ceil(.04*height))
    right = min(w, left + width + pad_x)
    bottom = min(h, top + height + pad_y)
    left, top = max(0, left-pad_x), max(0, top-pad_y)
    if (right-left)*(bottom-top) > .92*w*h:
        return bbox
    return [x+left, y+top, right-left, bottom-top]


def has_mosaic_grid(pixels, bbox):
    """Look for repeated, aligned mosaic boundaries in both directions."""
    import numpy as np

    x, y, width, height = bbox
    # The proposal includes padding. Exclude its border from the test.
    dx, dy = round(width*.12), round(height*.12)
    crop = pixels[y+dy:y+height-dy, x+dx:x+width-dx]
    if min(crop.shape[:2]) < 15:
        return False
    crop = crop.astype(np.float32)
    for axis in (0, 1):
        edges = np.abs(np.diff(crop, axis=axis)).mean(axis=2)
        strength = np.moveaxis(edges, axis, 0).mean(axis=1)
        if strength.mean() < .7:
            return False
        found = False
        for period in range(3, min(32, len(strength)//3)+1):
            phases = np.arange(len(strength)) % period
            for phase in range(period):
                on_grid = phases == phase
                peak = strength[on_grid]
                other = strength[~on_grid]
                if (peak.mean() >= max(3, 2.8*other.mean())
                        and (peak >= max(2, 1.8*other.mean())).sum() >= 3
                        and peak.sum() >= .46*strength.sum()):
                    found = True
                    break
            if found:
                break
        if not found:
            return False
    return True


def plate_looks_pixelated(pixels, bbox):
    if has_mosaic_grid(pixels, bbox):
        return True
    x, y, width, height = bbox
    # Some anonymizers mosaic only the text, leaving the plate surround intact.
    for fraction in (.5, .28):
        w, h = round(width*fraction), round(height*.52)
        if min(w, h) < 19:
            continue
        for left in (.15, .30, .45):
            for top in (0, .15, .30):
                if has_mosaic_grid(pixels, (x+round(width*left), y+round(height*top), w, h)):
                    return True
    return False


def looks_like_printed_registration(text):
    compact = re.sub(r'[\s.-]+', '', text.upper())
    return (re.fullmatch(r'[A-ZА-ЯЁ0-9]{4,12}', compact) is not None
            and sum(ch.isdigit() for ch in compact) >= 2
            and any(ch.isalpha() for ch in compact))


class PlateTextReader:
    """Recognize plate text locally and return only a readability flag."""
    profiles = (('en',), ('ru', 'en'), ('ch_sim', 'en'), ('ja', 'en'), ('ko', 'en'))

    def __init__(self):
        self.readers = {}

    def printed_regions(self, pixels, vehicle_boxes, physical_plates):
        import cv2
        import easyocr

        profile = ('ru', 'en', 'text_detection')
        if profile not in self.readers:
            self.readers[profile] = easyocr.Reader(['ru', 'en'], gpu=False, verbose=False)
        regions = []
        for x1, y1, x2, y2 in vehicle_boxes:
            x, y = max(0, math.floor(x1)), max(0, math.floor(y1))
            crop = pixels[y:math.ceil(y2), x:math.ceil(x2)]
            height, width = crop.shape[:2]
            if width < 32 or height < 24:
                continue
            scale = min(2, 768 / max(width, height))
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (round(width*scale), round(height*scale)))
            for corners, text, score in self.readers[profile].readtext(gray, detail=1, workers=0):
                if score < .35 or not looks_like_printed_registration(text):
                    continue
                xs, ys = zip(*corners)
                left = max(x, x + math.floor(min(xs)/scale) - 2)
                top = max(y, y + math.floor(min(ys)/scale) - 2)
                right = min(x+width, x + math.ceil(max(xs)/scale) + 2)
                bottom = min(y+height, y + math.ceil(max(ys)/scale) + 2)
                bbox = [left, top, right-left, bottom-top]
                if min(bbox[2:]) <= 0 or supported_by(bbox, physical_plates):
                    continue
                regions.append(dict(kind='plate', bbox=bbox, confidence=float(score), weak=True,
                                    sources=['Printed registration OCR'], radius=18,
                                    vehicle_context=True, printed_registration=True))
        return regions

    def readable(self, pixels, bbox):
        import cv2
        import easyocr

        x, y, width, height = bbox
        crop = pixels[y:y+height, x:x+width]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if height < 48:
            gray = cv2.resize(gray, (max(1, round(width*48/height)), 48),
                              interpolation=cv2.INTER_CUBIC)
        for profile in self.profiles:
            if profile not in self.readers:
                self.readers[profile] = easyocr.Reader(list(profile), gpu=False,
                                                       detector=False, verbose=False)
            for _, text, confidence in self.readers[profile].recognize(gray, detail=1, workers=0):
                count = sum(character.isalnum() for character in text)
                if (count >= 2 and confidence >= .45) or (count == 1 and confidence >= .85):
                    return True
        return False


def supported_by(bbox, boxes):
    x, y, w, h = bbox
    return any(
        max(0, min(x+w, right)-max(x, left)) *
        max(0, min(y+h, bottom)-max(y, top)) >= .5*w*h
        for left, top, right, bottom in boxes
    )


class PrivacyDetector:
    def __init__(self):
        self.dashcam = None
        self.face = None
        self.plate = {}
        self.face_ssd = None
        self.plate_text_reader = PlateTextReader()
        self.context_models = {}
        self.profile = None

    def dashcam_detections(self, image, threshold):
        configure_local_ultralytics()
        import torch
        from ultralytics import YOLO
        if self.dashcam is None:
            weights = MODELS / 'dashcam.pt'
            if not weights.is_file():
                raise FileNotFoundError('Fork checkpoint dashcam.pt is missing.')
            self.dashcam = YOLO(str(weights), task='detect')
        result = predict_transient(self.dashcam, image, imgsz=960, conf=threshold,
                                      device=0 if torch.cuda.is_available() else 'cpu',
                                      max_det=100, verbose=False)[0]
        for x1, y1, x2, y2, score, class_id in result.boxes.data.cpu().tolist():
            name = result.names[int(class_id)].lower()
            kind = 'face' if 'face' in name else 'plate' if 'plate' in name else None
            if kind:
                yield kind, (x1,y1,x2,y2), score, 'Dashcam YOLOv8'

    def face_landmarks(self, pixels, threshold):
        import cv2
        if self.face is None:
            self.face = cv2.FaceDetectorYN.create(str(MODELS / 'yunet.onnx'), '', (320,320), .5, .3, 1000)
        self.face.setInputSize((pixels.shape[1], pixels.shape[0]))
        self.face.setScoreThreshold(threshold)
        _, faces = self.face.detect(pixels)
        return faces if faces is not None else ()

    def face_detections(self, bgr, threshold):
        import cv2
        height, width = bgr.shape[:2]
        scale = min(1, 1280 / max(width, height))
        resized = cv2.resize(bgr, (max(1, round(width*scale)), max(1, round(height*scale)))) if scale < 1 else bgr
        rh, rw = resized.shape[:2]
        for row in self.face_landmarks(resized, threshold):
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

    def face_features(self, bgr, bbox):
        import cv2
        x, y, w, h = bbox
        if min(w, h) < 8:
            return False
        scale = min(320 / max(w, h), max(1, 128 / min(w, h)))
        crop = cv2.resize(bgr[y:y+h, x:x+w], (max(1, round(w*scale)), max(1, round(h*scale))))
        height, width = crop.shape[:2]

        def fits(x, y, w, h):
            return (w*h >= .08*width*height and
                    .2*width <= x+w/2 <= .8*width and .2*height <= y+h/2 <= .8*height)

        for pixels in (crop, cv2.flip(crop, 1)):
            for row in self.face_landmarks(pixels, .6):
                left, top, fw, fh = row[:4]
                landmarks = row[4:14].reshape(5, 2)
                points = {(float(px), float(py)) for px, py in landmarks
                          if left <= px <= left+fw and top <= py <= top+fh}
                if fits(left, top, fw, fh) and len(points) >= 3:
                    return True
            if self.profile is None:
                self.profile = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_profileface.xml')
            if self.profile.empty():
                raise RuntimeError('OpenCV profile cascade is missing.')
            gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
            profiles = self.profile.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
            if any(fits(*box) for box in profiles):
                return True
        return False

    def context_boxes(self, image, filename, classes):
        configure_local_ultralytics()
        import torch
        from ultralytics import YOLO
        if filename not in self.context_models:
            path = MODELS / filename
            if not path.is_file():
                raise FileNotFoundError(f'{filename} is missing.')
            self.context_models[filename] = YOLO(str(path), task='detect')
        result = predict_transient(self.context_models[filename],
            image, imgsz=960, conf=.20, classes=classes,
            device=0 if torch.cuda.is_available() else 'cpu', max_det=100, verbose=False)[0]
        return [(int(class_id), (x1, y1, x2, y2))
                for x1, y1, x2, y2, _, class_id in result.boxes.data.cpu().tolist()]

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
        regions = merge_regions(detections, *image.size, thresholds)
        has_faces = any(region['kind'] == 'face' for region in regions)
        has_plates = any(region['kind'] == 'plate' for region in regions)
        people = vehicles = heads = None
        printed_enabled = payload.get('detect_printed_plates') is True
        heavy_vehicles = []
        if regions or printed_enabled:
            classes = ([0] if has_faces else []) + (
                [2, 3, 5, 7] if has_plates else [5, 7] if printed_enabled else [])
            try:
                context = self.context_boxes(image, 'context.pt', classes)
                people = [box for class_id, box in context if class_id == 0]
                vehicles = [box for class_id, box in context if class_id != 0]
                if printed_enabled:
                    heavy_vehicles = [box for class_id, box in context if class_id in (5, 7)]
            except Exception as error:
                warnings.append(f'Person/vehicle context unavailable: {error}')
        if has_faces:
            try:
                heads = [box for _, box in self.context_boxes(image, 'heads.pt', [0])]
            except Exception as error:
                warnings.append(f'Head context unavailable: {error}')
        ocr_available = feature_check_available = True
        for region in regions:
            if region['kind'] == 'face':
                if people is not None and heads is not None:
                    region['human_context'] = supported_by(region['bbox'], people + heads)
                if feature_check_available:
                    try:
                        region['face_features'] = self.face_features(bgr, region['bbox'])
                    except Exception as error:
                        warnings.append(f'Face crop verification unavailable: {error}')
                        feature_check_available = False
                continue
            if payload.get('refine_plate_edges') is True:
                region['bbox'] = refine_plate_box(bgr, region['bbox'])
            if vehicles is not None:
                region['vehicle_context'] = supported_by(region['bbox'], vehicles)
                if not region['vehicle_context']:
                    region['confidence'] *= .65
                    region['weak'] = True
            region['already_pixelated'] = plate_looks_pixelated(bgr, region['bbox'])
            if ocr_available:
                try:
                    region['plate_text_readable'] = self.plate_text_reader.readable(bgr, region['bbox'])
                except Exception as error:
                    warnings.append(f'Plate OCR unavailable: {error}')
                    ocr_available = False
        if heavy_vehicles:
            physical_plates = [(x, y, x+w, y+h) for x, y, w, h in
                               (region['bbox'] for region in regions if region['kind'] == 'plate')]
            try:
                regions.extend(self.plate_text_reader.printed_regions(bgr, heavy_vehicles, physical_plates))
            except Exception as error:
                warnings.append(f'Printed registration OCR unavailable: {error}')
        return {'regions': sorted(regions, key=lambda region: -region['confidence']),
                'detectors': completed,
                'warnings': warnings, 'seconds': round(perf_counter()-started, 2)}
