# GeoGrid

GeoGrid 的公开源码仓库。

本仓库只包含 GeoGrid 自有实现，以及运行和分发源码所必需的依赖清单、许可证与最小说明。测试代码、测试或试验照片、样例数据、识别输出、研究记录、论文材料、隔离 POC、AI 协作文件和本地克隆的第三方源码均不属于公开仓库内容。

## 开发环境

- Windows 11
- CPython 3.12
- Node.js 24

Python 依赖锁定在 `requirements-lock.txt`，浏览器依赖锁定在 `frontend/package-lock.json`。

```powershell
& 'C:\path\to\python3.12.exe' -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --no-build-isolation -e .

Push-Location '.\frontend'
npm install
npm run build
Pop-Location

& '.\.venv\Scripts\python.exe' -m geogrid --help
```

程序数据应放在代码仓库之外；本仓库不提供研究数据或演示数据。

## License

GeoGrid 自有源码以 [MIT License](LICENSE) 发布。依赖项目仍分别适用其各自许可证。
