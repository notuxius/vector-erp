"""Core safety and image-behaviour tests; no model download or GPU required."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

import process_product_photos as photos


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'originals/123/photo.png'
        self.path.parent.mkdir(parents=True)
        rng = np.random.default_rng(3)
        self.image = Image.fromarray(rng.integers(0, 256, (160, 200, 3), dtype=np.uint8))
        self.image.save(self.path)
        self.mask = Image.new('L', self.image.size)
        ImageDraw.Draw(self.mask).rectangle((30, 20, 170, 140), fill=255)
        self.override = self.root / 'override.png'
        self.mask.save(self.override)
        self.preset = json.loads((photos.PROJECT / 'scripts/photo_presets/catalogue-v1.json').read_text())
        self.entry = {'path': 'originals/123/photo.png', 'sha256': photos.file_sha(self.path),
                      'product_ids': ['123'], 'names': ['Test machine']}
        self.engine = SimpleNamespace(model_sha='fake-model-hash')
        self.versions = {'rembg': 'test', 'Pillow': 'test'}

    def process(self, **overrides):
        values = dict(root=self.root, entry=self.entry, preset=self.preset,
                      engine=self.engine, run='local-test', edits=None,
                      mask_override=self.override, versions=self.versions)
        values.update(overrides)
        return photos.process_one(**values)

    def test_alpha_preserves_every_rgb_pixel(self):
        result = photos.apply_alpha(self.image, self.mask)
        np.testing.assert_array_equal(np.asarray(result)[:, :, :3], np.asarray(self.image))
        np.testing.assert_array_equal(np.asarray(result)[:, :, 3], np.asarray(self.mask))

    def test_disabled_tone_operations_are_exact_noop(self):
        p = copy.deepcopy(self.preset)
        p.update(auto_exposure=False, shadow_lift=0, contrast=1, sharpen_percent=0)
        result, _ = photos.correct_tones(self.image, self.mask, p)
        np.testing.assert_array_equal(np.asarray(result), np.asarray(self.image))

    def test_repeat_skips_and_modified_output_is_protected(self):
        source_before = self.path.read_bytes()
        record, skipped = self.process()
        self.assertFalse(skipped)
        self.assertTrue(self.process()[1])
        self.assertEqual(self.path.read_bytes(), source_before)
        master = self.root / record['outputs']['master']['path']
        with Image.open(master) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.size, self.image.size)
        master.write_bytes(b'external editor changed this file')
        with self.assertRaisesRegex(RuntimeError, 'changed or removed'):
            self.process()
        self.assertEqual(master.read_bytes(), b'external editor changed this file')

    def test_new_recipe_creates_new_files(self):
        first, _ = self.process()
        p = copy.deepcopy(self.preset)
        p['shadow_lift'] = .04
        second, skipped = self.process(preset=p)
        self.assertFalse(skipped)
        self.assertNotEqual(first['outputs']['master']['path'], second['outputs']['master']['path'])
        self.assertTrue((self.root / first['outputs']['master']['path']).exists())

    def test_changed_original_and_outside_path_are_rejected(self):
        self.path.write_bytes(b'changed original')
        with self.assertRaisesRegex(ValueError, 'differs from extraction'):
            self.process()
        with self.assertRaisesRegex(ValueError, 'inside this archive'):
            photos.original_path(self.root, 'override.png')

    def test_wrong_mask_and_wrong_photo_edits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different original'):
            self.process(edits={'source_sha256': 'wrong'})
        Image.new('L', (10, 10), 255).save(self.override)
        with self.assertRaisesRegex(ValueError, 'dimensions differ'):
            self.process()

    def test_canvas_does_not_upscale_and_remains_centered(self):
        canvas = photos.catalogue_canvas(photos.apply_alpha(self.image, self.mask), self.preset)
        self.assertEqual(canvas.size, (1280, 960))
        left, top, right, bottom = canvas.getchannel('A').getbbox()
        self.assertEqual((right - left, bottom - top), (141, 121))
        self.assertLessEqual(abs(left - (1280 - right)), 1)
        self.assertLessEqual(abs(top - (960 - bottom)), 1)

    def test_auto_cleanup_removes_dark_fringe_and_preserves_interior(self):
        rgb = np.full((240, 240, 3), 20, dtype=np.uint8)
        rgb[30:210, 30:210] = 230
        rgb[209, 30:210] = 50  # Contaminated edge accidentally retained by mask.
        alpha = np.zeros((240, 240), dtype=np.uint8)
        alpha[30:210, 30:210] = 255
        source = Image.fromarray(rgb)
        result, info = photos.cleanup_edges(source, Image.fromarray(alpha), {'enabled': True})
        out = np.asarray(result)
        self.assertGreater(info['changed_pixels'], 0)
        self.assertLess(out[209, 120], 40)
        self.assertEqual(out[190, 120], 255)
        self.assertTrue(np.all(out <= alpha))
        np.testing.assert_array_equal(np.asarray(source), rgb)

    def test_auto_cleanup_preserves_thin_parts_and_low_contrast(self):
        alpha = np.zeros((120, 120), dtype=np.uint8)
        alpha[20:100, 40:44] = 255
        rgb = np.full((120, 120, 3), 20, dtype=np.uint8)
        rgb[20:100, 40:44] = 230
        result, _ = photos.cleanup_edges(Image.fromarray(rgb), Image.fromarray(alpha), {'enabled': True})
        np.testing.assert_array_equal(np.asarray(result), alpha)
        alpha[20:100, 20:100] = 255
        rgb[:] = 200
        rgb[20:100, 20:100] = 215
        result, _ = photos.cleanup_edges(Image.fromarray(rgb), Image.fromarray(alpha), {'enabled': True})
        np.testing.assert_array_equal(np.asarray(result), alpha)

    def test_auto_cleanup_abstains_on_dark_product(self):
        alpha = np.zeros((120, 120), dtype=np.uint8)
        alpha[20:100, 20:100] = 255
        rgb = np.full((120, 120, 3), 230, dtype=np.uint8)
        rgb[20:100, 20:100] = 30
        result, _ = photos.cleanup_edges(Image.fromarray(rgb), Image.fromarray(alpha), {'enabled': True})
        np.testing.assert_array_equal(np.asarray(result), alpha)

    def test_existing_source_alpha_is_preserved_without_inference(self):
        rgba = self.image.convert('RGBA')
        alpha = np.asarray(self.mask).copy()
        alpha[20, 30:171] = 93
        rgba.putalpha(Image.fromarray(alpha))
        rgba.save(self.path)
        self.entry['sha256'] = photos.file_sha(self.path)
        self.preset['edge_cleanup'] = {'enabled': True}
        record, _ = self.process(mask_override=None)
        self.assertEqual(record['mask_origin'], 'existing-source-transparency')
        with Image.open(self.root / record['outputs']['mask']['path']) as result:
            np.testing.assert_array_equal(np.asarray(result), alpha)

    def test_preserve_source_background_keeps_rgb_and_rim_exactly(self):
        self.preset['background_policy'] = 'preserve-source'
        self.preset['edge_cleanup'] = {'enabled': True}
        record, _ = self.process(mask_override=None)
        self.assertEqual(record['mask_origin'], 'preserved-source-background')
        with Image.open(self.root / record['outputs']['master']['path']) as result:
            np.testing.assert_array_equal(np.asarray(result)[:, :, :3], np.asarray(self.image))
            self.assertTrue(np.all(np.asarray(result)[:, :, 3] == 255))
        self.assertEqual(record['tone_adjustments']['exposure_ev'], 0)

    def test_preserve_source_rejects_conflicting_mask_override(self):
        self.preset['background_policy'] = 'preserve-source'
        with self.assertRaisesRegex(ValueError, 'cannot be combined'):
            self.process()

    def test_safe_preset_disables_experimental_edge_trimming(self):
        p = json.loads((photos.PROJECT / 'scripts/photo_presets/catalogue-v3.json').read_text())
        result, _ = photos.cleanup_edges(self.image, self.mask, p['edge_cleanup'])
        np.testing.assert_array_equal(np.asarray(result), np.asarray(self.mask))

    def test_auto_policy_preserves_white_studio_photos_of_varied_colours(self):
        for color in [(240, 220, 100), (30, 35, 40), (248, 248, 248)]:
            source = Image.new('RGB', (200, 160), 'white')
            ImageDraw.Draw(source).rectangle((40, 30, 160, 130), fill=color)
            policy, evidence = photos.choose_background_policy(source, None, 'auto')
            self.assertEqual(policy, 'preserve-source')
            self.assertEqual(evidence['reason'], 'existing-white-background-preserved')

    def test_auto_policy_rejects_nonwhite_or_coloured_background(self):
        for color in [(185, 185, 185), (255, 245, 220), (100, 130, 160)]:
            source = Image.new('RGB', (200, 160), color)
            self.assertEqual(photos.choose_background_policy(source, None, 'auto')[0], 'cutout')
        source = Image.new('RGB', (200, 160), 'white')
        ImageDraw.Draw(source).rectangle((0, 0, 10, 159), fill='black')
        self.assertEqual(photos.choose_background_policy(source, None, 'auto')[0], 'cutout')

    def test_auto_policy_does_not_classify_white_frame_as_studio(self):
        source = Image.new('RGB', (200, 160), 'white')
        ImageDraw.Draw(source).rectangle((2, 2, 197, 157), fill='#444455')
        self.assertEqual(photos.choose_background_policy(source, None, 'auto')[0], 'cutout')

    def test_auto_policy_prioritizes_existing_alpha_and_explicit_overrides(self):
        white = Image.new('RGB', self.image.size, 'white')
        self.assertEqual(photos.choose_background_policy(white, self.mask, 'auto')[1]['reason'],
                         'existing-source-transparency')
        self.assertEqual(photos.choose_background_policy(white, None, 'auto', True)[0], 'cutout')

    def test_auto_pipeline_preserves_pixels_without_product_specific_metadata(self):
        source = Image.new('RGB', (200, 160), 'white')
        ImageDraw.Draw(source).ellipse((45, 30, 150, 130), fill=(255, 239, 160))
        source.save(self.path)
        self.entry['sha256'] = photos.file_sha(self.path)
        self.entry['names'] = ['Unrelated product name']
        self.preset['background_policy'] = 'auto'
        record, _ = self.process(mask_override=None)
        self.assertEqual(record['background_policy'], 'preserve-source')
        with Image.open(self.root / record['outputs']['master']['path']) as result:
            np.testing.assert_array_equal(np.asarray(result)[:, :, :3], np.asarray(source))

    def test_scene_guard_preserves_structured_views_in_different_orientations(self):
        yy, xx = np.indices((160, 240))
        structure = np.where(((xx // 7 + yy // 9) % 2) == 0, 35, 180).astype(np.uint8)
        image = Image.fromarray(structure).convert('RGB')
        alpha = Image.new('L', image.size, 0)
        ImageDraw.Draw(alpha).rectangle((90, 35, 150, 125), fill=255)
        for angle in (0, 90, 180):
            decision = photos.assess_scene_risk(image.rotate(angle, expand=True), alpha.rotate(angle, expand=True))
            self.assertTrue(decision['preserve_scene'])
            self.assertTrue(decision['review_required'])

    def test_scene_guard_does_not_remove_studio_cutouts(self):
        image = Image.new('RGB', (240, 160), '#dedede')
        mask = Image.new('L', image.size, 0)
        ImageDraw.Draw(image).rectangle((80, 30, 160, 130), fill='black')
        ImageDraw.Draw(mask).rectangle((80, 30, 160, 130), fill=255)
        self.assertFalse(photos.assess_scene_risk(image, mask)['preserve_scene'])

    def test_scene_guard_keeps_frame_filling_and_cropped_details(self):
        self.assertTrue(photos.assess_scene_risk(self.image, Image.new('L', self.image.size, 255))['preserve_scene'])
        alpha = Image.new('L', self.image.size, 0)
        ImageDraw.Draw(alpha).rectangle((0, 20, 199, 90), fill=255)
        self.assertTrue(photos.assess_scene_risk(self.image, alpha)['preserve_scene'])

    def test_scene_guard_pipeline_keeps_full_scene_pixels(self):
        self.preset['background_policy'] = 'auto'
        self.preset['scene_guard'] = {'enabled': True}
        detail_mask = Image.new('L', self.image.size, 0)
        ImageDraw.Draw(detail_mask).rectangle((70, 50, 120, 100), fill=255)
        self.engine.predict = lambda image: detail_mask.copy()
        self.engine.providers = ['test']
        # Random textured scene, intentionally presented as unrelated metadata.
        self.entry['names'] = ['Generic equipment view']
        record, _ = self.process(mask_override=None)
        self.assertEqual(record['mask_origin'], 'preserved-complete-scene')
        with Image.open(self.root / record['outputs']['master']['path']) as result:
            np.testing.assert_array_equal(np.asarray(result)[:, :, :3], np.asarray(self.image))
            self.assertTrue(np.all(np.asarray(result)[:, :, 3] == 255))

    def test_scene_guard_allows_larger_subject_in_textured_workshop(self):
        yy, xx = np.indices((200, 240))
        structure = np.where(((xx // 7 + yy // 9) % 2) == 0, 35, 180).astype(np.uint8)
        image = Image.fromarray(structure).convert('RGB')
        mask = Image.new('L', image.size, 0)
        ImageDraw.Draw(mask).rectangle((30, 70, 210, 140), fill=255)
        self.assertFalse(photos.assess_scene_risk(image, mask)['preserve_scene'])

    def annotation_fixture(self, label_text='24 V', offset=0):
        image = Image.new('RGB', (240, 200), '#eeeeee')
        draw = ImageDraw.Draw(image)
        draw.rectangle((120, 95, 210, 180), fill='#445566')
        draw.rectangle((20 + offset, 30, 76 + offset, 52), fill='black')
        draw.text((23 + offset, 34), label_text, fill='white')
        draw.line((48 + offset, 52, 48 + offset, 135, 120, 135), fill='black', width=1)
        mask = Image.new('L', image.size, 0)
        ImageDraw.Draw(mask).rectangle((120, 95, 210, 180), fill=255)
        return image, mask

    def test_annotation_guard_handles_different_text_and_positions(self):
        for text, offset in [('24 V', 0), ('8 cm', 45), ('150 kg', 90)]:
            image, mask = self.annotation_fixture(text, offset)
            self.assertTrue(photos.assess_annotation_risk(image, mask)['preserve_frame'])

    def test_annotation_guard_ignores_markings_inside_product(self):
        image = Image.new('RGB', (240, 200), '#eeeeee')
        mask = Image.new('L', image.size, 0)
        ImageDraw.Draw(mask).rectangle((40, 30, 210, 170), fill=255)
        ImageDraw.Draw(image).rectangle((70, 60, 145, 90), fill='black')
        self.assertFalse(photos.assess_annotation_risk(image, mask)['preserve_frame'])

    def test_annotation_pipeline_keeps_text_and_leader_pixels(self):
        image, mask = self.annotation_fixture()
        image.save(self.path)
        self.entry['sha256'] = photos.file_sha(self.path)
        self.engine.predict = lambda source: mask.copy()
        self.engine.providers = ['test']
        self.preset['background_policy'] = 'auto'
        self.preset['annotation_guard'] = {'enabled': True}
        record, _ = self.process(mask_override=None)
        self.assertEqual(record['mask_origin'], 'preserved-annotation-frame')
        with Image.open(self.root / record['outputs']['master']['path']) as output:
            np.testing.assert_array_equal(np.asarray(output)[:, :, :3], np.asarray(image))
            self.assertTrue(np.all(np.asarray(output)[:, :, 3] == 255))

    def test_edge_spill_reduces_bright_colour_contamination_without_cutting(self):
        source = Image.new('RGB', (220, 220), 'white')
        draw = ImageDraw.Draw(source)
        draw.rectangle((25, 25, 195, 195), fill=(10, 30, 120))
        draw.rectangle((25, 90, 26, 110), fill=(150, 160, 200))
        mask = Image.new('L', source.size, 0)
        ImageDraw.Draw(mask).rectangle((25, 25, 195, 195), fill=255)
        before_alpha = np.asarray(mask).copy()
        output, info = photos.reduce_edge_spill(source, mask, {'enabled': True})
        self.assertGreater(info['changed_pixels'], 0)
        self.assertLess(np.asarray(output)[100, 25, 0], 150)
        np.testing.assert_array_equal(np.asarray(mask), before_alpha)
        np.testing.assert_array_equal(np.asarray(output)[40:180, 40:180], np.asarray(source)[40:180, 40:180])
        self.assertLessEqual(np.abs(np.asarray(output).astype(int) - np.asarray(source).astype(int)).max(), 64)

    def test_edge_spill_preserves_neutral_panels_and_thin_parts(self):
        for box, color in [((25, 25, 175, 175), (230, 230, 230)), ((80, 25, 83, 175), (10, 30, 120))]:
            source = Image.new('RGB', (200, 200), 'white')
            mask = Image.new('L', source.size, 0)
            ImageDraw.Draw(source).rectangle(box, fill=color)
            ImageDraw.Draw(mask).rectangle(box, fill=255)
            result, _ = photos.reduce_edge_spill(source, mask, {'enabled': True})
            np.testing.assert_array_equal(np.asarray(result), np.asarray(source))

    def test_edge_spill_skips_preserved_source_frames(self):
        self.preset['background_policy'] = 'preserve-source'
        self.preset['edge_spill'] = {'enabled': True}
        record, _ = self.process(mask_override=None)
        self.assertFalse(record['edge_spill']['enabled'])
        with Image.open(self.root / record['outputs']['master']['path']) as image:
            np.testing.assert_array_equal(np.asarray(image)[:, :, :3], np.asarray(self.image))

    def surface_fixture(self, offset=0, dark_ring=False, with_detail=True):
        image = Image.new('RGB', (400, 400), '#555555')
        mask = Image.new('L', image.size, 0)
        ImageDraw.Draw(image).rectangle((25, 25, 375, 375), fill='#222222' if dark_ring else '#dddddd')
        ImageDraw.Draw(mask).rectangle((25, 25, 375, 375), fill=255)
        x, y = 100 + offset, 110 + offset
        ImageDraw.Draw(image).rectangle((x, y, x + 15, y + 19), fill='#dddddd')
        if with_detail:
            ImageDraw.Draw(image).rectangle((x + 5, y + 7, x + 10, y + 9), fill='#222222')
        ImageDraw.Draw(mask).rectangle((x - 1, y - 1, x + 16, y + 20), fill=180)
        ImageDraw.Draw(mask).rectangle((x, y, x + 15, y + 19), fill=0)
        return image, mask, x, y

    def test_surface_recovery_restores_detail_and_soft_seam_at_varied_positions(self):
        for offset in [0, 80, 160]:
            image, mask, x, y = self.surface_fixture(offset)
            source_before = np.asarray(image).copy()
            result, info = photos.recover_enclosed_surfaces(image, mask, {'enabled': True})
            self.assertGreater(info['restored_pixels'], 0)
            self.assertTrue(np.all(np.asarray(result)[y-1:y+21, x-1:x+17] == 255))
            np.testing.assert_array_equal(np.asarray(image), source_before)
            np.testing.assert_array_equal(np.asarray(result)[:50], np.asarray(mask)[:50])

    def test_surface_recovery_keeps_plain_openings_and_dark_loops_open(self):
        for dark_ring, with_detail in [(False, False), (True, True)]:
            image, mask, _, _ = self.surface_fixture(dark_ring=dark_ring, with_detail=with_detail)
            result, _ = photos.recover_enclosed_surfaces(image, mask, {'enabled': True})
            np.testing.assert_array_equal(np.asarray(result), np.asarray(mask))

    def test_surface_recovery_leaves_regions_connected_to_background(self):
        image, mask, x, y = self.surface_fixture()
        ImageDraw.Draw(mask).line((0, y + 10, x + 10, y + 10), fill=0, width=2)
        result, _ = photos.recover_enclosed_surfaces(image, mask, {'enabled': True})
        np.testing.assert_array_equal(np.asarray(result), np.asarray(mask))


if __name__ == '__main__':
    unittest.main()
