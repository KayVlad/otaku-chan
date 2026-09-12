import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from helpers import natural_sort_key
from libraries import get_volumes, visible_dirs, volume_images


class NaturalSortTests(unittest.TestCase):
    def test_numbers_and_leading_words(self):
        names = ['Volume 10', 'Volume 2', 'Volume 1', 'The Finale', 'A Prologue']
        self.assertEqual(sorted(names, key=natural_sort_key),
                         ['A Prologue', 'The Finale', 'Volume 1', 'Volume 2', 'Volume 10'])

    def test_equal_numbers_have_deterministic_order(self):
        names = ['volume 2', 'Volume 02', 'Volume 2', 'Volume 002']
        self.assertEqual(sorted(names, key=natural_sort_key),
                         sorted(reversed(names), key=natural_sort_key))
        self.assertLess(natural_sort_key('Volume 02'), natural_sort_key('Volume 10'))

    def test_detail_and_navigation_share_volume_order(self):
        with tempfile.TemporaryDirectory(prefix='otaku-sort-') as directory:
            root = Path(directory)
            for name in ['Volume 10', 'Volume 2', 'Volume 1', '.cache']:
                (root / name).mkdir()
            expected = ['Volume 1', 'Volume 2', 'Volume 10']
            self.assertEqual([p.name for p in visible_dirs(root)], expected)
            self.assertEqual([v['name'] for v in get_volumes(root)], expected)
            for name in ['page10.png', 'page2.png', 'page1.png']:
                (root / 'Volume 1' / name).touch()
            self.assertEqual([p.name for p in volume_images(root / 'Volume 1')],
                             ['page1.png', 'page2.png', 'page10.png'])
