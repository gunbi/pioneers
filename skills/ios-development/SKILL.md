---
name: ios-development
description: 在 iOS 功能实现、UI 审查和设备验收时使用。沿用项目技术栈，核对状态、构建和模拟器证据。
---

# iOS 开发

[English edition](SKILL.en.md)

1. 先检查项目说明、workspace 或 project、scheme、deployment target、已有 SwiftUI 或 UIKit 结构和测试方式。使用当前目标系统支持的 API，沿用现有依赖和界面约定。
2. 明确状态归属、导航、副作用和异步任务的取消时机。按改动覆盖加载、空数据、错误恢复和可访问性。QA 按相同用户路径核查实现和证据。
3. 使用项目现有构建与测试命令。有 Xcode 和模拟器时，确认 scheme 与设备，构建并启动应用，再执行关键路径。可用宿主提供的 XcodeBuildMCP 查界面、日志和截图。
4. 交付命令结果、scheme、系统版本、设备或模拟器型号、实际行为与证据。区分编译、单测与设备验证。缺少 macOS、Xcode 或模拟器时，明确哪些行为仍待验证。

## 参考来源

- SwiftUI 状态、视图与平台 API。 [SwiftUI Expert](https://github.com/AvdLee/SwiftUI-Agent-Skill/blob/main/skills/swiftui-expert-skill/SKILL.md)
- 构建、启动和模拟器调试。 [OpenAI iOS Debugger Agent](https://github.com/openai/plugins/blob/main/plugins/build-ios-apps/skills/ios-debugger-agent/SKILL.md)
