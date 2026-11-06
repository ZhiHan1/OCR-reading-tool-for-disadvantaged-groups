# 更新日志

本项目从 2026-11 起使用本文件记录版本变更，格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- **麦克风输入源选择**：语音输入页可在启动时枚举出的所有输入设备中选择，
  支持手动刷新，按设备名持久化到 `asr.device_name`。
  同一支麦克风在 MME / DirectSound / WASAPI / WDM-KS 下的重复端口自动归并
  （本机实测 55 个端口 → 15 个设备）。
- `tools/download_asr_models.py`：一键下载语音识别、VAD、标点模型。
- 发布版自检新增 `microphone_listing` 项（25 项检查）。

### 修复

- **设置页页签向左切换时「闪一下旧页面」**：轨道 pane 改为按阅读顺序排列，
  视口位置只由 `translateX` 控制；`onfinish` 中先提交 DOM 再取消动画。
- 发布自检中 `radios` 页的假阴性（`{animate:false}` + `router.refresh()`）。
- 打包脚本对 SenseVoice 模型的硬编码，改为自适应（至少 1 个识别模型 + vad/punc）。

### 变更

- 闲置退出改为固定档位下拉：1 / 2 / 5 / 10 / 30 分钟 + 常驻，默认 5 分钟。
- 语音页「识别设置」面板默认展开。
- 界面文字不可选中，仅 `input` / `textarea` 内容可复制。
- 前端资源版本号统一为 `?v=20261105c`（24 处）。

### 文档

- 新增 `README.md`、`LICENSE`、`CHANGELOG.md`、`.gitattributes`。
- 知识库新增：`KB-WEB-009`（设置页切换闪旧页）、`KB-ASR-011`（麦克风选择）、
  `KB-GOT-009`（动画 bug 三帧采样法）。

### 构建

- 打包入口改为 `build_web.py`（6 步流程），`build_web.ps1` 降为薄封装。
  支持 `--skip-tests` 与 `--tag`。

---

## 说明

- 2026-11-05 之前的开发历史没有逐版本记录，完整细节见 `KNOWLEDGE/`。
