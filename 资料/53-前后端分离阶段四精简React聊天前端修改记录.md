# 前后端分离阶段四：精简 React 聊天前端修改记录

## 1. 本阶段目标

阶段三已经提供了会话、历史消息、SSE 对话、取消和审批 API。本阶段不再修改 Agent
业务逻辑，而是建立一个可以实际使用这些接口的浏览器界面。

根据项目当前阶段，前端采取“功能完整但界面克制”的原则，只实现聊天产品的基本闭环：

1. 查看和切换最近会话；
2. 创建新会话；
3. 读取历史用户消息和助手消息；
4. 发送问题并消费 SSE 事件；
5. 展示粗粒度进度和最终回答；
6. 停止正在执行的轮次；
7. 确认或拒绝高风险工具操作；
8. 选择是否保存长期记忆候选项；
9. 在刷新页面后恢复尚未处理的审批；
10. 支持桌面和移动端基本布局。

本阶段没有加入前端测试文件。验证使用 TypeScript 严格检查、Vite 生产构建、依赖安全
扫描和真实浏览器交互完成。

## 2. 为什么选择 React、TypeScript 和 Vite

### 2.1 React

聊天界面包含多个互相联动的状态：当前会话、消息列表、输入内容、活动轮次、SSE 进度、
审批弹窗和移动侧栏。React 适合通过状态驱动界面，避免手动查询 DOM 并逐项更新。

这里没有引入 Redux 等全局状态框架。当前界面只有一个主要页面，状态范围清晰，使用
React 内置的 `useState`、`useEffect`、`useMemo` 和 `useRef` 已经足够。过早增加全局状态
库只会扩大理解和维护成本。

### 2.2 TypeScript

前端必须准确消费后端协议。例如消息角色只能是 `user` 或 `assistant`，轮次状态只能是
约定的六种值，审批又分为工具审批和记忆审批。TypeScript 把这些约束写成静态类型：

```typescript
export type TurnStatus =
  | "running"
  | "awaiting_review"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed";
```

这不能代替服务端校验，但可以在开发阶段提前发现字段拼写和状态处理遗漏。

### 2.3 Vite

Vite 负责开发服务器和生产构建：

- 开发时提供快速模块更新；
- 将 `/api` 代理到 FastAPI，避免开发环境额外处理跨域地址；
- 生产时输出静态 `dist` 文件；
- 与 React 和 TypeScript 的配置较少，适合当前精简前端。

当前安装版本及完整依赖树记录在 `web/pnpm-lock.yaml`。锁文件必须提交，它保证不同开发
机器和 CI 安装到相同依赖版本。

## 3. 前端目录职责

```text
web/
  public/
    assistant-avatar.png   助手头像视觉资产
  src/
    api.ts                 JSON 请求、SSE 读取和 API 错误
    types.ts               浏览器使用的公开协议类型
    App.tsx                页面级状态与工作流协调
    components/
      Sidebar.tsx          会话列表和移动端侧栏
      ChatView.tsx         消息区、进度和输入框
      ReviewDialog.tsx     工具与长期记忆审批弹窗
    styles.css             全局和响应式样式
    main.tsx               React 挂载入口
  vite.config.ts           React 插件和开发代理
  package.json             命令与直接依赖
  pnpm-lock.yaml           可复现依赖锁定
```

组件按照用户界面职责拆分，没有为每个小元素创建文件。这样既避免单个 `App.tsx` 膨胀，
也没有形成过度组件化。

## 4. API 层为什么单独放在 api.ts

React 组件不直接到处调用 `fetch()`。所有接口路径、错误信封、请求头和 SSE 解码集中在
`api.ts`。

普通 JSON API 使用泛型函数：

```typescript
async function requestJson<T>(path: string, init: RequestInit): Promise<T>
```

它统一完成：

