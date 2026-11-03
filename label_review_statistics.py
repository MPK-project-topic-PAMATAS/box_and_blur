"""Descriptive statistics for the saved annotations in an open review dataset."""
import csv
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean, quantiles

from PIL import Image


METADATA_FIELDS = ('city', 'region', 'country', 'pavement', 'weather', 'lighting',
                   'camera', 'vehicle', 'camera_height_m', 'camera_pitch_deg',
                   'focal_length_mm', 'frame_rate_fps', 'route_id', 'sequence_id', 'split')
AREA_BINS = ('<0.05%', '0.05–<0.1%', '0.1–<0.25%', '≥0.25%')
SPLITS = {'train': 'train', 'training': 'train', 'val': 'validation',
          'valid': 'validation', 'validation': 'validation', 'test': 'test'}


def describe(values):
    if not values:
        return None
    q1, median, q3 = quantiles(values, n=4, method='inclusive') if len(values) > 1 else values * 3
    return dict(min=min(values), q1=q1, median=median, q3=q3, mean=mean(values), max=max(values))


def box_statistics(boxes):
    grid = [[0] * 10 for _ in range(10)]
    area_bins = dict.fromkeys(AREA_BINS, 0)
    for width, height, cx, cy, image_width, image_height in boxes:
        area = 100 * (width / image_width) * (height / image_height)
        area_bins[AREA_BINS[sum(area >= edge for edge in (.05, .1, .25))]] += 1
        grid[min(9, int(cy * 10))][min(9, int(cx * 10))] += 1
    return {
        'instances': len(boxes), 'area_bins': area_bins, 'center_grid': grid,
        'width_px': describe([b[0] for b in boxes]),
        'height_px': describe([b[1] for b in boxes]),
        'width_height_ratio': describe([b[0] / b[1] for b in boxes]),
        'area_percent': describe([100 * (b[0] / b[4]) * (b[1] / b[5]) for b in boxes]),
    }


def calculate(dataset):
    metadata = {}
    metadata_path = dataset.root / 'dataset_metadata.csv'
    if metadata_path.is_file():
        with metadata_path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                path = (row.get('relative_path') or '').strip().replace('\\', '/')
                if not path or path in metadata:
                    raise ValueError('dataset_metadata.csv needs one unique relative_path per row.')
                metadata[path] = {field: (row.get(field) or '').strip() for field in METADATA_FIELDS}
    totals = Counter()
    resolutions = Counter()
    splits = defaultdict(Counter)
    by_class = defaultdict(list)
    class_images = Counter()
    split_classes = defaultdict(Counter)
    per_image = Counter()
    distributions = {field: Counter() for field in METADATA_FIELDS if field != 'split'}
    group_splits = {field: defaultdict(set) for field in ('route_id', 'sequence_id')}
    issues = []
    names = {c['id']: c['name'] for c in dataset.categories}
    for entry in dataset.entries:
        path = entry['path'].relative_to(dataset.root)
        meta = metadata.get(path.as_posix(), {})
        split = meta.get('split') or next((SPLITS[p.lower()] for p in path.parts[:-1]
                                          if p.lower() in SPLITS), 'unsplit')
        split = SPLITS.get(split.lower(), split)
        counts = splits[split]
        counts['images'] += 1
        totals['images'] += 1
        for field, distribution in distributions.items():
            distribution[meta.get(field) or 'Unknown'] += 1
        for field, groups in group_splits.items():
            if meta.get(field) and split != 'unsplit':
                groups[meta[field]].add(split)
        try:
            with Image.open(entry['path']) as image:
                width, height = image.size
            resolutions[f'{width} × {height}'] += 1
        except OSError as error:
            issues.append({'image': path.as_posix(), 'problem': str(error)})
            counts['unreadable_images'] += 1
            totals['unreadable_images'] += 1
            continue

        rows = []
        if entry['source'] == 'yolo':
            if not entry['label'].is_file():
                status = 'missing_annotations'
            else:
                raw, error = dataset.read_yolo(entry['label'])
                status = 'invalid_annotations' if error else 'labeled'
                if error:
                    issues.append({'image': path.as_posix(), 'problem': error})
                for box in raw:
                    index = box['category']
                    category = dataset.categories[index]['id'] if index < len(dataset.categories) else None
                    x, y, w, h = box['bbox']
                    rows.append((category, x, y, w, h))
        else:
            status = 'missing_annotations' if entry['image_id'] is None else 'labeled'
            annotations = dataset.docs[entry['doc']]['index'].get(entry['image_id'], [])
            for annotation in annotations:
                try:
                    x, y, w, h = map(float, annotation['bbox'])
                    rows.append((annotation['category_id'], (x+w/2)/width, (y+h/2)/height, w/width, h/height))
                except (KeyError, ValueError, TypeError):
                    rows.append((None, 0, 0, 0, 0))
        invalid = sum(
            category not in names or not all(math.isfinite(v) for v in (x, y, w, h))
            or w <= 0 or h <= 0 or min(x-w/2, y-h/2) < -1e-6 or max(x+w/2, y+h/2) > 1+1e-6
            for category, x, y, w, h in rows
        )
        if invalid:
            status = 'invalid_annotations'
            issues.append({'image': path.as_posix(), 'problem': f'{invalid} invalid or out-of-frame boxes'})
        if status == 'labeled':
            status = 'positive' if rows else 'negative'
            per_image[len(rows)] += 1
            for category in {row[0] for row in rows}:
                class_images[category] += 1
            for category, x, y, w, h in rows:
                by_class[category].append((w*width, h*height, x, y, width, height))
                split_classes[split][category] += 1
            counts['instances'] += len(rows)
            totals['instances'] += len(rows)
        counts[status] += 1
        totals[status] += 1
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'dataset': str(dataset.root), 'annotation_mode': dataset.mode,
        'totals': totals, 'splits': dict(splits), 'resolutions': resolutions,
        'instances_per_image': dict(sorted(per_image.items())),
        'boxes': box_statistics([b for boxes in by_class.values() for b in boxes]),
        'classes': [
            {'id': cid, 'name': name, 'images': class_images[cid],
             'instances_by_split': {split: split_classes[split][cid] for split in splits},
             **box_statistics(by_class[cid])}
            for cid, name in names.items() if cid not in dataset.removed_classes or by_class[cid]
        ],
        'metadata_distributions': distributions,
        'groups_in_multiple_splits': {
            field: {group: sorted(values) for group, values in groups.items() if len(values) > 1}
            for field, groups in group_splits.items()
        },
        'issues': issues,
        'notes': [
            'Statistics cover the open dataset and its saved labels, independent of editor filters.',
            'Auto mode uses YOLO when present and COCO otherwise. Linked formats are counted once.',
            'Negative means an existing empty annotation. Missing or invalid annotations and unreadable images are reported separately.',
            'Images with invalid boxes are excluded from object distributions. Area is bounding-box area divided by full image area.',
            'Center grid rows run from top to bottom and columns from left to right in normalized image coordinates.',
            'Capture context to verify per source: perspective-view, fixed vehicle mount behind the windshield, looking in the direction of travel.',
            'Record optical-axis pitch relative to the local road plane. A road-normal axis is 90° to that plane. A forward-facing axis can be horizontal or pitched down, so verify pitch separately.',
            'Pixel scale and image position describe appearance in perspective, not physical defect size or camera-to-defect distance.',
            'Geography, pavement, conditions, camera and sequence metadata come only from dataset_metadata.csv. Group overlap is checked only for supplied IDs.',
        ],
    }
