from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


COORDINATE_FRAME = "normalized_full_image_px"
TOPOLOGY_SCHEMA = "GeoGridTopologyCandidate/v1"
REGIONS_SCHEMA = "GeoGridReviewRegions/v1"
PROJECT_SCHEMA = "GeoGridReviewProject/v1"
SESSION_SCHEMA = "GeoGridReviewSession/v1"
REVIEWED_MESH_SCHEMA = "ReviewedMesh/v1"


class ContractError(ValueError):
    """Raised when an input or review state violates a GeoGrid contract."""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON 顶层必须是对象：{path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _matrix_for_exif_orientation(orientation: int, width: int, height: int) -> list[list[float]]:
    matrices = {
        1: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        2: [[-1, 0, width - 1], [0, 1, 0], [0, 0, 1]],
        3: [[-1, 0, width - 1], [0, -1, height - 1], [0, 0, 1]],
        4: [[1, 0, 0], [0, -1, height - 1], [0, 0, 1]],
        5: [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
        6: [[0, -1, height - 1], [1, 0, 0], [0, 0, 1]],
        7: [[0, -1, height - 1], [-1, 0, width - 1], [0, 0, 1]],
        8: [[0, 1, 0], [-1, 0, width - 1], [0, 0, 1]],
    }
    return matrices.get(orientation, matrices[1])


def _load_roi_polygon(path: Path | None, width: int, height: int) -> list[list[float]]:
    if path is None:
        return []
    payload = read_json(path)
    polygon = payload.get("roi_polygon_normalized_full_image_px", payload.get("roi_polygon"))
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ContractError("ROI JSON 必须包含至少三个点的 roi_polygon_normalized_full_image_px")
    normalized: list[list[float]] = []
    for point in polygon:
        if not isinstance(point, list) or len(point) != 2:
            raise ContractError("ROI 点必须是 [x, y]")
        x, y = float(point[0]), float(point[1])
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < width and 0 <= y < height):
            raise ContractError(f"ROI 点超出归一化图像范围：{point}")
        normalized.append([x, y])
    return normalized


def _node_id(node: dict[str, Any]) -> str:
    value = node.get("candidate_id", node.get("node_id"))
    if not isinstance(value, str) or not value:
        raise ContractError("候选节点缺少 candidate_id/node_id")
    return value


def _auto_node_decisions(status: str) -> tuple[str, str]:
    if status == "observed":
        return "accepted", "accepted"
    if status == "rejected_evidence":
        return "rejected", "rejected"
    if status == "duplicate_conflict":
        return "pending", "rejected"
    return "pending", "accepted"


def _auto_edge_decisions(status: str) -> tuple[str, str]:
    if status == "observed":
        return "accepted", "accepted"
    if status == "topology_conflict":
        return "pending", "rejected"
    return "pending", "accepted"


def _validate_input_contracts(
    topology: dict[str, Any], regions: dict[str, Any], image_size: tuple[int, int]
) -> None:
    if topology.get("schema") != TOPOLOGY_SCHEMA:
        raise ContractError(f"拓扑 schema 必须为 {TOPOLOGY_SCHEMA}")
    if regions.get("schema") != REGIONS_SCHEMA:
        raise ContractError(f"复核区 schema 必须为 {REGIONS_SCHEMA}")
    if topology.get("coordinate_frame") != COORDINATE_FRAME:
        raise ContractError(f"拓扑坐标系必须为 {COORDINATE_FRAME}")
    if regions.get("coordinate_frame") != COORDINATE_FRAME:
        raise ContractError(f"复核区坐标系必须为 {COORDINATE_FRAME}")

    width, height = image_size
    node_ids: set[str] = set()
    for node in topology.get("nodes", []):
        identifier = _node_id(node)
        if identifier in node_ids:
            raise ContractError(f"重复节点 ID：{identifier}")
        node_ids.add(identifier)
        x, y = float(node["x"]), float(node["y"])
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < width and 0 <= y < height):
            raise ContractError(f"节点 {identifier} 超出归一化图像范围")

    edge_ids: set[str] = set()
    for edge in topology.get("edges", []):
        identifier = edge.get("edge_id")
        if not isinstance(identifier, str) or not identifier or identifier in edge_ids:
            raise ContractError(f"边 ID 缺失或重复：{identifier}")
        edge_ids.add(identifier)
        if edge.get("from_node_id") not in node_ids or edge.get("to_node_id") not in node_ids:
            raise ContractError(f"边 {identifier} 引用了不存在的节点")

    item_ids: set[str] = set()
    for item in regions.get("review_items", []):
        identifier = item.get("review_item_id")
        if not isinstance(identifier, str) or not identifier or identifier in item_ids:
            raise ContractError(f"复核项 ID 缺失或重复：{identifier}")
        item_ids.add(identifier)
        kind, _, target_id = identifier.partition(":")
        valid = target_id in node_ids if kind == "node" else target_id in edge_ids if kind == "edge" else False
        if not valid:
            raise ContractError(f"复核项 {identifier} 引用了不存在的对象")

    assigned: set[str] = set()
    for region in regions.get("review_regions", []):
        region_id = region.get("review_region_id")
        bbox = region.get("bbox_full_xyxy")
        if not isinstance(region_id, str) or not region_id:
            raise ContractError("复核区缺少 review_region_id")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ContractError(f"复核区 {region_id} 缺少 bbox_full_xyxy")
        x0, y0, x1, y1 = map(float, bbox)
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ContractError(f"复核区 {region_id} 超出图像范围")
        for item_id in region.get("review_item_ids", []):
            if item_id not in item_ids:
                raise ContractError(f"复核区 {region_id} 引用了不存在的复核项 {item_id}")
            if item_id in assigned:
                raise ContractError(f"复核项被重复分区：{item_id}")
            assigned.add(item_id)
    if assigned != item_ids:
        missing = sorted(item_ids - assigned)
        raise ContractError(f"存在未分区复核项：{missing[:5]}")