- 拼接 `/api/v1` 前缀；
- 添加 `Accept` 和必要的 `Content-Type`；
- 检查 HTTP 状态码；
- 读取后端 `{ error: { message } }` 信封；
- 在反向代理返回非 JSON 错误时提供稳定后备提示。

组件只调用 `listSessions()`、`createSession()`、`startTurn()` 等业务含义明确的函数，不
依赖 URL 和传输细节。

## 5. POST 请求如何消费 SSE

浏览器原生 `EventSource` 主要用于 GET 请求，而开始对话和提交审批需要 JSON POST body，
因此前端使用 `fetch()` 加 `ReadableStream`：

```text
fetch(POST)
  -> response.body.getReader()
  -> TextDecoder
  -> 按空行切分 SSE event block
  -> 解析 event 和 data
  -> 交给 App 更新界面
```

解析器同时支持 `\n\n` 和标准 HTTP 常见的 `\r\n\r\n` 事件边界。它不会先对每个网络
数据块单独替换换行符，因为 CRLF 可能刚好被拆在两个数据块之间；先保留原始缓冲区，
找到完整边界后再规范化单个事件，可以避免偶发的事件丢失或错误切分。

响应建立后立即读取 `X-Turn-ID`，并通过回调交给页面状态。后续停止、审批和刷新恢复都
使用这个 ID，而不是让前端猜测 LangGraph thread id。

## 6. App 页面状态如何组织

`App.tsx` 负责页面级协调，主要状态可以分为四组：

### 6.1 会话状态

- `sessions`：侧栏摘要；
- `activeSessionId`：当前会话；
- `messages`：当前会话的公开消息；
- `loadingSessions`、`loadingHistory`：初始加载状态。

切换会话时创建新的 `AbortController`。旧请求在会话变化后被终止，并且只有未终止请求
可以关闭加载状态，避免快速切换时旧请求覆盖新页面。

### 6.2 一轮对话的状态

- `busy`：当前 SSE 是否仍在执行；
- `progress`：后端提供的粗粒度进度；
- `currentTurnId`：当前活动轮次；
- `streamControllerRef`：页面卸载时中止浏览器连接。

用户消息先乐观加入页面，使输入提交后立即得到反馈。如果请求在服务端建立轮次之前
失败，前端会移除这条乐观消息并恢复输入内容；如果已经获得 turn id，则保留消息并允许
通过后端状态恢复。

### 6.3 审批状态

- `review`：当前待确认内容；
- `selectedReviewIds`：用户选中的长期记忆候选项。

工具审批不能选择子项，因为后端按照一个工具调用批次整体审批；长期记忆审批使用原生
checkbox，默认选中候选项，用户可以逐项取消。真正的合法性仍由后端校验，前端状态只是
交互表现。

### 6.4 错误状态

预期和非预期请求错误统一显示为底部提示，不渲染后端堆栈或内部对象。错误提示可以关闭，
且不会破坏已加载消息。

## 7. 为什么使用 sessionStorage 保存 turn id

LangGraph 的等待审批状态保存在 checkpoint，但浏览器刷新后还需要知道应该查询哪个
`turn_id`。前端在响应建立时写入：

```text
human-chat:turn:<session id> = <turn id>
```

选择会话时：

1. 读取该 session 对应的本地 turn id；
2. 调用 `GET /turns/{turn_id}`；
3. 如果状态是 `awaiting_review`，重新显示审批弹窗；
4. 如果轮次已经结束或不存在，清除记录；
5. 如果刷新时仍显示运行中，则请求取消失去 SSE 订阅的旧轮次。

选择 `sessionStorage` 而不是 `localStorage`，是因为活动轮次属于当前浏览器标签页的短期
状态。关闭标签页后不应长期留下大量失效 turn id。会话历史本身仍由后端 checkpoint
持久化。

## 8. 为什么不展示普通工具调用过程

前端只处理阶段三公开的 `turn.progress`、`message.completed`、`review.required` 等事件。
它没有工具日志列表、Chain of Thought 区域或 Graph 节点调试面板。

