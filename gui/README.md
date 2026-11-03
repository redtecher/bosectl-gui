# gui/ — 托盘应用源码

功能介绍、运行/打包步骤见[仓库根 README](../README.md)。

## 架构

```
gui/
  app.py           # TrayController(QObject): 托盘 + 主窗口 + service 接线
  service.py       # DeviceService: worker QThread 封装 pybmap（所有蓝牙 IO）
  main_window.py   # 主窗口 UI（状态/CNC/模式/EQ）
  resources.py     # 程序化生成图标（QPainter 绘制，无版权素材）
  smoke_test.py    # 真机冒烟测试（--smoke / python -m gui.smoke_test）
  screenshot.py    # 截图工具（python -m gui.screenshot → docs/）
  run.py           # PyInstaller 打包入口（顶层脚本，相对导入在打包后不解析）
  __main__.py      # python -m gui 入口
  bosectl-gui.spec # PyInstaller spec
```

线程模型：UI 线程通过 queued signal/slot 调用 `DeviceService` 的槽
（该对象 `moveToThread` 到独立 QThread），蓝牙阻塞操作永不触碰 UI 线程。
注意：跨线程信号的接收方必须是 QObject（如 `TrayController`），否则
PySide 直连到 worker 线程执行，UI native 调用会死锁。
