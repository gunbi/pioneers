---
name: architecture-design
description: 在跨模块开发或准备冻结接口契约时使用，定义可由客户端和服务端共同验收的边界。
---

# 架构设计

1. 先明确核心用户路径，确认哪些模块承担状态、校验和持久化。
2. 定义模块职责与依赖方向。涉及前后端协作时，约定请求字段、成功响应和错误行为，能用相同契约编写 Mock 与测试。
3. 如果任务要求结构化交付，输出 `architecture.json`。至少包含 `summary`、`modules`（每项有 `name` 和 `responsibility`）及 `interfaces`（每项有 `name` 和 `contract`，无跨模块接口时可为空数组）。
4. 使用角色可用的 `architecture_check` 检查结构。验收接口行为时，再运行对应项目的测试。
