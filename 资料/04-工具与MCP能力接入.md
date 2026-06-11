# 04 工具与 MCP 能力接入

## 目标

当前 Agent 只能聊天，不能主动执行任务。本阶段先加入本地项目相关的基础工具能力，并为 MCP 接入预留结构。

## 设计目的

- 让 Agent 能读取项目上下文，而不是只依赖用户粘贴。
- 先提供安全、只读、低风险的工具。
- 为后续 MCP server 接入建立工具注册和调用边界。

## 计划代码

新增 `human_chat/tools.py`：

```python
def list_project_files(root, limit=100):
    ...

def read_project_file(root, path):
    ...

def search_project_text(root, query):
    ...
```

CLI 先提供显式命令：

```text
/tools
/files
/read human_chat/graph.py
/search memory
```

后续再把工具调用接入 LangGraph，让模型自动决定是否使用工具。

## 完成标准

- 用户可以通过 CLI 命令列出项目文件。
- 用户可以读取项目内的文本文件。
- 用户可以搜索项目文本。
- 工具访问范围限制在项目根目录内。

