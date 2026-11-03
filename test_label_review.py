"""Meaningful persistence/identity tests, using disposable datasets only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import label_review as lr


class LabelReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='label-review-', dir=lr.ROOT)
        self.root = Path(self.tmp.name)
        self.state_patch = patch.object(lr, 'STATE', self.root / '.state')
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.tmp.cleanup()

    def image(self, relative, color=(100,150,200)):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (120,80), color).save(p)
        return p

    def text(self, relative, data):
        p=self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
        return p

    def coco(self, filename='train.json', images=None):
        d={'info': {'description':'keep this'},'images': images or [{'id':31,'file_name':'same.png','width':120,'height':80}],
           'categories':[{'id':7,'name':'pothole','supercategory':'road'}],
           'annotations':[{'id':90,'image_id':31,'category_id':7,'bbox':[12,8,24,16],'area':384,'segmentation':[],'iscrowd':0,'custom':'keep'}]}
        self.text(filename,json.dumps(d))
        return d

    def payload(self,d,id='0',boxes=None,blur=None):
        detail=d.detail(id)
        return {'revision':detail['revision'],'boxes':detail['boxes'] if boxes is None else boxes,'blur':blur or []}

    def test_nested_same_basename_identity_and_empty_labels(self):
        a=self.image('street_a/images/sequence_a/G001.png')
        b=self.image('street_b/images/sequence_b/G001.png')
        la=self.text('street_a/labels/sequence_a/G001.txt','0 0.5 0.5 0.2 0.2\n')
        lb=self.text('street_b/labels/sequence_b/G001.txt','')
        d=lr.Dataset(self.root)
        self.assertEqual(len(d.entries),2)
        self.assertNotEqual(d.entries[0]['path'],d.entries[1]['path'])
        hashes=(lr.signature(a),lr.signature(b))
        before=la.read_bytes()
        result=d.save('0',self.payload(d,boxes=[]))
        self.assertEqual(la.read_bytes(),b'')
        self.assertEqual(lb.read_bytes(),b'')
        self.assertEqual(hashes,(lr.signature(a),lr.signature(b)))
        backup=Path(result['backup'])/la.relative_to(self.root)
        self.assertEqual(backup.read_bytes(),before)
        self.assertEqual(d.detail('0')['boxes'],[])
        self.assertTrue(lr.Dataset(self.root).summary()['images'][0]['reviewed'])

    def test_both_yolo_folder_layouts(self):
        self.image('images/train/one.png')
        self.text('labels/train/one.txt','0 .5 .5 .2 .2')
        self.image('valid/images/two.png')
        self.text('valid/labels/two.txt','0 .5 .5 .2 .2')
        d=lr.Dataset(self.root)
        self.assertEqual([e['count'] for e in d.entries],[1,1])

    def test_class_changes_preserve_ids_across_reopen(self):
        self.image('images/same.png')
        label = self.text('labels/same.txt', '0 .2 .2 .2 .2\n')
        original = self.coco()
        dataset = lr.Dataset(self.root)
        dataset.manage_class({'id': 7, 'name': 'Road defect'})
        dataset.manage_class({'name': 'Drain cover'})
        reopened = lr.Dataset(self.root)
        self.assertEqual(reopened.categories, [
            {'id': 7, 'name': 'Road defect'}, {'id': 8, 'name': 'Drain cover'},
        ])
        document = json.loads((self.root / 'train.json').read_text())
        self.assertEqual(document['annotations'], original['annotations'])
        self.assertEqual(document['categories'][0]['supercategory'], 'road')
        self.assertEqual(label.read_text(), '0 .2 .2 .2 .2\n')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            reopened.manage_class({'name': 'drain cover'})

    def test_remove_class_requires_phrase_and_preserves_yolo_indices(self):
        self.image('images/road.png')
        label = self.text('labels/road.txt', '1 .5 .5 .2 .2\n')
        dataset = lr.Dataset(self.root)
        with self.assertRaisesRegex(ValueError, 'exact confirmation'):
            dataset.manage_class({'action': 'remove', 'id': 0, 'confirmation': 'yes'})
        with self.assertRaisesRegex(ValueError, 'still has annotations'):
            dataset.manage_class({'action': 'remove', 'id': 1,
                                  'confirmation': 'REMOVE CLASS 1: Class 1 | KEEP ALL ANNOTATIONS'})
        dataset.manage_class({'action': 'remove', 'id': 0,
                              'confirmation': 'REMOVE CLASS 0: Class 0 | KEEP ALL ANNOTATIONS'})
        reopened = lr.Dataset(self.root)
        self.assertEqual(reopened.summary()['categories'], [{'id': 1, 'name': 'Class 1'}])
        self.assertEqual(reopened.detail('0')['boxes'][0]['category'], 1)
        self.assertEqual(label.read_text(), '1 .5 .5 .2 .2\n')

    def test_bulk_class_removal_updates_yolo_and_coco_with_backups(self):
        self.image('images/same.png')
        label = self.text('labels/same.txt', '0 .2 .2 .2 .2\n')
        original = self.coco()
        dataset = lr.Dataset(self.root)
        dataset.manage_class({'name': 'Drain cover'})
        dataset.manage_class({'name': 'Other'})
        with self.assertRaisesRegex(ValueError, 'exact confirmation'):
            dataset.manage_class({'action': 'remove', 'id': 7, 'handling': 'delete',
                                  'confirmation': 'REMOVE CLASS 7: pothole | KEEP ALL ANNOTATIONS'})
        dataset.manage_class({'action': 'remove', 'id': 7, 'handling': 'reassign', 'target_id': 8,
                              'confirmation': 'REMOVE CLASS 7: pothole | REASSIGN ALL ANNOTATIONS TO 8: Drain cover'})
        self.assertEqual(label.read_text(), '1 .2 .2 .2 .2\n')
        document = json.loads((self.root / 'train.json').read_text())
        self.assertEqual(document['annotations'], [{**original['annotations'][0], 'category_id': 8}])
        self.assertEqual(dataset.detail('0')['boxes'][0]['category'], 8)
        dataset.manage_class({'action': 'remove', 'id': 8, 'handling': 'delete',
                              'confirmation': 'REMOVE CLASS 8: Drain cover | DELETE ALL ANNOTATIONS IN THIS CLASS'})
        self.assertEqual(label.read_bytes(), b'')
        self.assertEqual(json.loads((self.root / 'train.json').read_text())['annotations'], [])
        self.assertEqual(dataset.summary()['images'][0]['count'], 0)
        self.assertEqual(lr.Dataset(self.root).summary()['categories'], [{'id': 9, 'name': 'Other'}])
        backups = list((dataset.state / 'backups').glob('*/originals/labels/same.txt'))
        self.assertEqual({path.read_text() for path in backups}, {'0 .2 .2 .2 .2\n', '1 .2 .2 .2 .2\n'})

    def test_review_progress_is_separate_from_saving_edits(self):
        self.image('images/road.png')
        label = self.text('labels/road.txt', '')
        dataset = lr.Dataset(self.root)
        saved = dataset.save('0', {**self.payload(dataset), 'mark_reviewed': False})
        self.assertFalse(saved['entry']['reviewed'])
        before = label.stat().st_mtime_ns
        reviewed = dataset.save('0', {'revision': saved['detail']['revision'], 'review_only': True})
        self.assertTrue(reviewed['entry']['reviewed'])
        self.assertEqual(label.stat().st_mtime_ns, before)
        self.assertTrue(lr.Dataset(self.root).public(dataset.by_id['0'])['reviewed'])

    def test_statistics_distinguish_missing_labels_and_count_linked_formats_once(self):
        self.image('images/train/same.png')
        self.image('images/valid/empty.png')
        self.image('images/test/missing.png')
        self.text('labels/train/same.txt', '0 .2 .2 .2 .2\n')
        self.text('labels/valid/empty.txt', '')
        self.coco()
        self.text('dataset_metadata.csv', 'relative_path,route_id,pavement\n'
                  'images/train/same.png,R1,asphalt\nimages/valid/empty.png,R1,concrete\n')
        result = lr.calculate(lr.Dataset(self.root))
        self.assertEqual(result['totals'], {
            'images': 3, 'instances': 1, 'positive': 1, 'negative': 1, 'missing_annotations': 1,
        })
        self.assertEqual(result['boxes']['area_percent']['median'], 4)
        self.assertEqual(result['boxes']['center_grid'][2][2], 1)
        self.assertEqual(result['groups_in_multiple_splits']['route_id'], {'R1': ['train', 'validation']})

    def test_prediction_cache_persists_and_prioritizes_waiting_foreground(self):
        from label_review_prediction_cache import PredictionCache
        from types import SimpleNamespace

        self.image('images/sample.png')
        dataset = lr.Dataset(self.root)
        entry = dataset.by_id['0']
        predict = unittest.mock.Mock(return_value={'regions': [], 'warnings': []})
        detector = SimpleNamespace(predict=predict)
        payload = {'id': '0', 'background': True}
        cache = PredictionCache()
        cache_only = {**payload, 'cache_only': True}
        self.assertIsNone(cache.run(dataset, entry, 'privacy', cache_only, detector, lr.atomic, lr.inside))
        predict.assert_not_called()
        cache.waiting = 1
        self.assertEqual(cache.run(dataset, entry, 'privacy', payload, detector, lr.atomic, lr.inside),
                         {'deferred': True, 'reason': 'Waiting for inference capacity'})
        predict.assert_not_called()
        cache.waiting = 0
        cache.run(dataset, entry, 'privacy', payload, detector, lr.atomic, lr.inside)
        cached = PredictionCache().run(dataset, entry, 'privacy', cache_only, detector, lr.atomic, lr.inside)
        self.assertTrue(cached['cached'])
        self.assertEqual(predict.call_count, 1)
        self.assertEqual(len(list((self.root / 'prediction_cache/privacy').glob('*.json'))), 1)
        self.assertFalse(entry['label'].exists())
        self.image('images/sample.png', (0, 0, 0))
        cache.run(dataset, entry, 'privacy', {'id': '0'}, detector, lr.atomic, lr.inside)
        self.assertEqual(predict.call_count, 2)

    def test_contact_sheets_are_excluded(self):
        self.image('keep_together/images/sequence/road.png')
        self.text('keep_together/labels/sequence/road.txt','')
        self.image('review_sheets/contact_sheet.png')
        self.assertEqual(len(lr.Dataset(self.root).entries),1)

    def test_yolo_edits_preserve_linked_coco_ids_and_metadata(self):
        self.image('train/images/same.png')
        self.text('train/labels/same.txt','0 .2 .2 .2 .2')
        self.coco()
        ds=lr.Dataset(self.root)
        boxes=ds.detail('0')['boxes']
        boxes[0]['bbox']=[30,20,40,20]
        ds.save('0',self.payload(ds,boxes=boxes))
        ann=json.loads((self.root/'train.json').read_text())['annotations'][0]
        self.assertEqual(ann['id'],90)
        self.assertEqual(ann['custom'],'keep')

    def test_coco_yolo_sync_and_metadata_preservation(self):
        image=self.image('train/images/same.png')
        label=self.text('train/labels/same.txt','0 .2 .2 .2 .2\n')
        original=self.coco()
        d=lr.Dataset(self.root,'coco')
        detail=d.detail('0')
        self.assertEqual(detail['boxes'][0]['category'],7)
        self.assertFalse(detail['formats_disagree'])
        boxes=copy.deepcopy(detail['boxes'])
        boxes[0]['bbox']=[20,10,30,20]
        boxes.append({'id':'new','category':7,'bbox':[65,30,10,10]})
        before=lr.signature(image)
        result=d.save('0',self.payload(d,boxes=boxes))
        saved=json.loads((self.root/'train.json').read_text())
        self.assertEqual(saved['info'],original['info'])
        self.assertEqual(saved['categories'],original['categories'])
        self.assertEqual(saved['annotations'][0]['id'],90)
        self.assertEqual(saved['annotations'][0]['custom'],'keep')
        self.assertEqual(saved['annotations'][0]['bbox'],[20,10,30,20])
        self.assertEqual(len(label.read_text().splitlines()),2)
        self.assertEqual(lr.signature(image),before)
        self.assertEqual(result['detail']['count'],2)
        d.save('0',self.payload(d,boxes=[]))
        self.assertEqual(json.loads((self.root/'train.json').read_text())['annotations'],[])
        self.assertEqual(label.read_bytes(),b'')

    def test_split_duplicate_basenames_are_not_crosslinked(self):
        self.image('train/images/same.png')
        self.image('test/images/same.png')
        self.coco('train.json')
        self.coco('test.json')
        d=lr.Dataset(self.root,'coco')
        self.assertEqual(len(d.entries),2)
        for e in d.entries:
            self.assertEqual(e['doc'].stem,e['path'].relative_to(self.root).parts[0])

    def test_gaussian_blur_changes_only_region_and_has_backup(self):
        p=self.root/'images/a.png';p.parent.mkdir()
        yy,xx=np.indices((80,120));arr=np.repeat((((xx+yy)%2)*255).astype('uint8')[:,:,None],3,axis=2)
        Image.fromarray(arr).save(p)
        d=lr.Dataset(self.root)
        result=d.save('0',self.payload(d,blur=[{'bbox':[20,20,40,30],'radius':8}]))
        actual=np.array(Image.open(p))
        self.assertTrue(np.array_equal(actual[:20],arr[:20]))
        self.assertTrue(np.array_equal(actual[:, :20],arr[:, :20]))
        self.assertGreater(np.abs(actual[20:50,20:60].astype(float)-arr[20:50,20:60]).mean(),50)
        self.assertTrue((Path(result['backup'])/'images/a.png').exists())
        with Image.open(p) as image:
            self.assertEqual(image.size,(120,80))

    def test_stale_save_does_not_touch_any_file(self):
        p=self.image('images/a.png');label=self.text('labels/a.txt','0 .5 .5 .2 .2')
        d=lr.Dataset(self.root);payload=self.payload(d)
        label.write_text('0 .4 .4 .2 .2')
        before=(lr.signature(p),lr.signature(label))
        with self.assertRaisesRegex(ValueError,'changed'):
            d.save('0',payload)
        self.assertEqual(before,(lr.signature(p),lr.signature(label)))

    def test_failed_multifile_save_rolls_back_completed_writes(self):
        self.image('train/images/same.png');self.text('train/labels/same.txt','0 .2 .2 .2 .2')
        self.coco();d=lr.Dataset(self.root)
        label=self.root/'train/labels/same.txt';doc=self.root/'train.json'
        old=(label.read_bytes(),doc.read_bytes());real_atomic=lr.atomic
        def fail(path,data):
            if path==doc and data!=old[1]:
                raise OSError('simulated disk failure')
            return real_atomic(path,data)
        with patch.object(lr,'atomic',fail):
            with self.assertRaisesRegex(OSError,'disk failure'):
                d.save('0',self.payload(d,boxes=[]))
        self.assertEqual((label.read_bytes(),doc.read_bytes()),old)

    def test_unsupported_yolo_never_overwritten(self):
        self.image('images/a.png');label=self.text('labels/a.txt','0 .1 .1 .2 .2 .3 .3')
        d=lr.Dataset(self.root);old=label.read_bytes()
        with self.assertRaisesRegex(ValueError,'polygons'):
            d.save('0',self.payload(d,boxes=[]))
        self.assertEqual(label.read_bytes(),old)

    def test_coco_polygon_protected(self):
        self.image('train/images/same.png');d=self.coco()
        d['annotations'][0]['segmentation']=[[12,8,36,8,36,24,12,24]]
        self.text('train.json',json.dumps(d));ds=lr.Dataset(self.root,'coco')
        with self.assertRaisesRegex(ValueError,'segmentation'):
            ds.save('0',self.payload(ds,boxes=[]))

    def test_invalid_bbox_and_blur_are_rejected(self):
        self.image('images/a.png');d=lr.Dataset(self.root)
        with self.assertRaises(ValueError):
            d.save('0',self.payload(d,boxes=[{'id':'n','category':0,'bbox':[115,0,20,10]}]))
        with self.assertRaises(ValueError):
            d.save('0',self.payload(d,blur=[{'bbox':[0,0,20,20],'radius':float('nan')}]))


if __name__=='__main__':
    unittest.main()
