# pioneers

一个本地运行的多模型 Agent 调度项目，用来实践产品 Lead、评审和开发 Agent 之间的任务交接。

工作流 JSON 定义任务的角色、执行方式、依赖和交付要求。调度器检查依赖，按顺序执行任务，把文本产物保存到 `.pioneers/runs/`，再将前置任务的产物交给下游。

| 路由 | 执行方式 | 示例 |
| --- | --- | --- |
| `fast` | DeepSeek API | 起草 API 契约 |
| `deep` | OpenAI API | 评审契约并写出验收条件 |
| `code` | Codex CLI | 在指定工作目录里实现代码和运行测试 |

角色和路由独立配置。后端开发可以用 `deep` 做设计评审，也可以用 `code` 落地实现。Codex 负责自己的开发循环，pioneers 负责任务顺序和交接。

## 本地跑一遍

需要 Python 3.9 或更高版本。默认离线运行，不需要安装 Python 依赖。

```bash
python3 pioneers.py examples/todo.json
```

示例按「起草接口 → 评审接口 → 实现接口」顺序生成三个 Markdown 文件。离线模式使用模拟输出，用于验证调度和交接。

## 接入真实执行

在本机环境中设置 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`OPENAI_API_KEY` 和 `OPENAI_MODEL`。模型 ID 以各自账号可用的模型为准。使用 `code` 路由时，需提前安装并登录 Codex CLI，并指定一个你愿意让 Codex 修改的 Git 仓库或 worktree。

```bash
python3 pioneers.py examples/todo.json --live --workspace /path/to/practice-repo
```

真实模式会调用对应服务，传入任务说明及相关前置产物。开发步骤可能写入指定工作目录。任务失败时停止当前运行，已保存的产物可用于排查。

当前版本串行执行任务。Codex 返回的测试结果作为文本产物保存，独立验收、多 Agent 并行、媒体生成和失败续跑留待后续接入。
