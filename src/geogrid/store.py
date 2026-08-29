from __future__ import annotations

import math
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from geogrid.domain import (
    COORDINATE_FRAME,
    REVIEWED_MESH_SCHEMA,
    ContractError,
    load_project,
    read_json,
    utc_now,
    write_json_atomic,
)


DECISIONS = {"accepted", "rejected", "pending"}


class RevisionConflict(ContractError):
    """Raised when a browser tries to edit a stale review revision."""


class ReviewStore:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir.resolve()
        self._lock = threading.RLock()
        self.manifest, self.session = load_project(self.project_dir)

    @property
    def session_path(self) -> Path:
        return self.project_dir / "state" / "review_session.json"

    @property
    def image_path(self) -> Path:
        return self.project_dir / self.manifest["image"]["normalized_copy"]

    def reload(self) -> None:
        with self._lock:
            self.manifest, self.session = load_project(self.project_dir)

    def _persist(self) -> None:
        self.session["updated_at"] = utc_now()
        write_json_atomic(self.session_path, self.session)

    @staticmethod
    def _id_key(collection: str) -> str:
        return {
            "nodes": "node_id",
            "edges": "edge_id",
            "review_regions": "review_region_id",
        }[collection]

    def _find(self, collection: str, identifier: str) -> dict[str, Any]:
        key = self._id_key(collection)
        for item in self.session[collection]:
            if item.get(key) == identifier:
                return item
        raise ContractError(f"找不到 {collection} 对象：{identifier}")

    @staticmethod
    def _field_change(
        collection: str, identifier: str | None, field: str, before: Any, after: Any
    ) -> dict[str, Any]:
        return {
            "kind": "field",
            "collection": collection,
            "id": identifier,
            "field": field,
            "before": deepcopy(before),
            "after": deepcopy(after),
        }

    @staticmethod
    def _add_change(collection: str, value: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "add", "collection": collection, "value": deepcopy(value)}

    def _apply_change(self, change: dict[str, Any], forward: bool) -> None:
        kind = change["kind"]
        if kind == "field":
            value = deepcopy(change["after"] if forward else change["before"])
            if change["collection"] == "session":
                self.session[change["field"]] = value
            else:
                target = self._find(change["collection"], change["id"])
                target[change["field"]] = value
            return
        if kind == "add":
            collection = change["collection"]
            value = deepcopy(change["value"])
            identifier = value[self._id_key(collection)]
            if forward:
                self.session[collection].append(value)
            else:
                existing = self._find(collection, identifier)
                self.session[collection].remove(existing)
            return
        raise ContractError(f"未知历史变更类型：{kind}")

    def _image_size(self) -> tuple[int, int]:
        width, height = self.manifest["image"]["normalized_size_width_height"]
        return int(width), int(height)

    def _validate_xy(self, x: Any, y: Any) -> tuple[float, float]:
        x_value, y_value = float(x), float(y)
        width, height = self._image_size()
        if not (
            math.isfinite(x_value)
            and math.isfinite(y_value)
            and 0 <= x_value < width
            and 0 <= y_value < height
        ):
            raise ContractError("节点坐标必须位于归一化图像范围内")
        return x_value, y_value

    @staticmethod
    def _validate_grid_index(value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ContractError("格网行列必须是整数或空值")
        integer = int(value)
        if float(value) != integer:
            raise ContractError("格网行列必须是整数或空值")
        return integer

    @staticmethod
    def _physical_signed_area_px2(node_ids: list[str], nodes_by_id: dict[str, dict[str, Any]]) -> float:
        image_signed_area_twice = 0.0
        for index, node_id in enumerate(node_ids):
            current = nodes_by_id[node_id]
            following = nodes_by_id[node_ids[(index + 1) % len(node_ids)]]
            image_signed_area_twice += current["x"] * following["y"] - current["y"] * following["x"]
        return -0.5 * image_signed_area_twice

    def _invalidate_final_review(self, changes: list[dict[str, Any]]) -> None:
        current = self.session["final_review"]
        if current.get("status") == "reviewed":
            changes.append(
                self._field_change(
                    "session",
                    None,
                    "final_review",
                    current,
                    {"status": "in_progress", "reviewed_at": None},
                )
            )

    def _build_command(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        changes: list[dict[str, Any]] = []
        description = command_type

        if command_type == "move_node":
            node = self._find("nodes", str(payload.get("node_id", "")))
            x, y = self._validate_xy(payload.get("x"), payload.get("y"))
            if node["x"] != x:
                changes.append(self._field_change("nodes", node["node_id"], "x", node["x"], x))
            if node["y"] != y:
                changes.append(self._field_change("nodes", node["node_id"], "y", node["y"], y))
            description = f"移动节点 {node['node_id']}"

        elif command_type == "set_node_decision":
            node = self._find("nodes", str(payload.get("node_id", "")))
            decision = str(payload.get("decision", ""))
            if decision not in DECISIONS:
                raise ContractError("节点 decision 必须为 accepted/rejected/pending")
            if node["decision"] != decision:
                changes.append(
                    self._field_change("nodes", node["node_id"], "decision", node["decision"], decision)
                )
            description = f"设置节点 {node['node_id']} 为 {decision}"

        elif command_type == "set_node_grid_index":
            node = self._find("nodes", str(payload.get("node_id", "")))
            row = self._validate_grid_index(payload.get("grid_row"))
            col = self._validate_grid_index(payload.get("grid_col"))
            if node.get("grid_row") != row:
                changes.append(
                    self._field_change("nodes", node["node_id"], "grid_row", node.get("grid_row"), row)
                )
            if node.get("grid_col") != col:
                changes.append(
                    self._field_change("nodes", node["node_id"], "grid_col", node.get("grid_col"), col)
                )
            description = f"修改节点 {node['node_id']} 格网索引"

        elif command_type == "add_node":
            x, y = self._validate_xy(payload.get("x"), payload.get("y"))
            row = self._validate_grid_index(payload.get("grid_row"))
            col = self._validate_grid_index(payload.get("grid_col"))
            self.session["manual_node_counter"] += 1
            identifier = f"manual-node-{self.session['manual_node_counter']:04d}"
            node = {
                "node_id": identifier,
                "x": x,
                "y": y,
                "original_x": None,
                "original_y": None,
                "grid_row": row,
                "grid_col": col,
                "source": "manual",
                "auto_status": "manual",
                "decision": "accepted",
                "suggested_decision": "accepted",
                "review_item_id": None,
            }
            changes.append(self._add_change("nodes", node))
            description = f"新增节点 {identifier}"

        elif command_type == "set_edge_decision":
            edge = self._find("edges", str(payload.get("edge_id", "")))
            decision = str(payload.get("decision", ""))
            if decision not in DECISIONS:
                raise ContractError("边 decision 必须为 accepted/rejected/pending")
            if edge["decision"] != decision:
                changes.append(
                    self._field_change("edges", edge["edge_id"], "decision", edge["decision"], decision)
                )
            description = f"设置边 {edge['edge_id']} 为 {decision}"

        elif command_type == "toggle_edge_between":
            first_id = str(payload.get("from_node_id", ""))
            second_id = str(payload.get("to_node_id", ""))
            if first_id == second_id:
                raise ContractError("一条边不能连接同一节点")
            self._find("nodes", first_id)
            self._find("nodes", second_id)
            existing = next(
                (
                    edge
                    for edge in self.session["edges"]
                    if {edge["from_node_id"], edge["to_node_id"]} == {first_id, second_id}
                ),
                None,
            )
            if existing:
                after = "rejected" if existing["decision"] == "accepted" else "accepted"
                changes.append(
                    self._field_change(
                        "edges", existing["edge_id"], "decision", existing["decision"], after
                    )
                )
                description = f"切换边 {existing['edge_id']} 为 {after}"
            else:
                self.session["manual_edge_counter"] += 1
                identifier = f"manual-edge-{self.session['manual_edge_counter']:04d}"
                first = self._find("nodes", first_id)
                second = self._find("nodes", second_id)
                delta_row = (
                    None
                    if first.get("grid_row") is None or second.get("grid_row") is None
                    else second["grid_row"] - first["grid_row"]
                )
                delta_col = (
                    None
                    if first.get("grid_col") is None or second.get("grid_col") is None
                    else second["grid_col"] - first["grid_col"]
                )
                orientation = "horizontal" if delta_row == 0 else "vertical" if delta_col == 0 else None
                edge = {
                    "edge_id": identifier,
                    "from_node_id": first_id,
                    "to_node_id": second_id,
                    "orientation": orientation,
                    "source": "manual",
                    "auto_status": "manual",
                    "decision": "accepted",
                    "suggested_decision": "accepted",
                    "review_item_id": None,
                }
                changes.append(self._add_change("edges", edge))
                description = f"新增边 {identifier}"

        elif command_type == "accept_region_defaults":
            region = self._find("review_regions", str(payload.get("review_region_id", "")))
            for item_id in region["review_item_ids"]:
                kind, _, identifier = item_id.partition(":")
                collection = "nodes" if kind == "node" else "edges"
                item = self._find(collection, identifier)
                if item["decision"] == "pending":
                    changes.append(
                        self._field_change(
                            collection,
                            identifier,
                            "decision",
                            "pending",
                            item["suggested_decision"],
                        )
                    )
            timestamp = utc_now()
            if region["review_status"] != "reviewed":
                changes.append(
                    self._field_change(
                        "review_regions",
                        region["review_region_id"],
                        "review_status",
                        region["review_status"],
                        "reviewed",
                    )
                )
                changes.append(
                    self._field_change(
                        "review_regions",
                        region["review_region_id"],
                        "reviewed_at",
                        region.get("reviewed_at"),
                        timestamp,
                    )
                )
            description = f"接受复核区 {region['review_region_id']} 当前建议"

        elif command_type == "set_final_reviewed":
            errors = self._readiness_errors(require_final=False)
            if errors:
                raise ContractError("尚不能完成最终审核：" + "；".join(errors[:6]))
            current = self.session["final_review"]
            after = {"status": "reviewed", "reviewed_at": utc_now()}
            if current != after:
                changes.append(self._field_change("session", None, "final_review", current, after))
            description = "确认整张网格已完成人工审核"

        else:
            raise ContractError(f"不支持的命令：{command_type}")

        if command_type != "set_final_reviewed":
            self._invalidate_final_review(changes)
        if not changes:
            raise ContractError("该操作没有产生任何变化")
        return {
            "command_type": command_type,
            "description": description,
            "created_at": utc_now(),
            "changes": changes,
        }

    def apply_command(
        self, command_type: str, payload: dict[str, Any], expected_revision: int
    ) -> dict[str, Any]:
        with self._lock:
            if expected_revision != self.session["revision"]:
                raise RevisionConflict(
                    f"浏览器修订号 {expected_revision} 已过期，当前为 {self.session['revision']}"
                )
            command = self._build_command(command_type, payload)
            cursor = self.session["history_cursor"]
            if cursor < len(self.session["history"]):
                self.session["history"] = self.session["history"][:cursor]
            for change in command["changes"]:
                self._apply_change(change, forward=True)
            self.session["history"].append(command)
            self.session["history_cursor"] += 1
            self.session["revision"] += 1
            self._persist()
            return self.state_for_client()

    def undo(self, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            if expected_revision != self.session["revision"]:
                raise RevisionConflict("撤销请求的修订号已过期")
            cursor = self.session["history_cursor"]
            if cursor <= 0:
                raise ContractError("没有可以撤销的操作")
            command = self.session["history"][cursor - 1]
            for change in reversed(command["changes"]):
                self._apply_change(change, forward=False)
            self.session["history_cursor"] -= 1
            self.session["revision"] += 1
            self._persist()
            return self.state_for_client()

    def redo(self, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            if expected_revision != self.session["revision"]:
                raise RevisionConflict("重做请求的修订号已过期")
            cursor = self.session["history_cursor"]
            if cursor >= len(self.session["history"]):
                raise ContractError("没有可以重做的操作")
            command = self.session["history"][cursor]
            for change in command["changes"]:
                self._apply_change(change, forward=True)
            self.session["history_cursor"] += 1
            self.session["revision"] += 1
            self._persist()
            return self.state_for_client()

    def _readiness_errors(self, require_final: bool) -> list[str]:
        errors: list[str] = []
        pending_regions = [
            region["review_region_id"]
            for region in self.session["review_regions"]
            if region["review_status"] != "reviewed"
        ]
        if pending_regions:
            errors.append(f"仍有 {len(pending_regions)} 个复核区未完成")
        pending_nodes = [node["node_id"] for node in self.session["nodes"] if node["decision"] == "pending"]
        pending_edges = [edge["edge_id"] for edge in self.session["edges"] if edge["decision"] == "pending"]
        if pending_nodes:
            errors.append(f"仍有 {len(pending_nodes)} 个节点待决定")
        if pending_edges:
            errors.append(f"仍有 {len(pending_edges)} 条边待决定")
        if not self.manifest["roi"]["polygon_normalized_full_image_px"]:
            errors.append("项目未提供 ROI 多边形")

        accepted_nodes = [node for node in self.session["nodes"] if node["decision"] == "accepted"]
        index_map: dict[tuple[int, int], str] = {}
        for node in accepted_nodes:
            row, col = node.get("grid_row"), node.get("grid_col")
            if not isinstance(row, int) or isinstance(row, bool) or not isinstance(col, int) or isinstance(col, bool):
                errors.append(f"已接受节点 {node['node_id']} 缺少整数格网行列")
                continue
            if (row, col) in index_map:
                errors.append(
                    f"格网索引 ({row}, {col}) 被 {index_map[(row, col)]}/{node['node_id']} 同时占用"
                )
            index_map[(row, col)] = node["node_id"]

        accepted_ids = {node["node_id"] for node in accepted_nodes}
        pairs: set[frozenset[str]] = set()
        nodes_by_id = {node["node_id"]: node for node in self.session["nodes"]}
        for edge in self.session["edges"]:
            if edge["decision"] != "accepted":
                continue
            first_id, second_id = edge["from_node_id"], edge["to_node_id"]
            if first_id not in accepted_ids or second_id not in accepted_ids:
                errors.append(f"已接受边 {edge['edge_id']} 连接了未接受节点")
                continue
            pair = frozenset((first_id, second_id))
            if pair in pairs:
                errors.append(f"节点对 {first_id}/{second_id} 存在重复已接受边")
            pairs.add(pair)
            first, second = nodes_by_id[first_id], nodes_by_id[second_id]
            if all(isinstance(value, int) and not isinstance(value, bool) for value in (
                first.get("grid_row"), first.get("grid_col"), second.get("grid_row"), second.get("grid_col")
            )):
                distance = abs(first["grid_row"] - second["grid_row"]) + abs(
                    first["grid_col"] - second["grid_col"]
                )
                if distance != 1:
                    errors.append(f"已接受边 {edge['edge_id']} 不是格网四邻接")
        for row, col in sorted(index_map):
            top_left = index_map.get((row, col))
            bottom_left = index_map.get((row + 1, col))
            bottom_right = index_map.get((row + 1, col + 1))
            top_right = index_map.get((row, col + 1))
            if not all((top_left, bottom_left, bottom_right, top_right)):
                continue
            boundary_pairs = (
                frozenset((top_left, bottom_left)),
                frozenset((bottom_left, bottom_right)),
                frozenset((bottom_right, top_right)),
                frozenset((top_right, top_left)),
            )
            if not all(pair in pairs for pair in boundary_pairs):
                continue
            cell_nodes = [top_left, bottom_left, bottom_right, top_right]
            if self._physical_signed_area_px2(cell_nodes, nodes_by_id) <= 0:
                errors.append(f"单元 ({row}, {col}) 在当前像素坐标中倒置或退化")
        if require_final and self.session["final_review"].get("status") != "reviewed":
            errors.append("整张网格尚未执行最终审核确认")
        return errors

    def readiness(self) -> dict[str, Any]:
        with self._lock:
            errors = self._readiness_errors(require_final=False)
            return {"ready_for_final_review": not errors, "errors": errors}

    def state_for_client(self) -> dict[str, Any]:
        with self._lock:
            session = deepcopy(self.session)
            history = session.pop("history")
            session["can_undo"] = session["history_cursor"] > 0
            session["can_redo"] = session["history_cursor"] < len(history)
            session["history_length"] = len(history)
            session["last_operation"] = history[session["history_cursor"] - 1]["description"] if session["history_cursor"] else None
            counts = {
                "nodes": len(session["nodes"]),
                "accepted_nodes": sum(node["decision"] == "accepted" for node in session["nodes"]),
                "pending_nodes": sum(node["decision"] == "pending" for node in session["nodes"]),
                "edges": len(session["edges"]),
                "accepted_edges": sum(edge["decision"] == "accepted" for edge in session["edges"]),
                "pending_edges": sum(edge["decision"] == "pending" for edge in session["edges"]),
                "reviewed_regions": sum(
                    region["review_status"] == "reviewed" for region in session["review_regions"]
                ),
                "regions": len(session["review_regions"]),
            }
            return {
                "manifest": deepcopy(self.manifest),
                "session": session,
                "counts": counts,
                "readiness": self.readiness(),
                "image_url": "/api/image",
            }

    def export_reviewed_mesh(self) -> tuple[dict[str, Any], Path]:
        with self._lock:
            errors = self._readiness_errors(require_final=True)
            if errors:
                raise ContractError("不能导出 ReviewedMesh/v1：" + "；".join(errors[:10]))

            roi_bbox = self.manifest["roi"]["bbox_normalized_full_image_px"]
            roi_x0, roi_y0 = roi_bbox[0], roi_bbox[1]
            spacing = float(self.manifest["specimen"]["grid_spacing_mm"])
            accepted_source_nodes = [
                node for node in self.session["nodes"] if node["decision"] == "accepted"
            ]
            nodes = []
            for node in sorted(accepted_source_nodes, key=lambda item: (item["grid_row"], item["grid_col"])):
                nodes.append(
                    {
                        "node_id": node["node_id"],
                        "grid_index": {"row": node["grid_row"], "col": node["grid_col"]},
                        "normalized_full_image_px": {"x": node["x"], "y": node["y"]},
                        "roi_local_px": {"x": node["x"] - roi_x0, "y": node["y"] - roi_y0},
                        "nominal_initial_mm": {
                            "x": node["grid_col"] * spacing,
                            "y_down_from_visible_top": node["grid_row"] * spacing,
                        },
                        "source": node["source"],
                        "review_status": "accepted",
                        "provenance": {
                            "auto_status": node["auto_status"],
                            "review_item_id": node.get("review_item_id"),
                            "position_modified": node.get("original_x") is None
                            or node["x"] != node.get("original_x")
                            or node["y"] != node.get("original_y"),
                        },
                    }
                )

            accepted_ids = {node["node_id"] for node in accepted_source_nodes}
            edges = [
                {
                    "edge_id": edge["edge_id"],
                    "from_node_id": edge["from_node_id"],
                    "to_node_id": edge["to_node_id"],
                    "source": edge["source"],
                    "review_status": "accepted",
                    "provenance": {
                        "auto_status": edge["auto_status"],
                        "review_item_id": edge.get("review_item_id"),
                    },
                }
                for edge in self.session["edges"]
                if edge["decision"] == "accepted"
                and edge["from_node_id"] in accepted_ids
                and edge["to_node_id"] in accepted_ids
            ]
            pair_to_edge = {
                frozenset((edge["from_node_id"], edge["to_node_id"])): edge["edge_id"]
                for edge in edges
            }
            accepted_by_id = {node["node_id"]: node for node in accepted_source_nodes}
            index_to_node = {
                (node["grid_row"], node["grid_col"]): node["node_id"]
                for node in accepted_source_nodes
            }
            cells = []
            for row, col in sorted(index_to_node):
                top_left = index_to_node.get((row, col))
                bottom_left = index_to_node.get((row + 1, col))
                bottom_right = index_to_node.get((row + 1, col + 1))
                top_right = index_to_node.get((row, col + 1))
                if not all((top_left, bottom_left, bottom_right, top_right)):
                    continue
                boundary_pairs = [
                    frozenset((top_left, bottom_left)),
                    frozenset((bottom_left, bottom_right)),
                    frozenset((bottom_right, top_right)),
                    frozenset((top_right, top_left)),
                ]
                if not all(pair in pair_to_edge for pair in boundary_pairs):
                    continue
                node_ids_ccw = [top_left, bottom_left, bottom_right, top_right]
                signed_area = self._physical_signed_area_px2(
                    node_ids_ccw,
                    accepted_by_id,
                )
                side_lengths = []
                for index, node_id in enumerate(node_ids_ccw):
                    first = accepted_by_id[node_id]
                    second = accepted_by_id[node_ids_ccw[(index + 1) % 4]]
                    side_lengths.append(math.hypot(first["x"] - second["x"], first["y"] - second["y"]))
                cells.append(
                    {
                        "cell_id": f"cell-r{row:03d}-c{col:03d}",
                        "grid_index": {"row": row, "col": col},
                        "node_ids_ccw": node_ids_ccw,
                        "boundary_edge_ids_ccw": [pair_to_edge[pair] for pair in boundary_pairs],
                        "quality": {
                            "status": "orientation_ok" if signed_area > 0 else "inverted_or_degenerate",
                            "physical_signed_area_px2": signed_area,
                            "minimum_side_length_px": min(side_lengths),
                            "maximum_side_length_px": max(side_lengths),
                            "side_length_ratio": min(side_lengths) / max(side_lengths),
                            "scientific_quality_evaluated": False,
                        },
                    }
                )

            exported_at = utc_now()
            mesh = {
                "schema": REVIEWED_MESH_SCHEMA,
                "project_id": self.manifest["project_id"],
                "exported_at": exported_at,
                "coordinate_frames": {
                    "normalized_full_image_px": self.manifest["coordinate_frame"],
                    "roi_local_px": {
                        "origin_in_normalized_full_image_px": [roi_x0, roi_y0],
                        "x_axis": "right",
                        "y_axis": "down",
                        "units": "pixel",
                    },
                    "nominal_initial_mm": {
                        "origin": "reviewed visible grid index row=0, col=0",
                        "x_axis": "increasing reviewed column",
                        "y_axis": "increasing reviewed row, image-down",
                        "grid_spacing_mm": spacing,
                    },
                },
                "image_provenance": deepcopy(self.manifest["image"]),
                "roi": deepcopy(self.manifest["roi"]),
                "specimen": deepcopy(self.manifest["specimen"]),
                "topology_semantics": {
                    "grid_index": "human-reviewed visible topology indices",
                    "cell_node_order": "counterclockwise in x-right/y-up physical axes: top-left, bottom-left, bottom-right, top-right",
                    "automatic_observed_is_ground_truth": False,
                },
                "nodes": nodes,
                "edges": edges,
                "cells": cells,
                "review_audit": {
                    "final_review": deepcopy(self.session["final_review"]),
                    "reviewed_region_ids": [
                        region["review_region_id"] for region in self.session["review_regions"]
                    ],
                    "applied_operation_count": self.session["history_cursor"],
                    "operations": deepcopy(self.session["history"][: self.session["history_cursor"]]),
                    "excluded_node_count": sum(
                        node["decision"] == "rejected" for node in self.session["nodes"]
                    ),
                    "excluded_edge_count": sum(
                        edge["decision"] == "rejected" for edge in self.session["edges"]
                    ),
                },
            }
            target = self.project_dir / "exports" / "reviewed_mesh.json"
            write_json_atomic(target, mesh)
            return deepcopy(mesh), target

    def exported_mesh(self) -> dict[str, Any]:
        target = self.project_dir / "exports" / "reviewed_mesh.json"
        if not target.is_file():
            raise ContractError("尚未生成 ReviewedMesh/v1")
        mesh = read_json(target)
        if mesh.get("schema") != REVIEWED_MESH_SCHEMA:
            raise ContractError("导出文件 schema 不是 ReviewedMesh/v1")
        return mesh
