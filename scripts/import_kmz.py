#!/usr/bin/env python3
"""Import the supplied hiking KMZ without dependencies or network access.

KML coordinates are read and written as Decimal values so their original
precision is retained. Descriptions are converted to inert plain text; neither
HTML nor other files from an archive are executed or extracted.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile


EARTH_RADIUS_KM = 6371.0088
GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}


def tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def child(element: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in element if tag(item) == name), None)


def child_text(element: ET.Element, name: str) -> str:
    item = child(element, name)
    return "" if item is None else "".join(item.itertext())


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, name: str, attrs: list) -> None:
        if name in {"script", "style"}:
            self.skip += 1
        elif name in {"p", "br", "div", "li"} and not self.skip:
            self.parts.append(" ")

    def handle_endtag(self, name: str) -> None:
        if name in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        elif name in {"p", "div", "li"} and not self.skip:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def plain_text(raw: str) -> str:
    parser = PlainText()
    parser.feed(raw)
    parser.close()
    return " ".join("".join(parser.parts).split())


def json_text(value, level: int = 0) -> str:
    """Write standard JSON, preserving source decimals as numeric literals."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Coordinates must be finite")
        return str(value)
    if isinstance(value, dict):
        indent = "  " * (level + 1)
        pairs = [json.dumps(k) + ": " + json_text(v, level + 1) for k, v in value.items()]
        return "{\n" + indent + (",\n" + indent).join(pairs) + "\n" + "  " * level + "}"
    if isinstance(value, (list, tuple)):
        # Compact arrays keep long route coordinate sequences manageable.
        return "[" + ", ".join(json_text(item, level) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def coordinates(element: ET.Element) -> list[list[Decimal]]:
    result = []
    for item in child_text(element, "coordinates").split():
        numbers = [Decimal(part) for part in item.split(",")]
        if len(numbers) not in {2, 3} or not all(n.is_finite() for n in numbers):
            raise ValueError(f"Invalid coordinate: {item}")
        if not (-180 <= numbers[0] <= 180 and -90 <= numbers[1] <= 90):
            raise ValueError(f"Out-of-range coordinate: {item}")
        result.append(numbers)
    return result


def geometry(element: ET.Element) -> dict:
    kind = tag(element)
    if kind == "Point":
        positions = coordinates(element)
        if len(positions) != 1:
            raise ValueError("A point must contain exactly one coordinate")
        return {"type": "Point", "coordinates": positions[0]}
    if kind in {"LineString", "LinearRing"}:
        positions = coordinates(element)
        if len(positions) < 2:
            raise ValueError("A line must contain at least two coordinates")
        return {"type": "LineString", "coordinates": positions}
    if kind == "Polygon":
        rings = []
        for boundary_name in ("outerBoundaryIs", "innerBoundaryIs"):
            for boundary in element:
                if tag(boundary) == boundary_name:
                    ring = child(boundary, "LinearRing")
                    if ring is None:
                        raise ValueError("Polygon boundary has no LinearRing")
                    positions = coordinates(ring)
                    if len(positions) < 4 or positions[0] != positions[-1]:
                        raise ValueError("Polygon rings must be closed and contain four coordinates")
                    rings.append(positions)
        if not rings:
            raise ValueError("Polygon has no rings")
        return {"type": "Polygon", "coordinates": rings}
    if kind == "MultiGeometry":
        return {"type": "GeometryCollection", "geometries": [
            geometry(item) for item in element if tag(item) in GEOMETRY_TAGS
        ]}
    raise ValueError(f"Unsupported geometry: {kind}")


def all_positions(shape: dict):
    kind = shape["type"]
    if kind == "GeometryCollection":
        for part in shape["geometries"]:
            yield from all_positions(part)
    elif kind == "Point":
        yield shape["coordinates"]
    elif kind == "Polygon":
        for ring in shape["coordinates"]:
            yield from ring
    else:
        yield from shape["coordinates"]


def haversine(a, b) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (float(a[0]), float(a[1]), float(b[0]), float(b[1])))
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1, max(0, value))))


def line_distance(shape: dict) -> float | None:
    if shape["type"] == "LineString":
        points = shape["coordinates"]
        return sum(haversine(a, b) for a, b in zip(points, points[1:]))
    if shape["type"] == "GeometryCollection":
        distances = [line_distance(part) for part in shape["geometries"]]
        lines = [distance for distance in distances if distance is not None]
        return sum(lines) if lines else None
    return None


