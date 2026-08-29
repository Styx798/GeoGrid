from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from geogrid.api import create_app
from geogrid.domain import ContractError, initialize_review_project


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geogrid",
        description="GeoGrid 单图、区域优先人工网格校核器",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser(
        "init-review", help="从一张照片和候选 JSON 建立隔离校核项目"
    )
    initialize.add_argument("--project-dir", required=True, type=_path, help="新建的项目数据目录")
    initialize.add_argument("--image", required=True, type=_path, help="只读源照片")
    initialize.add_argument("--topology", required=True, type=_path, help="GeoGridTopologyCandidate/v1")
    initialize.add_argument("--regions", required=True, type=_path, help="GeoGridReviewRegions/v1")
    initialize.add_argument("--roi", type=_path, help="包含 roi_polygon_normalized_full_image_px 的 JSON")
    initialize.add_argument(
        "--compression-percent", type=float, default=15.0, help="轴向压缩量百分数，默认 15"
    )

    serve = subparsers.add_parser("serve", help="以前台进程启动本地校核界面")
    serve.add_argument("--project-dir", required=True, type=_path, help="已有项目数据目录")
    serve.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open-browser", action="store_true", help="启动后打开默认浏览器")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init-review":
            manifest = initialize_review_project(
                project_dir=args.project_dir,
                image_path=args.image,
                topology_path=args.topology,
                regions_path=args.regions,
                roi_path=args.roi,
                compression_percent=args.compression_percent,
            )
            print(f"已建立校核项目：{args.project_dir.resolve()}")
            print(f"项目 ID：{manifest['project_id']}")
            print(
                "归一化尺寸："
                f"{manifest['image']['normalized_size_width_height'][0]} × "
                f"{manifest['image']['normalized_size_width_height'][1]}"
            )
            return 0

        app = create_app(args.project_dir)
        url = f"http://{args.host}:{args.port}/"
        print(f"GeoGrid 正在前台运行：{url}")
        print("关闭此命令行窗口或按 Ctrl+C 即停止服务。")
        if args.open_browser:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    except (ContractError, RuntimeError, OSError) as exc:
        print(f"GeoGrid：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
