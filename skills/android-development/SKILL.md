---
name: android-development
description: 在 Android 功能实现、UI 审查和设备验收时使用。沿用项目技术栈，核对窗口适配、Gradle 构建和设备行为。
---

# Android 开发

1. 先检查模块、Gradle Wrapper、构建变体、项目的 SDK 版本和 Compose 或 Views 约定。按项目已有的 ViewModel、流和生命周期管理方式交接状态。
2. 根据用户路径核对加载、空数据、失败与恢复。涉及界面时，按本次要求检查窗口尺寸、旋转与状态恢复、系统栏内边距和可访问性。复杂改动区分纯逻辑、界面和设备验证。
3. 优先执行仓库现有的 Gradle 构建和测试任务。宿主已有 Android CLI 或设备工具时，用现有模拟器或已授权设备走主路径。根据改动选择 UI 或截图测试。
4. 交付任务名、模块及变体、运行结果、设备/API 级别和可访问的日志或截图。只完成 JVM 测试时，单列仍需设备验证的行为。

## 参考来源

- 窗口大小和自适应布局。 [Android Skills · Adaptive](https://github.com/android/skills/blob/main/jetpack-compose/adaptive/SKILL.md)
- 单元、UI 和截图测试策略。 [Android Skills · Testing Setup](https://github.com/android/skills/blob/main/testing/testing-setup/SKILL.md)
- 模拟器和设备操作。 [Android Skills · Android CLI](https://github.com/android/skills/blob/main/devtools/android-cli/SKILL.md)
