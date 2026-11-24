---
name: test-engineering
description: 为新行为、缺陷或高风险变更选择测试层级，编写或评审可重复执行的回归测试。
---

# 测试工程

[English edition](SKILL.en.md)

1. 从验收条件和实际变更提取可断言的行为。按影响和发生概率排序，优先保护核心路径、边界、失败恢复和已出现的回归。
2. 利用项目已有测试框架，选择能可靠捕获风险的最小层级。用单元测试覆盖规则和边界，用契约或集成测试验证模块与外部依赖间的行为，用端到端测试覆盖少量关键用户路径。
3. 为可复现的缺陷编写能在修复前暴露问题的测试。核对失败原因与目标行为一致，再在修复后运行同一测试。使用可控的数据和时间条件，从公开行为断言结果。
4. 执行新增或受影响的测试，记录命令、预期、结果及未覆盖的风险。交付工程师将可维护的测试留在项目中。只读 QA 评审覆盖、复现可靠性和失败信号，并把缺失测试交回工程师。
5. 交付测试位置、对应行为或风险、复现命令和结果。独立验收仍按验收条件产出逐项证据。

## 参考来源

- 用修复前的失败验证回归断言。 [Superpowers · Test-Driven Development](https://github.com/obra/superpowers/blob/main/skills/test-driven-development/SKILL.md)（[MIT](https://github.com/obra/superpowers/blob/main/LICENSE)）
- 按风险和测试层级确定覆盖。 [BMad · Test Engineering Architect](https://github.com/bmad-code-org/bmad-method-test-architecture-enterprise/blob/main/README.md)（[MIT](https://github.com/bmad-code-org/bmad-method-test-architecture-enterprise/blob/main/LICENSE)）
