---
name: aliyun-ecs-container-deployment
description: 在向阿里云 ECS 容器发布应用、准备部署方案或验收部署结果时使用，覆盖 Docker Compose、ACR 镜像、健康检查和回滚。
---

# 阿里云 ECS 容器部署

1. 从部署说明、Dockerfile 和 Compose 配置确认构建方式、服务依赖、端口、健康检查和数据目录。核对目标 ECS 的地域、实例、环境和 CPU 架构。单机多服务沿用 Docker Compose。使用现有 ACR 或 ECS 应用管理时，沿用已配置的镜像仓库及部署方式。
2. 在本地或测试环境验证镜像启动和关键路径。为镜像记录不可变版本或摘要，确认 ECS 能拉取对应架构的镜像。列出配置变量名、持久化卷、现行版本、回滚目标和所需数据备份。凭证由宿主通过授权配置注入，证据使用脱敏信息。
3. 把远端发布登记为 `effect: publish` 的独立任务。审批内容包括目标环境、实例或应用分组、镜像版本、对外入口、数据变更和回滚范围。创建或扩容 ECS、调整网络或权限等变更按工作流单独授权。LD 和 QA 可准备方案或核验证据，获派且得到授权的执行端负责部署。
4. 按获批范围部署到目标 ECS。保留持久化卷，核对实际运行的镜像版本和服务状态，从目标访问入口检查健康端点和关键业务路径。更新失败时保留脱敏日志，记录影响和恢复状态。按已授权的回滚范围处理，重新部署走相应审批。
5. 交付目标环境、镜像标识、访问地址、健康检查和冒烟结果、回滚方式及可复查的脱敏日志。独立 QA 复验已部署版本。

## 参考来源

- ECS 部署前检查、日志和结果验证。 [阿里云 · ECS Code Deploy](https://github.com/aliyun/alibabacloud-aiops-skills/blob/master/skills/computing/computenest/alibabacloud-ecs-code-deploy/SKILL.md)
- 单机多服务。 [阿里云 ECS · Docker 与 Docker Compose](https://www.alibabacloud.com/help/zh/ecs/user-guide/install-and-use-docker)
- 通过镜像仓库发布。 [阿里云 ECS · 使用 Docker 镜像部署应用](https://www.alibabacloud.com/help/zh/ecs/user-guide/docker-image-object)
