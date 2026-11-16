"""Shared illustration guard checks, independent of filenames and model inference."""
import unittest
import numpy as np
from PIL import Image, ImageDraw
import process_product_photos as photos


class GraphicGuardTests(unittest.TestCase):
    def example(self):
        image = Image.new('RGB', (400, 300), 'white')
        ImageDraw.Draw(image).rectangle((180, 70, 330, 260), fill='#505050')
        mask = Image.new('L', image.size)
        ImageDraw.Draw(mask).rectangle((180, 70, 330, 260), fill=255)
        return image, mask

    def test_colored_annotation_outside_object_is_preserved(self):
        image, mask = self.example()
        ImageDraw.Draw(image).rectangle((15, 30, 250, 270), outline='red', width=3)
        result = photos.assess_graphic_risk(image, mask)
        self.assertTrue(result['preserve_frame'])
        self.assertEqual(result['reason'], 'discarded-colored-annotation')

    def test_interface_text_is_preserved(self):
        image, mask = self.example()
        draw = ImageDraw.Draw(image)
        for y in range(10, 280, 25):
            for x in range(10, 170, 15):
                draw.rectangle((x, y, x + 3, y + 6), fill='#333333')
        self.assertEqual(photos.assess_graphic_risk(image, mask)['reason'], 'discarded-interface-text')

    def test_clean_product_and_included_labels_do_not_trigger(self):
        image, mask = self.example()
        ImageDraw.Draw(image).line((190, 100, 320, 100), fill='red', width=3)
        self.assertFalse(photos.assess_graphic_risk(image, mask)['preserve_frame'])

    def test_annotation_recovered_without_filling_its_interior(self):
        for color in ['red', 'blue']:
            image, mask = self.example()
            ImageDraw.Draw(image).rectangle((15, 30, 250, 270), outline=color, width=3)
            overlay, evidence = photos.recover_colored_annotations(image, mask)
            self.assertTrue(evidence['recovered'])
            self.assertEqual(np.asarray(overlay)[30, 100], 255)
            self.assertEqual(np.asarray(overlay)[100, 15], 255)
            self.assertEqual(np.asarray(overlay)[100, 100], 0)
            self.assertEqual(np.asarray(overlay)[0, 0], 0)

    def test_busy_background_refuses_annotation_recovery(self):
        image = Image.new('RGB', (400, 300), '#808080')
        ImageDraw.Draw(image).rectangle((15, 30, 250, 270), outline='red', width=3)
        overlay, evidence = photos.recover_colored_annotations(image, Image.new('L', image.size))
        self.assertIsNone(overlay)
        self.assertFalse(evidence['recovered'])


if __name__ == '__main__':
    unittest.main()
