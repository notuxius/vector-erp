"""Synthetic blur and preservation checks for the optional sharpening stage."""
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from sharpen_product_photos import SETTINGS, sharpen


class SharpenTests(unittest.TestCase):
    def test_improves_known_blurred_edges_without_overshoot(self):
        target = Image.new('RGB', (128, 128), (210, 210, 210))
        draw = ImageDraw.Draw(target)
        draw.rectangle((30, 25, 90, 100), fill=(60, 60, 60))
        draw.line((10, 55, 110, 55), fill=(120, 120, 120), width=5)
        blurred = target.filter(ImageFilter.GaussianBlur(1.1))
        expected = np.asarray(target).astype(float)
        before = np.asarray(blurred).astype(float)
        for settings in SETTINGS.values():
            result, _ = sharpen(blurred, settings)
            after = np.asarray(result)[:, :, :3].astype(float)
            self.assertLess(np.mean((after - expected) ** 2), np.mean((before - expected) ** 2))
            self.assertGreaterEqual(after.min(), before.min())
            self.assertLessEqual(after.max(), before.max())

    def test_alpha_boundary_and_dimensions_preserved(self):
        rng = np.random.default_rng(42)
        rgba = rng.integers(0, 256, (80, 120, 4), dtype=np.uint8)
        rgba[:, :, 3] = 0
        rgba[10:70, 20:100, 3] = 255
        rgba[10, 20:100, 3] = 128
        for settings in SETTINGS.values():
            result, metrics = sharpen(Image.fromarray(rgba), settings)
            after = np.asarray(result)
            self.assertEqual(result.size, (120, 80))
            np.testing.assert_array_equal(after[:, :, 3], rgba[:, :, 3])
            np.testing.assert_array_equal(after[rgba[:, :, 3] < 250], rgba[rgba[:, :, 3] < 250])
            self.assertLessEqual(metrics['max_channel_change'], settings['max_change'])

    def test_flat_surfaces_and_very_small_noise_unchanged(self):
        rng = np.random.default_rng(4)
        noise = rng.integers(0, 2, (64, 64, 1), dtype=np.uint8)
        rgb = np.repeat(128 + noise, 3, axis=2)
        for settings in SETTINGS.values():
            result, _ = sharpen(Image.fromarray(rgb), settings)
            np.testing.assert_array_equal(np.asarray(result)[:, :, :3], rgb)


if __name__ == '__main__':
    unittest.main()
