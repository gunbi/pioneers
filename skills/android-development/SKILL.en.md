---
name: android-development
description: Use when implementing Android features, reviewing the UI or accepting a device build. Follow the existing stack and verify window adaptation, Gradle builds and device behavior.
---

# Android development

[Chinese edition](SKILL.md)

1. Inspect modules, the Gradle Wrapper, build variants, project SDK versions and Compose or Views conventions. Hand off state using the project's existing ViewModel, stream and lifecycle patterns.
2. Walk the user journey through loading, empty data, failure and recovery. For UI changes, check the window sizes, rotation and state restoration, system bar insets and accessibility relevant to the task. For complex changes, distinguish logic tests, UI checks and device verification.
3. Start with the repository's existing Gradle build and test tasks. If the host has Android CLI or device tools, walk the main path on an existing emulator or authorized device. Choose UI or screenshot tests according to the change.
4. Deliver task names, module and variant, results, device and API level, and accessible logs or screenshots. If only JVM tests ran, list behavior that still requires device verification.

## Sources

- Window size and adaptive layouts. [Android Skills · Adaptive](https://github.com/android/skills/blob/main/jetpack-compose/adaptive/SKILL.md)
- Unit, UI and screenshot test strategy. [Android Skills · Testing Setup](https://github.com/android/skills/blob/main/testing/testing-setup/SKILL.md)
- Emulator and device operations. [Android Skills · Android CLI](https://github.com/android/skills/blob/main/devtools/android-cli/SKILL.md)
