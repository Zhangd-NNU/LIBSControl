import unittest
from math import hypot

from libs_stage.paths import Point, filled_circle, offsets_from_start, raster, translate, validate_limits


class PathTests(unittest.TestCase):
    def test_filled_circle_expands_from_center(self):
        points = filled_circle(2, 3, 5, 2, 1)
        radii = [hypot(p.x-2, p.y-3) for p in points]
        self.assertEqual(points[0], Point(2, 3))
        self.assertAlmostEqual(radii[-1], 5)
        self.assertTrue(any(abs(r - 10/3) < 1e-9 for r in radii))
        self.assertTrue(any(abs(r - 5/3) < 1e-9 for r in radii))

    def test_raster_is_serpentine(self):
        points = raster(0, 0, 3, 3, 2, 5)
        self.assertEqual(len(points), 9)
        self.assertEqual([p.x for p in points[:3]], [-5, 0, 5])
        self.assertEqual([p.x for p in points[3:6]], [5, 0, -5])

    def test_limits(self):
        with self.assertRaises(ValueError):
            validate_limits(filled_circle(0, 0, 10, 2, 1), (-5, 5), (-5, 5))

    def test_relative_path_starts_at_current_position(self):
        relative = offsets_from_start(raster(0, 0, 3, 3, 2, 5))
        self.assertEqual(relative[0], Point(0, 0))
        absolute = translate(relative, 12.5, -3.0)
        self.assertEqual(absolute[0], Point(12.5, -3.0))


if __name__ == "__main__": unittest.main()
