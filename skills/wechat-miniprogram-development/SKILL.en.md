---
name: wechat-miniprogram-development
description: Use when implementing, integrating or accepting a WeChat Mini Program. Covers page routing, API integration, previews in WeChat DevTools and verification on physical devices.
---

# WeChat Mini Program development

[Chinese edition](SKILL.md)

1. Identify whether the project uses the native Mini Program stack or a cross-platform framework. Confirm the project entry point, build output directory, `project.config.json`, page routes and target base library. Follow the existing structure and build commands and develop in an isolated target project workspace.
2. Integrate the backend using the shared contract across clients. The Mini Program reuses the business semantics of product, cart and order APIs. Encapsulate platform login, network requests and simulated payments in an adapter so tests can switch to mocks. For WeChat login, pass the credential returned by `wx.login` to the server for processing. Keep platform secrets on the server.
3. Verify loading, empty data, failure on a weak network, retry and state after navigating back along the main user journey. For e-commerce, cover product details through order placement, simulated payment results, repeated taps to submit and order recovery. Use test data for preview. Real payment, version upload and release require authorization for the task.
4. Run the project's build and tests, then compile and preview with authorized WeChat DevTools. Check routing, the console and API requests. For device-specific behavior, verify on available physical iOS and Android devices and record the device and base library version. If only a build or static checks are possible, deliver the verified scope and list what still needs real-device testing.
5. Deliver the build command, preview entry point or access steps, test data, results for each check, and screenshots or logs. Read-only QA retests independently and records environment limitations.

## Sources

- Project configuration, routing and WeChat DevTools previews. [TencentCloudBase · Miniprogram Development](https://github.com/TencentCloudBase/skills/blob/main/skills/miniprogram-development/SKILL.md) ([MIT](https://github.com/TencentCloudBase/skills/blob/main/LICENSE))
- Mini Program APIs and platform behavior. [WeChat official documentation](https://developers.weixin.qq.com/miniprogram/dev/framework/)
