# Recursive Unzip Tool

一个面向桌面的递归解压工具。选择目标目录后，程序会递归查找该目录下的压缩包，并把内容解压到每个压缩包所在的目录。

## 功能

- 现代化 PySide6 图形界面
- 递归扫描目标目录和所有子目录
- 支持 `.zip`、`.7z`、`.tar`、`.tar.gz`、`.tgz`、`.tar.bz2`、`.tbz2`、`.tar.xz`、`.txz`
- 可选“解压成功后删除源文件”，默认关闭
- 后台线程执行，解压时 GUI 不会卡死
- 实时进度、文件状态、错误日志和结果汇总
- 遇到单个压缩包失败时继续处理后续文件

## 安装

推荐使用 `uv`：

```powershell
uv sync
```

或使用 `pip`：

```powershell
python -m pip install -e .
```

## 运行

```powershell
python main.py
```

安装为可编辑包后，也可以运行：

```powershell
recursive-unzip-tool
```

## 使用

1. 点击“选择”并选中目标目录。
2. 勾选需要处理的压缩格式。
3. 按需勾选“解压成功后删除源文件”。
4. 点击“开始解压”。
5. 在表格和日志中查看每个压缩包的处理状态。

## 安全说明

- 默认不会删除源压缩包。
- 只有压缩包解压成功，并且手动勾选删除选项时，才会删除对应源文件。
- `.zip` 和 `.tar*` 解压前会检查不安全路径，避免压缩包把文件写到目标目录之外。
- 当前版本不支持 `.rar`，因为 RAR 通常需要额外外部工具，跨平台兼容性更复杂。

## 开发与测试

```powershell
uv sync --group dev
uv run pytest
```

如果不使用 `uv`：

```powershell
python -m pip install -e . pytest
python -m pytest
```
