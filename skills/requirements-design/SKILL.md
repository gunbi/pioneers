---
name: requirements-design
description: 将功能请求转成供实现与 QA 共用的范围、用户场景和可复现的验收条件。
---

# 需求与验收设计

[English edition](SKILL.en.md)

1. 阅读用户目标和相关交接，写清适用用户、触发场景与要改变的结果。区分现有行为、已确认要求和待核实的假设。
2. 定义本轮范围及延期事项。复杂任务补充影响的路径、依赖和待决定的约束。遇到会改变范围或验收结论的歧义时，说明受影响的条目并提出具体问题。
3. 按主路径、关键失败场景和恢复行为写验收条件。每项包含前置状态、动作和可观察结果。提供给工程师和 QA 同一版本的条件原文。

例如，列表有三条未完成事项，选中两条后批量完成，选中的两条变为已完成，第三条保持原状态。撤销和刷新恢复需要验证时另列条件。

## 参考来源

- 范围和可测试需求。 [Product on Purpose · Deliver PRD](https://github.com/product-on-purpose/pm-skills/blob/main/skills/deliver-prd/SKILL.md)
- 主路径与可观察验收场景。 [Agent Skills · Deliver Acceptance Criteria](https://github.com/yuusakuri/agent-skills/blob/main/skills/deliver-acceptance-criteria/SKILL.md)
