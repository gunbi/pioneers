---
name: acceptance-testing
description: 依据交接的验收条件独立设计风险覆盖、复现行为和提交逐项证据。
---

# 验收与回归测试

[English edition](SKILL.en.md)

1. 读验收条件、当前轮次、实现交付和相关变更。先验证用户主路径及最可能受影响的回归点。为每项记录前置数据、操作、预期与实际结果。
2. 按条件选择项目测试、模拟交互或真实设备验证。注明验证环境、命令和可复查证据。证据仅覆盖静态行为时，真实交互仍待验证。
3. 在结构化报告的 `checks` 逐项引用验收条件原文，标注 `pass`、`fail` 或 `unverified`。全部通过才返回 `accepted`。实现缺陷返回 `changes_requested` 并指明修复任务。需求歧义用 `needs_clarification`，缺少必要证据用 `blocked`。复验先跑上一轮失败的步骤，再核对受影响的其他条件。

## 参考来源

- 按风险设计覆盖并追溯到证据。 [BMad · Test Engineering Architect](https://github.com/bmad-code-org/bmad-method-test-architecture-enterprise)
- 执行场景并用结果验证测试。 [GitHub Awesome Copilot · Playwright Generate Test](https://github.com/github/awesome-copilot/blob/main/skills/playwright-generate-test/SKILL.md)
