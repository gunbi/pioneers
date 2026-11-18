---
name: existing-ui-audit
description: 在已有 Web 界面改版前或新旧对照验收时，沿代表性用户路径检查信息层级、响应式、可访问性和界面反馈，并记录可复验的问题。
---

# 既有界面审计

1. 固定被审计的版本、入口、测试数据和视口。优先走改版涉及的关键用户任务，从实际进入点走到用户能确认完成的结果。
2. 在关键屏上检查用户是否能找到入口、理解当前状态和决定下一步。核对内容层级、标签、主要操作、返回和异常恢复。记录路径上的发现，分清功能失败、操作障碍和视觉建议。
3. 独立检查适用的技术条件，包括窄屏重排、缩放、键盘焦点、控件名称、可见反馈和可观察的加载问题。记录实际测过的范围。只有静态检查或环境受限时，标明待浏览器确认的项目。
4. 每个问题写清严重程度、页面和操作步骤、实际结果、受影响的任务以及截图或其他可复查证据。改版后用相同数据、视口和步骤复验与本次改动有关的问题及应保留的行为。

## 参考来源

- 任务、层级与交互评审。 [Impeccable · Critique，Apache 2.0](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/critique.md)
- 可访问性、响应式和技术检查。 [Impeccable · Audit，Apache 2.0](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/audit.md)
