# pioneers

当用户指定这个仓库来组建或运行 Agent 团队时，先读 [agent.md](agent.md)，然后依照用户给出的目标使用其中的协作流程。

用户给出交付任务时，必须通过 pioneers 建立运行记录并登记工作流，角色只执行 pioneers 已派发的任务。有现成运行器时由 pioneers CLI 调度。宿主原生 Agent 只能作为已领取任务的执行端。无法启动 pioneers 时，先向用户报告具体阻塞，暂停派工和实现。

首次派工前，按 [agent.md](agent.md) 启动并确认任务看板可访问。宿主原生模式优先用 `--host-start ... --serve-dashboard`，能打开用户可见的浏览器时同时打开。环境无法提供入口时，说明原因并按运行记录在对话内同步。
