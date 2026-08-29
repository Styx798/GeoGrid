# GeoGrid

GeoGrid 是一个面向 GDS 真三轴试验照片的本地网格拓扑校核工具。它读取试样照片和自动识别生成的候选节点、边与复核区域，在浏览器界面中完成逐区检查、人工修正和结构化导出。

当前版本完成了单张照片的人工校核闭环；自动网格提取尚未接入正式命令行入口，因此初始化项目时需要提供已有的候选拓扑和复核区域 JSON。

## 主要功能

- 按 EXIF Orientation 统一照片方向，所有坐标使用同一归一化像素坐标系；
- 原始照片只读，初始化时复制到独立项目目录并校验 SHA-256；
- 按复核区域查看原图、当前拓扑和冲突建议，避免在整张密集网格上同时处理全部候选；
- 移动或新增节点、修改网格行列、接受/待定/排除节点与边、连接或断开边；
- 每次编辑自动保存，支持撤销、重做和修订号冲突检查；
- 完成结构检查后导出版本化的 `ReviewedMesh/v1` 网格数据。

## 实现方式

```text
照片 + 候选拓扑 JSON + 复核区域 JSON + 可选 ROI
                         │
                         ▼
              init-review 初始化项目
                         │
          EXIF 归一化、输入校验、建立审核状态
                         │
                         ▼
       FastAPI 本地服务 ←→ Konva 浏览器交互界面
                         │
          原子保存、撤销/重做、结构完整性检查
                         │
                         ▼
                 ReviewedMesh/v1
```

后端使用 FastAPI 和 Uvicorn 提供本地 API，Pillow 负责图像方向归一化，前端使用原生 HTML/CSS/JavaScript 与 Konva 绘制和编辑网格。项目状态保存为 JSON，不依赖数据库；服务默认只监听 `127.0.0.1`。

## 环境要求

- Windows 11
- CPython `3.12`
- Node.js `24`
- npm `11`

Python 依赖定义在 `pyproject.toml`，实际锁定版本记录在 `requirements-lock.txt`；前端依赖锁定在 `frontend/package-lock.json`。

## 安装

```powershell
git clone https://github.com/Styx798/GeoGrid.git
Set-Location '.\GeoGrid'

py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -e .

Push-Location '.\frontend'
npm ci
npm run build
Pop-Location
```

`npm run build` 会把锁定版本的 Konva 浏览器文件及其许可证复制到本地静态资源目录。

## 输入文件

初始化一个校核项目需要：

- 一张源照片；
- `GeoGridTopologyCandidate/v1` 候选拓扑 JSON；
- `GeoGridReviewRegions/v1` 复核区域 JSON；
- 可选的 ROI JSON，其中包含 `roi_polygon_normalized_full_image_px` 多边形。

项目数据目录必须不存在或为空，建议放在源码仓库之外。

## 初始化校核项目

```powershell
& '.\.venv\Scripts\python.exe' -m geogrid init-review `
  --project-dir 'D:\GeoGridProjects\case-001' `
  --image 'D:\GeoGridInput\photo.jpg' `
  --topology 'D:\GeoGridInput\topology_candidate.json' `
  --regions 'D:\GeoGridInput\review_regions.json' `
  --roi 'D:\GeoGridInput\roi.json'
```

初始化后，项目目录包含：

- `project.json`：项目清单、坐标系、图像哈希和输入来源；
- `source/`：源照片及候选 JSON 的工作副本；
- `assets/normalized.png`：按 EXIF 方向归一化后的照片；
- `state/review_session.json`：当前审核状态与撤销/重做历史；
- `exports/`：最终导出目录。

## 启动界面

```powershell
& '.\.venv\Scripts\python.exe' -m geogrid serve `
  --project-dir 'D:\GeoGridProjects\case-001' `
  --open-browser
```

默认地址为 `http://127.0.0.1:8765/`。后端以前台进程运行，按 `Ctrl+C` 即可停止。

## 校核与导出

1. 从左侧选择复核区域，对照原图、当前拓扑和冲突建议；
2. 移动节点、修改行列索引，或使用“新增节点”“连接 / 断开边”修正结构；
3. 对节点和边选择接受、待定或排除，也可以整区接受当前建议；
4. 所有区域处理完成后执行整图确认；
5. 点击“导出 ReviewedMesh”生成 `exports/reviewed_mesh.json`。

导出前会检查待定对象、重复行列索引、边的端点与四邻接关系，以及四边形单元是否倒置或退化。任何继续编辑都会使之前的整图确认失效，需要重新确认后才能导出。

## 源码结构

```text
src/geogrid/
├── cli.py          命令行入口
├── domain.py       输入契约、图像归一化和项目初始化
├── store.py        审核命令、历史记录、结构检查与导出
├── api.py          FastAPI 本地接口
└── web/            浏览器界面

frontend/
└── build.mjs       前端依赖复制脚本
```

## License

GeoGrid 采用 [MIT License](LICENSE)。
