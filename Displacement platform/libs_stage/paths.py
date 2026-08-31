from __future__ import annotations

from dataclasses import dataclass
from math import ceil, cos, pi, sin


@dataclass(frozen=True)
class Point:
    x: float
    y: float


def offsets_from_start(points: list[Point]) -> list[Point]:
    """将一条几何路径转换为以首点为 (0, 0) 的相对路径。"""
    if not points:
        return []
    start = points[0]
    return [Point(p.x - start.x, p.y - start.y) for p in points]


def translate(points: list[Point], x: float, y: float) -> list[Point]:
    return [Point(p.x + x, p.y + y) for p in points]


def filled_circle(cx: float, cy: float, radius: float, ring_spacing: float, point_spacing: float) -> list[Point]:
    """从圆心开始，由内向外生成同心圆扫描点。"""
    if radius <= 0:
        raise ValueError("扫描半径必须大于 0")
    if ring_spacing <= 0 or point_spacing <= 0:
        raise ValueError("环间距和点间距必须大于 0")
    ring_count = max(1, ceil(radius / ring_spacing))
    # 均分实际环距，使最外圈精确落在用户设定的半径上。
    radii = [radius * i / ring_count for i in range(1, ring_count + 1)]
    points: list[Point] = [Point(cx, cy)]
    for ring_index, current_radius in enumerate(radii):
        count = max(6, ceil(2 * pi * current_radius / point_spacing))
        # 相邻圆环反向扫描，减少换环时的空行程。
        indices = range(count + 1) if ring_index % 2 == 0 else range(count, -1, -1)
        points.extend(Point(cx + current_radius * cos(2 * pi * i / count),
                           cy + current_radius * sin(2 * pi * i / count)) for i in indices)
    return points


def raster(cx: float, cy: float, rows: int, points_per_row: int, row_spacing: float, point_spacing: float) -> list[Point]:
    if rows < 2 or points_per_row < 2:
        raise ValueError("栅格纵向行数和横向点数至少为 2")
    if row_spacing <= 0 or point_spacing <= 0:
        raise ValueError("行间距和点间距必须大于 0")
    width = (points_per_row - 1) * point_spacing
    height = (rows - 1) * row_spacing
    result: list[Point] = []
    for row in range(rows):
        y = cy - height / 2 + height * row / (rows - 1)
        xs = [cx - width / 2 + width * i / (points_per_row - 1) for i in range(points_per_row)]
        if row % 2:
            xs.reverse()
        result.extend(Point(x, y) for x in xs)
    return result


def validate_limits(points: list[Point], x_limits: tuple[float, float], y_limits: tuple[float, float]) -> None:
    for index, point in enumerate(points, 1):
        if not x_limits[0] <= point.x <= x_limits[1]:
            raise ValueError(f"第 {index} 点 X={point.x:.3f} 超出软限位 {x_limits}")
        if not y_limits[0] <= point.y <= y_limits[1]:
            raise ValueError(f"第 {index} 点 Y={point.y:.3f} 超出软限位 {y_limits}")