def initialize_review_project(
    project_dir: Path,
    image_path: Path,
    topology_path: Path,
    regions_path: Path,
    roi_path: Path | None = None,
    compression_percent: float = 15.0,
) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    image_path = image_path.resolve()
    topology_path = topology_path.resolve()
    regions_path = regions_path.resolve()
    roi_path = roi_path.resolve() if roi_path else None

    for path, label in (
        (image_path, "照片"),
        (topology_path, "候选拓扑"),
        (regions_path, "复核区"),
    ):
        if not path.is_file():
            raise ContractError(f"{label}文件不存在：{path}")
    if roi_path and not roi_path.is_file():
        raise ContractError(f"ROI 文件不存在：{roi_path}")
    if not math.isfinite(compression_percent) or compression_percent < 0:
        raise ContractError("轴向压缩量必须是非负有限数")
    if project_dir.exists() and any(project_dir.iterdir()):
        raise ContractError(f"项目目录必须不存在或为空：{project_dir}")

    source_hash = sha256_file(image_path)
    with Image.open(image_path) as opened:
        original_size = opened.size
        orientation = int(opened.getexif().get(274, 1) or 1)
        normalized = ImageOps.exif_transpose(opened).convert("RGB")
        normalized_size = normalized.size
        topology = read_json(topology_path)
        regions = read_json(regions_path)
        _validate_input_contracts(topology, regions, normalized_size)
        roi_polygon = _load_roi_polygon(roi_path, *normalized_size)

        source_dir = project_dir / "source"
        assets_dir = project_dir / "assets"
        state_dir = project_dir / "state"
        exports_dir = project_dir / "exports"
        for directory in (source_dir, assets_dir, state_dir, exports_dir):
            directory.mkdir(parents=True, exist_ok=True)

        source_suffix = image_path.suffix.lower() or ".img"
        source_copy = source_dir / f"source_image{source_suffix}"
        topology_copy = source_dir / "topology_candidate.json"
        regions_copy = source_dir / "review_regions.json"
        normalized_path = assets_dir / "normalized.png"
        shutil.copyfile(image_path, source_copy)
        shutil.copyfile(topology_path, topology_copy)
        shutil.copyfile(regions_path, regions_copy)
        normalized.save(normalized_path, format="PNG")

    copied_hash = sha256_file(source_copy)
    if copied_hash != source_hash:
        raise ContractError("照片工作副本哈希与源文件不一致")

    review_items = {item["review_item_id"]: item for item in regions.get("review_items", [])}
    nodes: list[dict[str, Any]] = []
    for candidate in topology.get("nodes", []):
        identifier = _node_id(candidate)
        status = str(candidate.get("status", "unknown"))
        decision, suggestion = _auto_node_decisions(status)
        nodes.append(
            {
                "node_id": identifier,
                "x": float(candidate["x"]),
                "y": float(candidate["y"]),
                "original_x": float(candidate["x"]),
                "original_y": float(candidate["y"]),
                "grid_row": candidate.get("topology_row_visible"),
                "grid_col": candidate.get("topology_col_visible"),
                "source": candidate.get("source", "automatic_candidate"),
                "auto_status": status,
                "decision": decision,
                "suggested_decision": suggestion,
                "review_item_id": f"node:{identifier}" if f"node:{identifier}" in review_items else None,
            }
        )

    normalization_notes: list[dict[str, Any]] = []
    nodes_by_grid_index: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for node in nodes:
        row, col = node.get("grid_row"), node.get("grid_col")
        if (
            isinstance(row, int)
            and not isinstance(row, bool)
            and isinstance(col, int)
            and not isinstance(col, bool)
            and node["auto_status"] != "rejected_evidence"
        ):
            nodes_by_grid_index.setdefault((row, col), []).append(node)
    for (row, col), same_index_nodes in nodes_by_grid_index.items():
        if len(same_index_nodes) < 2:
            continue
        direct_candidates = [node for node in same_index_nodes if node["source"] != "topology_surrounded_gap"]
        inferred_candidates = [node for node in same_index_nodes if node["source"] == "topology_surrounded_gap"]
        if direct_candidates and inferred_candidates:
            for node in inferred_candidates:
                node["suggested_decision"] = "rejected"
            normalization_notes.append(
                {
                    "rule": "reject_inferred_default_when_direct_candidate_occupies_same_grid_index",
                    "grid_row": row,
                    "grid_col": col,
                    "direct_candidate_ids": [node["node_id"] for node in direct_candidates],
                    "inferred_candidate_ids": [node["node_id"] for node in inferred_candidates],
                }
            )

    edges: list[dict[str, Any]] = []
    for candidate in topology.get("edges", []):
        identifier = candidate["edge_id"]
        status = str(candidate.get("status", "unknown"))
        decision, suggestion = _auto_edge_decisions(status)
        edges.append(
            {
                "edge_id": identifier,
                "from_node_id": candidate["from_node_id"],
                "to_node_id": candidate["to_node_id"],
                "orientation": candidate.get("orientation"),
                "source": "automatic_candidate",
                "auto_status": status,
                "decision": decision,
                "suggested_decision": suggestion,
                "review_item_id": f"edge:{identifier}" if f"edge:{identifier}" in review_items else None,
            }
        )

    review_regions = [
        {
            "review_region_id": region["review_region_id"],
            "priority": region.get("priority", "normal"),
            "bbox_full_xyxy": [float(value) for value in region["bbox_full_xyxy"]],
            "review_item_ids": list(region.get("review_item_ids", [])),
            "review_status": "pending",
            "reviewed_at": None,
        }
        for region in regions.get("review_regions", [])
    ]

    roi_bbox = None
    if roi_polygon:
        xs = [point[0] for point in roi_polygon]
        ys = [point[1] for point in roi_polygon]
        roi_bbox = [min(xs), min(ys), max(xs), max(ys)]

    created_at = utc_now()
    project_id = f"review-{source_hash[:12]}"
    manifest = {
        "schema": PROJECT_SCHEMA,
        "project_id": project_id,
        "created_at": created_at,
        "coordinate_frame": {
            "name": COORDINATE_FRAME,
            "origin": "top-left pixel center of the EXIF-normalized image",
            "x_axis": "right",
            "y_axis": "down",
            "units": "pixel",
        },
        "image": {
            "source_copy": str(source_copy.relative_to(project_dir)).replace("\\", "/"),
            "normalized_copy": str(normalized_path.relative_to(project_dir)).replace("\\", "/"),
            "source_sha256": source_hash,
            "source_copy_sha256": copied_hash,
            "normalized_sha256": sha256_file(normalized_path),
            "original_size_width_height": list(original_size),
            "normalized_size_width_height": list(normalized_size),
            "exif_orientation": orientation,
            "source_to_normalized_matrix_3x3": _matrix_for_exif_orientation(
                orientation, original_size[0], original_size[1]
            ),
        },
        "inputs": {
            "topology_candidate": str(topology_copy.relative_to(project_dir)).replace("\\", "/"),
            "topology_candidate_sha256": sha256_file(topology_copy),
            "review_regions": str(regions_copy.relative_to(project_dir)).replace("\\", "/"),
            "review_regions_sha256": sha256_file(regions_copy),
        },
        "roi": {
            "polygon_normalized_full_image_px": roi_polygon,
            "bbox_normalized_full_image_px": roi_bbox,
            "source": "provided_at_project_initialization" if roi_polygon else "not_provided",
        },
        "specimen": {
            "nominal_height_mm": 150.0,
            "nominal_width_mm": 75.0,
            "grid_spacing_mm": 5.0,
            "axial_compression_percent": float(compression_percent),
            "image_vertical_direction": "sigma_1",
            "image_horizontal_direction": "sigma_3",
            "image_normal_direction": "sigma_2",
        },
    }
    session = {
        "schema": SESSION_SCHEMA,
        "project_id": project_id,
        "coordinate_frame": COORDINATE_FRAME,
        "candidate_schema": topology["schema"],
        "regions_schema": regions["schema"],
        "revision": 0,
        "updated_at": created_at,
        "nodes": nodes,
        "edges": edges,
        "review_regions": review_regions,
        "final_review": {"status": "in_progress", "reviewed_at": None},
        "manual_node_counter": 0,
        "manual_edge_counter": 0,
        "input_normalization_notes": normalization_notes,
        "history": [],
        "history_cursor": 0,
    }
    write_json_atomic(project_dir / "project.json", manifest)
    write_json_atomic(project_dir / "state" / "review_session.json", session)
    return deepcopy(manifest)


def load_project(project_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    project_dir = project_dir.resolve()
    manifest = read_json(project_dir / "project.json")
    session = read_json(project_dir / "state" / "review_session.json")
    if manifest.get("schema") != PROJECT_SCHEMA:
        raise ContractError(f"项目 schema 必须为 {PROJECT_SCHEMA}")
    if session.get("schema") != SESSION_SCHEMA:
        raise ContractError(f"校核会话 schema 必须为 {SESSION_SCHEMA}")
    if manifest.get("project_id") != session.get("project_id"):
        raise ContractError("项目清单与校核会话的 project_id 不一致")
    return manifest, session
