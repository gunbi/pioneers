---
name: aliyun-ecs-container-deployment
description: Use when planning, deploying or verifying a containerized application on Alibaba Cloud ECS. Covers Docker Compose, ACR images, health checks and rollback.
---

# Deploy containers on Alibaba Cloud ECS

[Chinese edition](SKILL.md)

1. Read the deployment instructions, Dockerfile and Compose configuration to identify the build procedure, service dependencies, ports, health checks and data directories. Check the region, instance, environment and CPU architecture of the target ECS host. Use Docker Compose for multiple services on a single host. If the project already uses ACR or ECS application management, follow its configured image registry and deployment method.
2. Verify image startup and the critical paths locally or in a test environment. Record an immutable image version or digest and confirm that the ECS instance can pull the image for its architecture. List configuration variable names, persistent volumes, the current version, the rollback target and required data backups. The host injects credentials through its authorized configuration. Redact sensitive values from the evidence.
3. Register the remote deployment as a separate task with `effect: publish`. Approval covers the target environment, instance or application group, image version, external entry point, data changes and rollback scope. Obtain separate workflow authorization for creating or scaling ECS instances or changing network settings or permissions. The delivery lead and QA may prepare a plan or verify evidence. The assigned, authorized executor performs the deployment.
4. Deploy to the target ECS instance within the approved scope. Preserve persistent volumes and check the image version actually running and the service status. Test the health endpoint and critical business paths from the target entry point. If an update fails, retain redacted logs and record the impact and recovery status. Follow the authorized rollback scope and obtain the applicable approval for redeployment.
5. Hand off the target environment, image identifier, service URL, health and smoke-test results, rollback procedure and reviewable redacted logs. Independent QA retests the deployed version.

## Sources

- Pre-deployment checks, logs and verification on ECS. [Alibaba Cloud · ECS Code Deploy](https://github.com/aliyun/alibabacloud-aiops-skills/blob/master/skills/computing/computenest/alibabacloud-ecs-code-deploy/SKILL.md)
- Multiple services on one host. [Alibaba Cloud ECS · Docker and Docker Compose](https://www.alibabacloud.com/help/zh/ecs/user-guide/install-and-use-docker)
- Deploying through an image registry. [Alibaba Cloud ECS · Deploy an application with a Docker image](https://www.alibabacloud.com/help/zh/ecs/user-guide/docker-image-object)
