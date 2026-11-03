"""Local bounding-box review for YOLO and COCO. Python 3.10+, Pillow.

Run: python label_review.py --open
All changes are explicit saves, backed up and recorded in .label_review/.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import webbrowser
from collections import defaultdict

from PIL import Image, ImageFilter, ImageOps
from label_review_statistics import calculate
from label_review_preannotation import DEFAULT_WEIGHTS, Preannotator
from label_review_privacy import PrivacyDetector
from label_review_prediction_cache import CACHE_FOLDER, PredictionCache

ROOT = Path(__file__).resolve().parent
UI = ROOT / 'label_review_ui'
STATE = ROOT / '.label_review'
PREFERENCES = STATE / 'preferences.json'
EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'}
SKIP = {'source_metadata', 'node_modules', '__pycache__', 'backups', 'cross_folder_audit_history', CACHE_FOLDER}


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def signature(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'missing'


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.review-' + secrets.token_hex(6) + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def inside(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('A dataset path points outside the selected folder.')
    return resolved


def label_path(image):
    parts = list(image.parts)
    positions = [i for i, part in enumerate(parts) if part.lower() == 'images']
    if positions:
        parts[positions[-1]] = 'labels'
        return Path(*parts).with_suffix('.txt')
    return image.with_suffix('.txt')


def class_names(root):
    """Read the common names list/dictionary without requiring PyYAML."""
    paths = [root / 'data.yaml', root / 'data.yml', root / 'source_metadata/data_local.yaml', root / 'data_local.yaml']
    paths += list(root.glob('*/data.yaml'))
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8-sig')
        match = re.search(r'^names:\s*(\[[^\n]+\])', text, re.M)
        if match:
            import ast
            try:
                names = ast.literal_eval(match[1])
                if isinstance(names, list):
                    return [str(n) for n in names]
            except (ValueError, SyntaxError):
                pass
        match = re.search(r'^names:\s*\n((?:[ \t]+[^\n]*\n?)*)', text, re.M)
        if match:
            rows = []
            for line in match[1].splitlines():
                item = re.match(r'\s*(?:(\d+)\s*:|-)\s*[\'"]?(.+?)[\'"]?\s*(?:#.*)?$', line)
                if item:
                    rows.append((int(item[1]) if item[1] else len(rows), item[2].strip(' \"\'')))
            if rows:
                return [dict(rows).get(i, f'Class {i}') for i in range(max(i for i, _ in rows) + 1)]
    return []


class Dataset:
    def __init__(self, folder, mode='auto'):
        self.root = Path(folder).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('Choose an existing dataset folder.')
        if mode not in {'auto', 'yolo', 'coco'}:
            raise ValueError('Unknown annotation format.')
        self.mode = mode
        self.key = hashlib.sha256(str(self.root).casefold().encode()).hexdigest()[:16]
        self.state = STATE / self.key
        self.docs = {}
        self.entries = []
        self.warnings = []
        self.lock = threading.RLock()
        self.history = {}
        if (self.state / 'progress.json').exists():
            self.history = json.loads((self.state / 'progress.json').read_text())
        images, jsons = [], []
        for parent, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in SKIP and not (Path(parent) / d).is_symlink()]
            for name in files:
                path = Path(parent) / name
                if path.is_symlink():
                    continue
                if path.suffix.lower() in EXT:
                    images.append(inside(path, self.root))
                elif path.suffix.lower() == '.json' and (Path(parent) == self.root or 'annotations' in [p.casefold() for p in path.parts] or 'coco' in name.lower() or name.lower().startswith('instances_')):
                    jsons.append(path)
        image_set = set(images)
        by_name = {}
        for p in images:
            by_name.setdefault(p.name.casefold(), []).append(p)
        mappings = {}
        categories = {}
        for path in sorted(jsons):
            try:
                data = json.loads(path.read_text(encoding='utf-8-sig'))
                if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ('images', 'annotations', 'categories')):
                    continue
            except (ValueError, OSError):
                continue
            path = inside(path, self.root)
            index = defaultdict(list)
            for a in data['annotations']:
                index[a['image_id']].append(a)
            self.docs[path] = {'data': data, 'digest': signature(path), 'index': index}
            for c in data['categories']:
                if c['id'] in categories and categories[c['id']] != c['name']:
                    raise ValueError('COCO files disagree on category IDs. Open one split at a time.')
                categories[c['id']] = c['name']
            missing = 0
            ids = set()
            for image in data['images']:
                if image['id'] in ids:
                    raise ValueError(f'Duplicate COCO image ID in {path.name}')
                ids.add(image['id'])
                name = Path(str(image['file_name']).replace('\\', '/'))
                candidates = [path.parent / name, self.root / name, path.parent / 'images' / name,
                              path.parent / path.stem / 'images' / name, self.root / path.stem / 'images' / name,
                              self.root / 'images' / path.stem / name, self.root / 'images' / name,
                              path.parent.parent / name, path.parent.parent / 'images' / name]
                # These are already compared with the resolved, contained inventory.
                # Lexical normalization avoids thousands of filesystem round-trips.
                resolved = list(dict.fromkeys(Path(os.path.abspath(p)) for p in candidates if Path(os.path.abspath(p)) in image_set))
                if not resolved:
                    fallback = by_name.get(name.name.casefold(), [])
                    split = path.stem.lower().replace('instances_', '').replace('2017', '').replace('2014', '')
                    preferred = [p for p in fallback if split in [x.lower() for x in p.relative_to(self.root).parts[:-1]]]
                    resolved = preferred or fallback
                if len(resolved) != 1:
                    missing += 1
                    continue
                p = resolved[0]
                mappings.setdefault(p, []).append((path, image['id']))
            if missing:
                self.warnings.append(f'{path.name}: {missing} missing or ambiguous image references were not linked.')
        # Review pages/contact sheets beside a real images/ tree are not data.
        # Keep explicitly referenced COCO images even in an otherwise mixed layout.
        in_images = lambda p: 'images' in [part.casefold() for part in p.relative_to(self.root).parts[:-1]]
        if any(in_images(p) for p in images):
            images = [p for p in images if in_images(p) or p in mappings or label_path(p).is_file()]
        self.class_file = inside(self.root / 'label_review_classes.json', self.root)
        self.class_digest = signature(self.class_file)
        class_metadata = (json.loads(self.class_file.read_text(encoding='utf-8'))
                          if self.class_file.exists() else {})
        names = class_metadata['names'] if 'names' in class_metadata else class_names(self.root)
        self.removed_classes = set(class_metadata.get('removed_ids', []))
        self.label_files = []
        self.categories = [{'id': k, 'name': v} for k, v in sorted(categories.items())]
        if not self.categories:
            self.categories = [{'id': i, 'name': n} for i, n in enumerate(names)]
        elif names:
            coco_names = {c['name']: c['id'] for c in self.categories}
            if all(n in coco_names for n in names):
                self.categories = [{'id': coco_names[n], 'name': n} for n in names]
            elif len(names) != len(self.categories):
                raise ValueError('YOLO and COCO class lists disagree. Open a single-format copy.')
        max_class = -1
        for p in sorted(images):
            label = inside(label_path(p), self.root)
            self.label_files.append(label)
            refs = mappings.get(p, [])
            if len(refs) > 1:
                raise ValueError(f'{p.name} is referenced by multiple COCO files; open a single split to avoid ambiguity.')
            raw, error = self.read_yolo(label)
            max_class = max([max_class] + [int(b['category']) for b in raw])
            doc, image_id = refs[0] if refs else (None, None)
            if doc is None and self.docs:
                related = [d for d in self.docs if d.stem.casefold() in [x.casefold() for x in p.relative_to(self.root).parts[:-1]]]
                doc = related[0] if len(related) == 1 else (next(iter(self.docs)) if len(self.docs) == 1 else None)
            source = 'yolo' if label.exists() and mode != 'coco' else ('coco' if doc else 'yolo')
            if mode == 'yolo':
                source = 'yolo'
            if mode == 'coco' and not doc:
                continue
            anns = self.docs[doc]['index'].get(image_id, []) if doc and image_id is not None else []
            annotation_names = [str(a.get('id', '')) for a in anns]
            e = {'id': str(len(self.entries)), 'path': p, 'label': label, 'doc': doc, 'image_id': image_id,
                 'source': source, 'error': error if source == 'yolo' else '',
                 'count': len(raw) if source == 'yolo' else len(anns), 'yolo_raw': raw,
                 'annotation_ids': annotation_names}
            self.entries.append(e)
        if not self.entries:
            raise ValueError('No readable dataset layout found. Choose a folder containing images (and YOLO labels or COCO JSON).')
        if max_class >= len(self.categories):
            if categories:
                raise ValueError('YOLO class index has no matching COCO category.')
            self.categories += [{'id': i, 'name': f'Class {i}'} for i in range(len(self.categories), max_class + 1)]
        if not self.categories:
            self.categories = [{'id': 0, 'name': 'pothole'}]
        if self.docs and any(e['label'].exists() for e in self.entries):
            self.warnings.append('YOLO and COCO found. Saving an image updates both linked annotation formats. Auto mode displays YOLO when present; choose COCO on opening if it is authoritative.')
        self.by_id = {e['id']: e for e in self.entries}

    @staticmethod
    def read_yolo(path):
        boxes = []
        if not path.exists():
            return boxes, ''
        try:
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                if not line.strip():
                    continue
                values = line.split()
                if len(values) != 5:
                    raise ValueError('Only YOLO detection boxes are editable; polygons/pose or malformed rows were found.')
                c, x, y, w, h = map(float, values)
                if not all(math.isfinite(v) for v in (c, x, y, w, h)) or c != int(c) or c < 0 or not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                    raise ValueError('Invalid YOLO box coordinates or class index.')
                boxes.append({'category': int(c), 'bbox': [x, y, w, h]})
            return boxes, ''
        except (ValueError, UnicodeError) as error:
            return [], str(error)

    def public(self, e):
        reviewed = self.history.get(e['path'].relative_to(self.root).as_posix(), {})
        if e['source'] == 'yolo':
            indices = {b['category'] for b in e['yolo_raw']}
            cats = [self.categories[i]['name'] for i in indices if i < len(self.categories)]
        else:
            ids = {a['category_id'] for a in self.docs[e['doc']]['index'].get(e['image_id'], [])}
            cats = [c['name'] for c in self.categories if c['id'] in ids]
        return {'id': e['id'], 'name': e['path'].name, 'relative': e['path'].relative_to(self.root).as_posix(),
                'annotation': e['label'].relative_to(self.root).as_posix() if e['source'] == 'yolo' else e['doc'].relative_to(self.root).as_posix(),
                'annotation_ids': e['annotation_ids'], 'count': e['count'], 'classes': cats, 'reviewed': bool(reviewed),
                'missing': e['source'] == 'yolo' and not e['label'].exists(), 'error': e['error'], 'format': e['source']}

    def summary(self):
        return {'folder': str(self.root), 'mode': self.mode, 'preannotation_weights': str(DEFAULT_WEIGHTS), 'prediction_cache': True,
                'categories': [c for c in self.categories if c['id'] not in self.removed_classes],
                'warnings': self.warnings,
                'images': [self.public(e) for e in self.entries], 'backup_folder': str(self.state / 'backups')}

    def revision(self, e):
        image = e['path'].stat()
        value = [str(image.st_size), str(image.st_mtime_ns), signature(e['label'])]
        if e['doc']:
            value.append(signature(e['doc']))
        return hashlib.sha256('|'.join(value).encode()).hexdigest()

    def detail(self, id):
        e = self.by_id[id]
        if e['doc'] and signature(e['doc']) != self.docs[e['doc']]['digest']:
            raise ValueError('COCO file changed outside this tool. Reopen the dataset to load the latest version.')
        raw, error = self.read_yolo(e['label'])
        with Image.open(e['path']) as im:
            width, height = im.size
            orientation = im.getexif().get(274, 1)
            if orientation not in (None, 1):
                error = 'This image has EXIF rotation. Normalize its orientation and annotations before editing.'
        boxes = []
        annotations = self.docs[e['doc']]['index'].get(e['image_id'], []) if e['doc'] else []
        if e['source'] == 'yolo':
            linked_ids = set()
            for n, b in enumerate(raw):
                x, y, w, h = b['bbox']
                box = {'id': f'y{n}', 'category': self.categories[b['category']]['id'], 'bbox': [(x-w/2)*width, (y-h/2)*height, w*width, h*height]}
                linked = next((a for a in annotations if a['id'] not in linked_ids and a['category_id'] == box['category'] and max(abs(v-z) for v,z in zip(a['bbox'],box['bbox'])) < 1), None)
                if linked:
                    box['coco_id'] = linked['id']
                    linked_ids.add(linked['id'])
                boxes.append(box)
        else:
            boxes = [{'id': str(a['id']), 'category': a['category_id'], 'bbox': a['bbox']} for a in annotations]
        mismatch = False
        if e['doc'] and e['label'].exists():
            mismatch = len(raw) != len(annotations)
            if not mismatch:
                aa = sorted([(self.categories[b['category']]['id'], *b['bbox']) for b in raw])
                bb = sorted([(a['category_id'], (a['bbox'][0]+a['bbox'][2]/2)/width, (a['bbox'][1]+a['bbox'][3]/2)/height, a['bbox'][2]/width, a['bbox'][3]/height) for a in annotations])
                mismatch = any(a[0] != b[0] or max(abs(x-y) for x,y in zip(a[1:], b[1:])) > .001 for a,b in zip(aa,bb))
        return {**self.public(e), 'width': width, 'height': height, 'boxes': boxes, 'revision': self.revision(e),
                'error': error, 'formats_disagree': mismatch,
                'saves': ['YOLO'] + (['COCO'] if e['doc'] else []), 'history': self.history.get(e['path'].relative_to(self.root).as_posix())}

    @staticmethod
    def checked_box(bbox, width, height):
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError('A box needs x, y, width and height.')
        x, y, w, h = map(float, bbox)
        if not all(math.isfinite(v) for v in (x,y,w,h)) or w <= 0 or h <= 0 or x < -.01 or y < -.01 or x+w > width+.01 or y+h > height+.01:
            raise ValueError('Boxes must be inside the image and have positive size.')
        return [max(0,x), max(0,y), min(w, width-max(0,x)), min(h, height-max(0,y))]

    def blurred(self, e, regions):
        with Image.open(e['path']) as im:
            image = im.convert('RGB')
        for region in regions:
            x,y,w,h = self.checked_box(region['bbox'], *image.size)
            method = region.get('method', 'gaussian')
            if method not in ('gaussian', 'pixelate'):
                raise ValueError('Choose Gaussian blur or pixelation.')
            radius = float(region.get('radius', 18))
            if not math.isfinite(radius) or not 1 <= radius <= 150:
                raise ValueError('Blur strength must be between 1 and 150.')
            rect = (int(x), int(y), min(image.width, math.ceil(x+w)), min(image.height, math.ceil(y+h)))
            crop = image.crop(rect)
            if method == 'pixelate':
                grid = (max(1, math.ceil(crop.width / radius)),
                        max(1, math.ceil(crop.height / radius)))
                crop = crop.resize(grid, Image.Resampling.BOX).resize(crop.size, Image.Resampling.NEAREST)
            else:
                crop = crop.filter(ImageFilter.GaussianBlur(radius))
            image.paste(crop, rect)
        return image

    def manage_class(self, payload):
        categories = copy.deepcopy(self.categories)
        removed = set(self.removed_classes)
        category_id = payload.get('id')
        removing = payload.get('action') == 'remove'
        removal = None
        if removing:
            category = next((c for c in categories if c['id'] == category_id and c['id'] not in removed), None)
            if category is None:
                raise ValueError('Unknown class.')
            handling = payload.get('handling', 'keep')
            target = None
            if handling == 'reassign':
                target = next((c for c in categories if c['id'] == payload.get('target_id')
                               and c['id'] != category_id and c['id'] not in removed), None)
                if target is None:
                    raise ValueError('Choose a different active class.')
                instruction = f"REASSIGN ALL ANNOTATIONS TO {target['id']}: {target['name']}"
            elif handling == 'delete':
                instruction = 'DELETE ALL ANNOTATIONS IN THIS CLASS'
            elif handling == 'keep':
                instruction = 'KEEP ALL ANNOTATIONS'
            else:
                raise ValueError('Choose how to handle the annotations.')
            phrase = f"REMOVE CLASS {category_id}: {category['name']} | {instruction}"
            if payload.get('confirmation') != phrase:
                raise ValueError('Type the exact confirmation phrase to remove this class.')
            if len(categories) - len(removed) <= 1:
                raise ValueError('Keep at least one active class.')
            removal = (category, target, handling)
            removed.add(category_id)
        else:
            self.update_class_name(categories, removed, category_id, payload.get('name', ''))

        return self.write_classes(categories, removed, removal)

    @staticmethod
    def update_class_name(categories, removed, category_id, name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or any(ord(c) < 32 for c in name):
            raise ValueError('Enter a class name of 1–100 characters on one line.')
        name = name.strip()
        if any(c['name'].casefold() == name.casefold() and c['id'] != category_id for c in categories):
            raise ValueError('A class with that name already exists.')
        if category_id is None:
            category_id = max(c['id'] for c in categories) + 1
            categories.append({'id': category_id, 'name': name})
        else:
            category = next((c for c in categories if c['id'] == category_id), None)
            if category is None or category_id in removed:
                raise ValueError('Unknown class.')
            category['name'] = name

    def write_classes(self, categories, removed, removal):
        writes = {self.class_file: dump({'names': [c['name'] for c in categories],
                                         'removed_ids': sorted(removed)})}
        expected = {self.class_file: self.class_digest}
        updated_docs = {}
        if removal:
            category, target, handling = removal
            source_index = categories.index(category)
            target_index = categories.index(target) if target else None
            for path in set(self.label_files):
                inside(path, self.root)
                if not path.exists():
                    continue
                original = path.read_bytes()
                boxes, error = self.read_yolo(path)
                if error or signature(path) != hashlib.sha256(original).hexdigest():
                    raise ValueError(f'Cannot verify class usage in {path.name}. Check the label file first.')
                if not any(box['category'] == source_index for box in boxes):
                    continue
                if handling == 'keep':
                    raise ValueError('This class still has annotations. Choose reassignment or deletion.')
                rows = []
                for row in original.decode('utf-8-sig').splitlines(keepends=True):
                    if row.strip() and int(float(row.split()[0])) == source_index:
                        if target is None:
                            continue
                        row = re.sub(r'^(\s*)\S+', lambda match: match[1] + str(target_index), row)
                    rows.append(row)
                writes[path] = ''.join(rows).encode('utf-8')
                expected[path] = hashlib.sha256(original).hexdigest()
        for path, doc in self.docs.items():
            expected[path] = doc['digest']
            document = dict(doc['data'])
            if removal:
                annotations = document['annotations']
                if not any(a['category_id'] == category['id'] for a in annotations):
                    continue
                if handling == 'keep':
                    raise ValueError('This class still has annotations. Choose reassignment or deletion.')
                document['annotations'] = [
                    {**a, 'category_id': target['id']} if a['category_id'] == category['id'] else a
                    for a in annotations if target is not None or a['category_id'] != category['id']
                ]
            else:
                existing = {c['id']: c for c in document['categories']}
                document['categories'] = [{**existing.get(c['id'], {}), **c} for c in categories]
            updated_docs[path] = document
            writes[path] = dump(document)
        originals = {}
        for path, digest in expected.items():
            inside(path, self.root)
            originals[path] = path.read_bytes() if path.exists() else None
            original = originals[path]
            if (hashlib.sha256(original).hexdigest() if original is not None else 'missing') != digest:
                raise ValueError('Class metadata changed on disk. Reopen the dataset.')
        backup = self.state / 'backups' / ('classes-' + secrets.token_hex(8))
        for path in writes:
            original = originals[path]
            if original is not None:
                atomic(backup / 'originals' / path.relative_to(self.root), original)
        if removal:
            atomic(backup / 'receipt.json', dump({
                'action': handling, 'class': category, 'target': target,
                'files': [{'path': str(path), 'backup': str(backup / 'originals' / path.relative_to(self.root))
                           if originals[path] is not None else None} for path in writes],
            }))
        completed = []
        try:
            for path, content in writes.items():
                atomic(path, content)
                completed.append(path)
        except Exception:
            for path in reversed(completed):
                if originals[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic(path, originals[path])
            raise
        self.class_digest = signature(self.class_file)
        for path, document in updated_docs.items():
            index = defaultdict(list)
            for annotation in document['annotations']:
                index[annotation['image_id']].append(annotation)
            self.docs[path].update(data=document, digest=signature(path), index=index)
        if removal:
            for entry in self.entries:
                if entry['label'] in writes:
                    entry['yolo_raw'], error = self.read_yolo(entry['label'])
                    if entry['source'] == 'yolo':
                        entry['error'] = error
                        entry['count'] = len(entry['yolo_raw'])
                if entry['doc'] in updated_docs:
                    annotations = self.docs[entry['doc']]['index'].get(entry['image_id'], [])
                    entry['annotation_ids'] = [str(a['id']) for a in annotations]
                    if entry['source'] == 'coco':
                        entry['count'] = len(annotations)
        self.categories = categories
        self.removed_classes = removed
        return self.summary()

    def save(self, id, payload):
        with self.lock:
            e = self.by_id[id]
            detail = self.detail(id)
            if payload.get('revision') != detail['revision']:
                raise ValueError('Files changed since this image was opened. Reload before saving; your edits were not written.')
            if detail['error']:
                raise ValueError(detail['error'])
            review_key = e['path'].relative_to(self.root).as_posix()
            if payload.get('review_only'):
                if review_key not in self.history:
                    history = dict(self.history)
                    history[review_key] = {'saved': time.strftime('%Y-%m-%d %H:%M:%S'),
                                           'boxes': len(detail['boxes']), 'blur_regions': 0}
                    atomic(self.state / 'progress.json', dump(history))
                    self.history = history
                return {'detail': self.detail(id), 'entry': self.public(e)}
            if signature(self.class_file) != self.class_digest:
                raise ValueError('Class metadata changed on disk. Reopen the dataset.')
            categories = {c['id']: n for n,c in enumerate(self.categories) if c['id'] not in self.removed_classes}
            boxes = copy.deepcopy(payload.get('boxes', []))
            if len(boxes) > 10000:
                raise ValueError('Too many boxes.')
            for box in boxes:
                if box['category'] not in categories:
                    raise ValueError('Unknown category.')
                box['bbox'] = self.checked_box(box['bbox'], detail['width'], detail['height'])
            width, height = detail['width'], detail['height']
            rows = []
            for b in boxes:
                x,y,w,h = b['bbox']
                rows.append(f"{categories[b['category']]} {(x+w/2)/width:.9f} {(y+h/2)/height:.9f} {w/width:.9f} {h/height:.9f}")
            writes = {e['label']: ('\n'.join(rows) + ('\n' if rows else '')).encode()}
            new_doc = None
            new_image_id = e['image_id']
            if e['doc']:
                new_doc = copy.deepcopy(self.docs[e['doc']]['data'])
                if new_image_id is None:
                    new_image_id = max([i['id'] for i in new_doc['images']] + [-1]) + 1
                    new_doc['images'].append({'id': new_image_id, 'file_name': os.path.relpath(e['path'], e['doc'].parent).replace('\\','/'), 'width': width, 'height': height})
                original = [a for a in new_doc['annotations'] if a['image_id'] == e['image_id']]
                protected = [a for a in original if a.get('segmentation') or a.get('keypoints')]
                if protected and boxes != detail['boxes']:
                    raise ValueError('This COCO image contains segmentation/keypoints. Box-only edits would invalidate them; use a segmentation editor.')
                next_id = max([a['id'] for a in new_doc['annotations']] + [0]) + 1
                new_annotations = []
                used = set()
                for b in boxes:
                    anchor = b['id'] if e['source'] == 'coco' else b.get('coco_id')
                    previous = next((a for a in original if str(a['id']) == str(anchor) and a['id'] not in used), None) if anchor is not None else None
                    if previous is None:
                        previous = next((a for a in original if a['id'] not in used and a['category_id'] == b['category'] and max(abs(x-y) for x,y in zip(a['bbox'], b['bbox'])) < 1), None)
                    a = copy.deepcopy(previous) if previous else {'id': next_id, 'iscrowd': 0, 'segmentation': []}
                    if not previous:
                        next_id += 1
                    used.add(a['id'])
                    a.update(image_id=new_image_id, category_id=b['category'], bbox=b['bbox'], area=b['bbox'][2]*b['bbox'][3])
                    new_annotations.append(a)
                new_doc['annotations'] = [a for a in new_doc['annotations'] if a['image_id'] != e['image_id']] + new_annotations
                writes[e['doc']] = dump(new_doc)
            regions = payload.get('blur', [])
            if regions:
                image = self.blurred(e, regions)
                buffer = io.BytesIO()
                fmt = Image.registered_extensions().get(e['path'].suffix.lower(), 'JPEG')
                image.save(buffer, format=fmt, **({'quality': 97, 'subsampling': 0} if fmt == 'JPEG' else {}))
                writes[e['path']] = buffer.getvalue()
            backup = self.state / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4))
            backup.mkdir(parents=True)
            originals = {}
            receipt = {'image': str(e['path']), 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'boxes': len(boxes), 'blur_regions': regions, 'files': []}
            for path in writes:
                inside(path, self.root)
                originals[path] = path.read_bytes() if path.exists() else None
                rel = path.relative_to(self.root)
                if originals[path] is not None:
                    atomic(backup / rel, originals[path])
                receipt['files'].append({'path': str(path), 'backup': str(backup / rel) if originals[path] is not None else None})
            atomic(backup / 'receipt.json', dump(receipt))
            completed = []
            try:
                # Recheck every source before the first write, including newly-created labels.
                if self.revision(e) != payload['revision']:
                    raise ValueError('Files changed during save. Reload the image.')
                for path, data in writes.items():
                    atomic(path, data)
                    completed.append(path)
            except Exception:
                for path in reversed(completed):
                    if originals[path] is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic(path, originals[path])
                raise
            if new_doc is not None:
                index = defaultdict(list)
                for a in new_doc['annotations']:
                    index[a['image_id']].append(a)
                self.docs[e['doc']] = {'data': new_doc, 'digest': signature(e['doc']), 'index': index}
                e['image_id'] = new_image_id
                e['annotation_ids'] = [str(a['id']) for a in new_annotations]
            e['yolo_raw'], e['error'] = self.read_yolo(e['label'])
            e['count'] = len(boxes)
            if payload.get('mark_reviewed', True) or review_key in self.history:
                self.history[review_key] = {'saved': receipt['time'], 'boxes': len(boxes), 'blur_regions': len(regions), 'backup': str(backup)}
                atomic(self.state / 'progress.json', dump(self.history))
            return {'detail': self.detail(id), 'entry': self.public(e), 'backup': str(backup)}


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.dataset = None
        self.preannotator = Preannotator()
        self.privacy_detector = PrivacyDetector()
        self.prediction_cache = PredictionCache()
        self.lock = threading.RLock()


APP = App()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, data, kind='application/json', status=200):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self, query):
        host = self.headers.get('Host', '')
        if host not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            raise ValueError('Invalid local host.')
        if self.headers.get('X-Review-Token', query.get('token', [''])[0]) != APP.token:
            raise ValueError('Reopen the tool from its launch link.')
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
            raise ValueError('Only the local review page may access this tool.')

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in {'/', '/app.js', '/styles.css'}:
                path = UI / {'/': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}[parsed.path]
                self.send(path.read_bytes(), mimetypes.guess_type(path)[0] or 'text/plain')
                return
            self.authorized(query)
            with APP.lock:
                if parsed.path == '/api/browse':
                    folder = query.get('path', [''])[0]
                    if not folder:
                        drives = [f'{letter}:\\' for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if Path(f'{letter}:\\').is_dir()] if os.name == 'nt' else ['/']
                        self.send({'path': '', 'parent': '', 'folders': drives})
                    else:
                        p = Path(folder).resolve()
                        self.send({'path': str(p), 'parent': str(p.parent) if p != p.parent else '', 'folders': sorted(str(c) for c in p.iterdir() if c.is_dir() and not c.name.startswith('.'))})
                elif parsed.path == '/api/preferences':
                    try:
                        preferences = json.loads(PREFERENCES.read_text(encoding='utf-8'))
                    except (FileNotFoundError, ValueError):
                        preferences = {}
                    self.send(preferences if isinstance(preferences, dict) else {})
                elif parsed.path == '/api/state':
                    self.send(APP.dataset.summary() if APP.dataset else {'folder': None, 'suggested': [str(Path.home()), str(Path.cwd())]})
                elif parsed.path == '/api/image':
                    self.send(APP.dataset.detail(query['id'][0]))
                elif parsed.path == '/api/statistics':
                    dataset = APP.dataset
                    if dataset is None:
                        raise ValueError('Open a dataset first.')
                    path = inside(dataset.root / 'dataset_statistics.json', dataset.root)
                    report = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
                    self.send({'report': report, 'path': str(path)})
                elif parsed.path in {'/api/pixels', '/api/thumb'}:
                    e = APP.dataset.by_id[query['id'][0]]
                    with Image.open(e['path']) as im:
                        if parsed.path == '/api/thumb':
                            im.draft('RGB', (240, 150))
                        im = ImageOps.exif_transpose(im).convert('RGB')
                        if parsed.path == '/api/thumb':
                            im.thumbnail((240, 150))
                        buffer = io.BytesIO()
                        im.save(buffer, 'JPEG', quality=86 if parsed.path == '/api/thumb' else 94)
                    self.send(buffer.getvalue(), 'image/jpeg')
                else:
                    self.send({'error': 'Not found'}, status=404)
        except (ValueError, KeyError, OSError, AttributeError) as error:
            self.send({'error': str(error)}, status=400)

    def do_POST(self):
        try:
            self.authorized({})
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size < 5_000_000:
                raise ValueError('Request too large or empty.')
            payload = json.loads(self.rfile.read(size))
            if self.path in {'/api/preannotate', '/api/privacy-suggestions'}:
                with APP.lock:
                    dataset = APP.dataset
                    if dataset is None:
                        raise ValueError('Open a dataset first.')
                    if payload.get('folder', str(dataset.root)) != str(dataset.root):
                        raise ValueError('The open dataset has changed.')
                    cache_ids = payload.get('cache_ids')
                    if cache_ids is not None:
                        if not isinstance(cache_ids, list) or len(cache_ids) > 200:
                            raise ValueError('Request at most 200 cached predictions at a time.')
                        entries = [(image_id, dataset.by_id[image_id]) for image_id in cache_ids]
                    else:
                        entry = dataset.by_id[payload['id']]
                        if payload.get('background') is not True and dataset.revision(entry) != payload.get('revision'):
                            raise ValueError('Reload the image before generating suggestions.')
                road = self.path == '/api/preannotate'
                kind = 'road' if road else 'privacy'
                detector = APP.preannotator if road else APP.privacy_detector
                if cache_ids is not None:
                    hits = {}
                    for image_id, cached_entry in entries:
                        result = APP.prediction_cache.run(
                            dataset, cached_entry, kind, {**payload, 'cache_only': True},
                            detector, atomic, inside)
                        if result is not None:
                            hits[image_id] = result
                    self.send(hits)
                    return
                self.send(APP.prediction_cache.run(dataset, entry, kind,
                                                   payload, detector, atomic, inside))
                return
            with APP.lock:
                if self.path == '/api/open':
                    dataset = Dataset(payload['folder'], payload.get('mode', 'auto'))
                    APP.dataset = dataset
                    self.send(dataset.summary())
                elif self.path == '/api/preferences':
                    if not isinstance(payload, dict) or size > 65536:
                        raise ValueError('Invalid review preferences.')
                    atomic(PREFERENCES, dump(payload))
                    self.send({'saved': True})
                elif self.path == '/api/classes':
                    self.send(APP.dataset.manage_class(payload))
                elif self.path == '/api/statistics':
                    dataset = APP.dataset
                    if dataset is None:
                        raise ValueError('Open a dataset first.')
                    if any(signature(path) != doc['digest'] for path, doc in dataset.docs.items()):
                        raise ValueError('COCO files changed on disk. Reopen the dataset.')
                    if signature(dataset.class_file) != dataset.class_digest:
                        raise ValueError('Class metadata changed on disk. Reopen the dataset.')
                    path = inside(dataset.root / 'dataset_statistics.json', dataset.root)
                    report = calculate(dataset)
                    atomic(path, dump(report))
                    self.send({'report': report, 'path': str(path)})
                elif self.path == '/api/save':
                    self.send(APP.dataset.save(payload['id'], payload))
                elif self.path == '/api/blur-preview':
                    e = APP.dataset.by_id[payload['id']]
                    image = APP.dataset.blurred(e, payload.get('blur', []))
                    buffer = io.BytesIO()
                    image.save(buffer, 'JPEG', quality=94)
                    self.send(buffer.getvalue(), 'image/jpeg')
                else:
                    self.send({'error': 'Not found'}, status=404)
        except (ValueError, KeyError, OSError, AttributeError, TypeError) as error:
            self.send({'error': str(error)}, status=400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--format', choices=['auto','yolo','coco'], default='auto')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    if args.dataset:
        APP.dataset = Dataset(args.dataset, args.format)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{server.server_port}/#token={APP.token}'
    print('Label review is running locally. Keep this window open.\n' + url, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
