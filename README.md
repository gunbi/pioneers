# pioneers

一个可在 AI 平台或本地命令行使用的多 Agent 协作框架。角色、任务依赖和验收规则写在仓库里。

## 在 Manus 中使用

将这个仓库的 Git 地址交给 Manus，并告诉它：

> 读取仓库根目录的 agent.md，用你的原生能力组建 pioneers 团队。按任务依赖和验收要求推进，根据每项任务标注的复杂度选择执行方式。

[agent.md](agent.md) 定义了团队名单和协作流程，[examples/todo.json](examples/todo.json) 展示了 PM、后端和测试如何交接。Manus 路径直接使用当前平台的能力，无需 API Key 或本地 Codex。

## 本地 CLI 快速开始

需要 Python 3.9 或更高版本，无需安装 Python 依赖。

```bash
python3 pioneers.py --list-roles
python3 pioneers.py examples/todo.json
```

示例在离线模式下演练「PM 定义需求 → 后端实现 → 测试验收」。运行时会打印产物目录，默认位于 `.pioneers/runs/`。每个任务生成一个 Markdown 文件，标明离线演示并展示它会收到的任务和前置产物。

## 角色与工作流

| 角色 ID | 职责 | 工作区权限 |
| --- | --- | --- |
| `ld` | 团队负责人，明确目标、分工和依赖 | 只读 |
| `pm` | 产品经理，定义需求和验收条件 | 只读 |
| `ui` | UI 设计师，交付布局和交互说明 | 只读 |
| `backend` | 后端开发，实现 API 和业务逻辑 | 可写 |
| `frontend` | 前端开发，实现 Web 界面 | 可写 |
| `ios` | iOS 开发，实现和验证 iOS 功能 | 可写 |
| `android` | 安卓开发，实现和验证 Android 功能 | 可写 |
| `qa` | 测试，对照验收条件核对实现和证据 | 只读 |

从 [examples/todo.json](examples/todo.json) 开始。为每项任务指定 `id`、`role`、`prompt`，也可提供 `depends_on`、`complexity` 和 `acceptance`。本地 CLI 在一个指定工作区内串行执行，每项任务启动独立的 Codex 会话。QA 的文本结论记录在产物中，供人工验收。

如需留下可重复使用的团队配置，可以调用初始化工具：

```bash
python3 pioneers.py --create-team --workspace /path/to/practice-repo
```

它会在目标 Git 项目写入 `.pioneers/team.json`，输出 JSON。重复调用会返回已有的团队。

## 运行真实的 Codex 会话

安装并登录 Codex CLI，在终端把 `MODEL_ID` 设为当前账号可用的模型 ID，然后指定一个可供修改的 Git 仓库或 worktree。

```bash
python3 pioneers.py examples/todo.json --live --model "$MODEL_ID" --workspace /path/to/practice-repo
```

如果初始化时用 `--model` 配置了默认模型，运行时可以省略这个参数。每个任务调用一次 `codex exec`，传入同一个模型，并根据角色选择工作区权限。任务失败时，后续任务停止，之前的 Markdown 产物可继续查阅。
