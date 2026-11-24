---
name: change-integration
description: 在并行改动汇合或跨模块联调时确认目标与集成顺序，并验证集成后的版本。
---

# 变更集成

[English edition](SKILL.en.md)

1. 确认集成负责人、目标项目工作区、目标分支或基线，以及每项待接入改动的提交或文件范围。记录现场未提交改动和已有测试失败，保留各执行端的原始产物。
2. 按依赖合入已就绪的变更，先对齐公共契约和配置，再接入实现。使用工作流指定的集成目录或分支。按任务权限执行 Git 操作，审批和远端操作沿用 Pioneers 的授权流程。
3. 处理冲突时核对双方意图、接口和测试。遇到双方对行为契约理解不一致时，记录分歧，交回相应负责人确认。
4. 在合并后的具体版本上运行受影响模块的测试、构建和关键路径冒烟验证。跨端改动优先验证真实接口以及配置、错误处理和兼容性。集成失败时记录失败版本、复现命令和阻塞来源。
5. 向下游交接集成分支或提交、纳入的变更、冲突处理、验证命令和结果。基线或候选版本变化后，对受影响的范围复验。

## 参考来源

- 确认基线并对合并结果重新验证。 [Superpowers · Finishing a Development Branch](https://github.com/obra/superpowers/blob/main/skills/finishing-a-development-branch/SKILL.md)（[MIT](https://github.com/obra/superpowers/blob/main/LICENSE)）
