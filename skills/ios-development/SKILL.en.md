---
name: ios-development
description: Use when implementing iOS features, reviewing the UI or accepting a device build. Follow the existing stack and verify state, builds and simulator evidence.
---

# iOS development

[Chinese edition](SKILL.md)

1. Read the project instructions and inspect the workspace or project, scheme, deployment target, existing SwiftUI or UIKit structure and testing approach. Use APIs supported by the target OS and follow existing dependency and UI conventions.
2. Identify state ownership, navigation, side effects and when asynchronous tasks should be cancelled. Cover loading, empty data, error recovery and accessibility as relevant to the change. QA checks the implementation and evidence along the same user journey.
3. Use the project's existing build and test commands. When Xcode and a simulator are available, confirm the scheme and device, build and launch the app, then exercise critical paths. If the host provides XcodeBuildMCP, use it to inspect the interface, logs and screenshots.
4. Deliver command results, scheme, OS version, device or simulator model, observed behavior and evidence. Distinguish compilation, unit tests and device verification. When macOS, Xcode or a simulator is unavailable, list the behavior still awaiting verification.

## Sources

- SwiftUI state, views and platform APIs. [SwiftUI Expert](https://github.com/AvdLee/SwiftUI-Agent-Skill/blob/main/skills/swiftui-expert-skill/SKILL.md)
- Building, launching and simulator debugging. [OpenAI iOS Debugger Agent](https://github.com/openai/plugins/blob/main/plugins/build-ios-apps/skills/ios-debugger-agent/SKILL.md)
