---
name: systematic-debugging
description: 在构建失败、行为异常或 QA 退回时复现问题，验证根因，并证明修复有效。
---

# 系统化调试

1. 记录观察到的行为、预期行为、复现命令或操作、环境和相关版本。先用最小输入复现，偶发问题则记录触发条件和观测次数。
2. 对比通过与失败的路径，找到第一个结果偏离预期的边界。检查相关 diff、配置和依赖，必要时用日志或断言核对边界两端的输入、输出和状态。
3. 一次验证一个有可观察结果的假设。实验只改变与该假设相关的条件。证据仍不足时，交接已排除项和下一个最有效的验证步骤。
4. 有写入权限的执行端做最小范围修复。用原复现步骤确认修复前后差异，补跑受影响路径。能稳定复现的行为缺陷，按测试工程的做法补充或更新回归测试。
5. 交接复现步骤、根因证据、变更位置、复验结果和仍未验证的条件。只读执行端交接证据及修复目标，由获派的工程师修改。

## 参考来源

- 从复现、边界证据到单个假设验证。 [Superpowers · Systematic Debugging](https://github.com/obra/superpowers/blob/main/skills/systematic-debugging/SKILL.md)（[MIT](https://github.com/obra/superpowers/blob/main/LICENSE)）