def point_to_line_meters(point, line) -> float:
    """Approximate local point-to-segment distance for source quality checks."""
    scale = EARTH_RADIUS_KM * 1000 * math.pi / 180
    longitude_scale = scale * math.cos(math.radians(float(point[1])))
    best = math.inf
    for start, end in zip(line, line[1:]):
        ax = (float(start[0]) - float(point[0])) * longitude_scale
        ay = (float(start[1]) - float(point[1])) * scale
        bx = (float(end[0]) - float(point[0])) * longitude_scale
        by = (float(end[1]) - float(point[1])) * scale
        dx, dy = bx - ax, by - ay
        squared_length = dx * dx + dy * dy
        fraction = min(1, max(0, -(ax * dx + ay * dy) / squared_length)) if squared_length else 0
        best = min(best, math.hypot(ax + fraction * dx, ay + fraction * dy))
    return best


def category_for(name: str, folders: list[str], shape: dict, trail: str | None) -> str:
    kind = shape["type"]
    if trail and kind == "LineString" and name in {"Day 1", "Day 2"}:
        return "main-trail"
    if trail and kind == "Point":
        return "trail-waypoint"
    if name.casefold() == "wheel chair route" and kind == "LineString":
        return "secondary-trail"
    if "Service Roads" in folders:
        return "service-road"
    if "Water" in folders:
        return "water"
    if name in {"Fossil", "Dinosaur Footprint"}:
        return "landmark"
    if name in {"Lodge", "Main Entrance"}:
        return "facility"
    return "other"


def parse_features(root: ET.Element) -> list[dict]:
    features = []
    used_ids: Counter = Counter()

    def walk(element: ET.Element, folders: list[str]) -> None:
        if tag(element) == "Folder":
            folders = folders + [child_text(element, "name")]
        if tag(element) == "Placemark":
            shapes = [geometry(item) for item in element if tag(item) in GEOMETRY_TAGS]
            if not shapes:
                raise ValueError(f"Placemark has no supported geometry: {child_text(element, 'name')}")
            shape = shapes[0] if len(shapes) == 1 else {"type": "GeometryCollection", "geometries": shapes}
            name = child_text(element, "name")
            trail = "day1" if "Day 1" in folders else "day2" if "Day 2" in folders else None
            number = re.match(r"^(\d+)\.", name) if shape["type"] == "Point" else None
            category = category_for(name, folders, shape, trail)
            identity = json_text([folders, name, shape])
            stem = (re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "feature") + "-" + sha256(identity.encode()).hexdigest()[:10]
            used_ids[stem] += 1
            feature_id = stem if used_ids[stem] == 1 else f"{stem}-{used_ids[stem]}"
            distance = line_distance(shape)
            features.append({
                "type": "Feature", "id": feature_id,
                "properties": {
                    "id": feature_id, "name": name, "folders": folders,
                    "category": category, "trail": trail,
                    "distanceKm": round(distance, 6) if distance is not None else None,
                    "waypointNumber": int(number.group(1)) if number else None,
                    "photo": None,
                    "description": plain_text(child_text(element, "description")),
                },
                "geometry": shape,
            })
            return
        for item in element:
            walk(item, folders)

    walk(root, [])
    return features