这样做有三个原因：

1. 普通用户关心任务是否在处理和最终结果，不需要理解内部节点；
2. 工具参数和返回结果可能包含项目路径或外部系统数据；
3. UI 不应依赖内部 Graph 节点结构，否则后端重构会破坏前端。

只有需要用户授权的高风险操作显示工具名称、读写属性和已经脱敏的参数。这是有效授权所
需的信息，不属于调试过程泄漏。

## 9. 界面设计取舍

本阶段没有制作营销首页，也没有复杂仪表盘。应用打开后直接进入工作界面：

- 左侧是 280px 的会话列表；
- 右侧是固定标题、可滚动消息区和底部输入框；
- 用户消息使用浅绿色背景，助手回答保持开放排版；
- 绿色表示主要动作，珊瑚色只用于风险和停止；
- 按钮统一使用 Lucide 图标；
- 卡片圆角不超过 8px；
- 文本使用 `overflow-wrap` 和明确的最大宽度，避免长内容溢出；
- 页面高度使用 `100dvh`，适配移动浏览器动态工具栏。

头像是项目内的真实位图资产，而不是临时网络 URL。它让助手身份在首屏、标题栏和回复中
保持一致，同时不依赖第三方图片服务。

## 10. 响应式布局

在宽度小于 760px 时：

- 左侧栏变为屏幕外抽屉；
- 标题栏出现菜单图标；
- 打开侧栏时显示半透明遮罩；
- 消息和输入区减小边距；
- 会话标题使用稳定可用宽度并省略超长文本；
- 输入框保持在底部，消息区独立滚动。

侧栏打开、关闭都使用有 `aria-label` 的图标按钮。弹窗使用 `role="alertdialog"` 和
`aria-modal="true"`，输入框、发送和停止按钮也都有可访问名称。

## 11. 依赖和生成文件管理

`.gitignore` 新增：

```text
web/node_modules/
web/dist/
web/*.tsbuildinfo
data/checkpoints/*.sqlite*
```

`node_modules` 可以由锁文件重新安装，`dist` 可以由源码重新构建，TypeScript build info
属于本地增量缓存，因此都不提交。SQLite checkpoint 是运行期数据，也不能进入代码仓库。

提交内容包括 `package.json` 和 `pnpm-lock.yaml`。前者描述直接依赖，后者固定完整依赖树；
两者职责不同，都有保留价值。

## 12. 运行方式

开发环境需要同时启动后端和前端。

后端：

```powershell
python -m human_chat.api
```

前端：

```powershell
Set-Location web
pnpm install
pnpm dev
```

默认访问 `http://127.0.0.1:5173`。Vite 将 `/api` 代理到
`http://127.0.0.1:8000`。如需连接其他地址，可配置 `VITE_API_BASE_URL`。

当前 Vite 工具链要求 Node.js `20.19+` 或 `22.12+`。

## 13. 验证结果

本阶段完成以下验证：

1. `tsc -b` 严格类型检查通过；
2. `vite build` 生产构建通过；
3. 生产 JavaScript gzip 后约 66 KB，CSS gzip 后约 3 KB；
4. `pnpm audit --prod` 未发现已知生产依赖漏洞；
5. 浏览器完成空状态、新建会话、SSE 消息发送与最终回复；
6. 浏览器完成高风险工具审批弹窗、确认和 Graph 恢复；
7. 桌面视口和 390×844 移动视口均无内容遮挡或横向溢出；
8. 移动端侧栏打开和关闭正常；
9. 浏览器控制台没有 error 或 warning；
10. 没有新增测试文件，也没有把临时浏览器验证数据提交到仓库。

下一阶段将统一开发启动方式，并让 FastAPI 在生产模式下直接交付 `web/dist`。完成后，
用户不再需要理解两个开发服务的组合即可运行生产版本。