def summarize(features: list[dict], source_name: str, source_hash: str, member: str) -> dict:
    positions = [point for feature in features for point in all_positions(feature["geometry"])]
    if not positions:
        raise ValueError("KML contains no coordinates")
    altitude = [point[2] for point in positions if len(point) == 3]
    bounds = [min(p[0] for p in positions), min(p[1] for p in positions),
              max(p[0] for p in positions), max(p[1] for p in positions)]
    trails = {}
    main_lines = {}
    for trail in ("day1", "day2"):
        lines = [f for f in features if f["properties"]["category"] == "main-trail" and f["properties"]["trail"] == trail]
        pins = [f for f in features if f["properties"]["category"] == "trail-waypoint" and f["properties"]["trail"] == trail]
        numbered = sorted([f for f in pins if f["properties"]["waypointNumber"] is not None], key=lambda f: f["properties"]["waypointNumber"])
        info = {
            "name": "Day 1" if trail == "day1" else "Day 2",
            "distanceKm": round(sum(line_distance(f["geometry"]) or 0 for f in lines), 6),
            "lineCount": len(lines), "waypointCount": len(pins),
            "numberedWaypointCount": len(numbered),
            "waypointNumbers": [f["properties"]["waypointNumber"] for f in numbered],
            "lineFeatureIds": [f["id"] for f in lines],
        }
        if len(lines) == 1:
            points = lines[0]["geometry"]["coordinates"]
            main_lines[trail] = points
            info.update({"coordinateCount": len(points), "startCoordinate": points[0], "endCoordinate": points[-1],
                         "endToStartGapMeters": round(haversine(points[0], points[-1]) * 1000, 1)})
            if numbered:
                info["firstNumberedWaypointToLineStartMeters"] = round(haversine(numbered[0]["geometry"]["coordinates"], points[0]) * 1000, 1)
                info["lastNumberedWaypointToLineEndMeters"] = round(haversine(numbered[-1]["geometry"]["coordinates"], points[-1]) * 1000, 1)
                info["maximumNumberedWaypointDistanceFromLineMetersApprox"] = round(max(
                    point_to_line_meters(pin["geometry"]["coordinates"], points) for pin in numbered
                ), 1)
        trails[trail] = info
    quality = "not provided" if not altitude else "all zero; no usable elevation data" if all(a == 0 for a in altitude) else "source values preserved; elevation accuracy unverified"
    summary = {
        "sourceFile": source_name, "sourceSha256": source_hash, "kmlMember": member,
        "featureCount": len(features),
        "geometryCounts": dict(sorted(Counter(f["geometry"]["type"] for f in features).items())),
        "categoryCounts": dict(sorted(Counter(f["properties"]["category"] for f in features).items())),
        "coordinateCount": len(positions), "bounds": bounds,
        "altitude": {"coordinateCount": len(altitude), "missingCount": len(positions) - len(altitude),
                     "min": min(altitude) if altitude else None, "max": max(altitude) if altitude else None,
                     "quality": quality},
        "photoCount": 0, "describedFeatureCount": sum(bool(f["properties"]["description"]) for f in features),
        "trails": trails,
        "distanceMethod": "Sum of WGS84 coordinate haversine segments on a sphere of radius 6371.0088 km; excludes elevation and is not a surveyed distance.",
        "sourceNotes": [
            "Original feature names, folder hierarchy, coordinate decimal precision, and geometry are preserved.",
            "Source waypoint numbers are retained; they do not establish travel direction.",
            "Numbered waypoints may lie between route endpoints; their distances to endpoints do not indicate route gaps.",
            "Route lines are not snapped, extended, or closed during import.",
            "A source label for water or wheelchair use does not verify drinking-water safety or accessibility.",
            "Embedded image references, if present, are not imported as photos automatically.",
        ],
    }
    if "day1" in main_lines and "day2" in main_lines:
        summary["day1EndToDay2StartMeters"] = round(haversine(main_lines["day1"][-1], main_lines["day2"][0]) * 1000, 1)
    return summary


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=repo / "data" / "Holhoek.kmz")
    parser.add_argument("--output-dir", type=Path, default=repo / "data")
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    with ZipFile(args.source) as archive:
        members = [entry for entry in archive.infolist() if entry.filename.lower().endswith(".kml")]
        preferred = next((entry for entry in members if entry.filename == "doc.kml"), None)
        if preferred is None and len(members) != 1:
            raise ValueError("Expected doc.kml or exactly one KML file in the archive")
        member = preferred or members[0]
        if member.file_size > 64 * 1024 * 1024:
            raise ValueError("KML file exceeds 64 MiB import limit")
        root = ET.fromstring(archive.read(member))
    features = parse_features(root)
    summary = summarize(features, args.source.name, sha256(source_bytes).hexdigest(), member.filename)
    collection = {"type": "FeatureCollection", "name": "Holhoek", "bbox": summary["bounds"],
                  "source": summary, "features": features}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "holhoek.geojson").write_text(json_text(collection) + "\n", encoding="utf-8")
    (args.output_dir / "source-summary.json").write_text(json_text(summary) + "\n", encoding="utf-8")
    retained_source = args.output_dir / "Holhoek.kmz"
    if args.source.resolve() != retained_source.resolve():
        shutil.copyfile(args.source, retained_source)
    print(json.dumps({"features": len(features), "geometryCounts": summary["geometryCounts"],
                      "trails": {k: {"distanceKm": v["distanceKm"], "waypoints": v["waypointCount"]} for k, v in summary["trails"].items()},
                      "outputDirectory": str(args.output_dir)}, indent=2))


if __name__ == "__main__":
    main()
