# Grafana Viewer 插件设计文档

- 插件名称：`astrbot_plugin_grafana_viewer`
- 显示名称：`Grafana 面板助手`
- 文档版本：`v0.1`
- 状态：设计草案
- 日期：2026-11-02
- 目标 AstrBot 版本：`>=4.10.4,<5`

## 1. 背景

AstrBot 已接入 QQ 群。用户希望通过群聊命令查看已经维护好的 Grafana 面板，而不是在群内重新实现一套监控页面。

Grafana 负责查询 Prometheus 数据和展示图表，插件负责：

1. 根据预配置的视图别名找到 Grafana 面板；
2. 请求 Grafana 生成面板图片；
3. 将 PNG 图片发送到当前 QQ 会话。

数据源仍然是 Prometheus，但插件的直接数据来源是 Grafana 面板渲染结果。

## 2. 目标

- 在 QQ 群中通过 `/grafana` 命令查看指定 Grafana 面板。
- 支持配置多个视图别名，例如 `cpu`、`api`、`db`。
- 支持常见时间范围，例如 `1h`、`6h`、`24h`。
- 复用 Grafana 已配置好的仪表盘、变量和图表样式。
- 服务端完成 Grafana 认证，不向用户暴露 Token。
- 对群聊场景提供缓存、频率限制和权限控制。
- 保持插件职责单一，不承担告警订阅和告警推送。

## 3. 非目标

以下内容不属于第一版范围：

- 不发送 Grafana 页面链接。
- 不允许群成员直接输入 Grafana URL、Dashboard UID、Panel ID 或 PromQL。
- 不实现 Grafana 告警订阅和主动推送；告警场景使用现有 `astrbot_plugin_grafana_alert`。
- 不实现 Dashboard 编辑、面板创建或数据源管理。
- 不直接调用 Grafana `/api/ds/query` 查询原始数据。
- 不实现任意自定义时间范围。

## 4. 核心决策

### 4.1 使用 Grafana 面板渲染

插件使用 Grafana 的图片渲染接口，将指定的 Dashboard Panel 渲染为图片：

```text
/render/d-solo/{dashboard_uid}/{slug}
  ?orgId={org_id}
  &panelId={panel_id}
  &from=now-{range}
  &to=now
  &width={width}
  &height={height}
  &kiosk
```

前提条件：

- Grafana 已部署并正常运行；
- 已部署 `grafana-image-renderer`；
- Grafana 已配置 `GF_RENDERING_SERVER_URL` 和 `GF_RENDERING_CALLBACK_URL`；
- 插件配置了可用的 Grafana Service Account Token。

### 4.2 用户只使用视图别名

用户在命令中只输入配置好的视图别名：

```text
/grafana show cpu
/grafana show cpu 6h
```

插件内部将别名映射到：

- `dashboard_uid`
- `panel_id`
- `slug`
- `org_id`
- 模板变量
- 图片尺寸
- 默认时间范围和允许的时间范围

这样可以避免任意 URL 请求、SSRF 和高开销查询。

### 4.3 服务端下载图片，再发送到 QQ

插件不能用带 Token 的 Grafana 渲染 URL 直接回复群聊。

正确流程：

```text
插件 -> Grafana /render/...（携带 Authorization）
Grafana -> 插件：PNG 字节
插件 -> AstrBot：Comp.Image.fromBytes(png_bytes)
AstrBot -> QQ 群：图片消息
```

### 4.4 告警功能保持独立

本插件只负责“查看面板”。

如果后续需要告警推送，继续使用：

- `astrbot_plugin_grafana_alert`

不把 Webhook、告警路由、订阅和静默逻辑混入本插件。

## 5. 整体架构

```text
QQ 群
  |
  v
AstrBot 消息适配器
  |
  v
astrbot_plugin_grafana_viewer
  |-- 命令解析
  |-- 视图配置查询
  |-- 时间范围校验
  |-- 缓存 / 频率限制
  |-- Grafana 图片渲染请求
  |
  v
Grafana /render/d-solo/...
  |
  v
grafana-image-renderer
  |
  v
Grafana Dashboard / Panel
  |
  v
Prometheus
```

## 6. 命令设计

### 6.1 命令树

```text
/grafana
├── list [关键词]
├── show <视图名> [时间范围]
├── status                管理员
└── test [视图名]          管理员
```

第一版不提供页面链接命令。

### 6.2 命令说明

| 命令 | 权限 | 说明 |
|---|---|---|
| `/grafana` | 群成员 | 显示帮助 |
| `/grafana list` | 群成员 | 列出已配置的视图 |
| `/grafana list <关键词>` | 群成员 | 按名称或标题过滤视图 |
| `/grafana show <视图>` | 群成员 | 使用默认时间范围渲染图片 |
| `/grafana show <视图> <范围>` | 群成员 | 使用指定时间范围渲染图片 |
| `/grafana status` | 管理员 | 检查配置、连接和缓存状态 |
| `/grafana test [视图]` | 管理员 | 实际执行一次渲染测试 |

### 6.3 `/grafana`

显示中文帮助信息。

示例：

```text
Grafana 面板助手

/grafana list                 查看可用视图
/grafana show cpu             查看 CPU 面板
/grafana show cpu 6h          查看最近 6 小时
/grafana status               检查服务状态
/grafana test cpu             测试面板渲染
```

### 6.4 `/grafana list`

列出配置中的视图，不调用 Grafana，响应应当快速。

输出示例：

```text
可用 Grafana 视图：

• cpu — CPU 使用率
  默认 1h，可选 1h / 6h / 24h

• api — API QPS / 延迟
  默认 1h，可选 1h / 6h / 24h

• db — 数据库连接数
  默认 6h，可选 6h / 24h
```

规则：

- 最多显示前 20 个视图；
- 超过 20 个时提示使用关键词过滤；
- 无配置时提示管理员先配置 `views`；
- 不显示 Token、内部 URL 或完整请求参数。

### 6.5 `/grafana show <视图> [范围]`

核心命令。

示例：

```text
/grafana show cpu
/grafana show cpu 6h
/grafana show api 24h
```

处理流程：

1. 校验当前群是否在允许列表中；
2. 校验视图别名是否存在；
3. 校验时间范围是否在视图的 `allowed_ranges` 中；
4. 检查缓存；
5. 构造 Grafana 渲染 URL；
6. 使用 `httpx.AsyncClient` 请求 Grafana；
7. 校验响应状态、Content-Type 和图片大小；
8. 将图片字节转换为 AstrBot 图片组件；
9. 返回图片消息。

成功时只发送图片，不发送内部 URL 和 Token。

建议附带简短说明，例如：

```text
CPU 使用率 · 最近 6 小时
```

### 6.6 `/grafana status`

仅管理员可用。

建议输出：

```text
Grafana 地址：已配置
Grafana 连接：正常
图片渲染器：正常
已配置视图：3 个
缓存条目：12 个
默认超时：10 秒
```

不得输出：

- Grafana Token；
- Authorization 请求头；
- 完整内部 URL 中的敏感参数；
- 服务器堆栈信息。

### 6.7 `/grafana test [视图]`

仅管理员可用。

行为：

- 未指定视图时，使用第一个已配置视图；
- 执行一次真实渲染请求；
- 成功时发送测试图片；
- 失败时返回可操作的错误信息。

错误分类：

- 配置缺失；
- Grafana 不可达；
- Grafana 认证失败；
- Dashboard 或 Panel 不存在；
- Image Renderer 不可用；
- 渲染超时；
- 图片过大或响应格式错误。

## 7. 时间范围设计

### 7.1 允许范围

第一版只接受以下格式：

```text
15m
1h
6h
24h
7d
30d
```

其中：

- `m` = 分钟；
- `h` = 小时；
- `d` = 天；
- 不支持任意数字，例如 `37h`；
- 不支持 `now-1h`、`now()` 等表达式；
- 不支持由用户输入 `from` 和 `to`。

### 7.2 视图级限制

每个视图单独配置：

```json
{
  "default_range": "1h",
  "allowed_ranges": ["1h", "6h", "24h"]
}
```

例如 CPU 面板允许查看 24 小时，但数据库面板只允许查看 6 小时，可以在配置中限制。

### 7.3 时间范围解析

插件只接受枚举值，并转换为 Grafana 参数：

```text
1h  -> from=now-1h&to=now
6h  -> from=now-6h&to=now
24h -> from=now-24h&to=now
```

不需要在插件中计算绝对时间戳。

## 8. 配置设计

配置文件为 `_conf_schema.json`，管理面板中生成对应配置。

### 8.1 全局配置

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `grafana_base_url` | string | `http://grafana:3000` | 插件访问 Grafana 的内部地址 |
| `grafana_token` | string | 空 | Service Account Token，建议设置 `secret: true` |
| `grafana_org_id` | int | `1` | 默认 Grafana Org ID |
| `render_timezone` | string | `Asia/Shanghai` | 渲染时区；留空则使用 Grafana 默认时区 |
| `request_timeout` | int | `10` | 单次 Grafana 请求超时，单位秒 |
| `render_width` | int | `1000` | 默认渲染宽度 |
| `render_height` | int | `600` | 默认渲染高度 |
| `cache_seconds` | int | `20` | 图片缓存时间 |
| `cooldown_seconds` | int | `15` | 同一群发起新渲染的冷却时间 |
| `allowed_groups` | list | `[]` | 允许使用的群号；空列表表示不限制，生产环境建议显式配置 |
| `max_image_bytes` | int | `8388608` | 单张图片最大字节数，默认 8 MiB |

### 8.2 视图配置

建议使用 `template_list` 类型，每个视图包含：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 命令中使用的别名，例如 `cpu` |
| `title` | string | 是 | 展示名称，例如 `CPU 使用率` |
| `dashboard_uid` | string | 是 | Grafana Dashboard UID |
| `panel_id` | int | 是 | Grafana Panel ID |
| `slug` | string | 否 | Grafana Dashboard slug，默认使用 `name` 或 `dashboard_uid` |
| `org_id` | int | 否 | 覆盖全局 Org ID |
| `default_range` | string | 否 | 默认时间范围，默认 `1h` |
| `allowed_ranges` | list | 否 | 允许的时间范围，默认 `["1h", "6h", "24h"]` |
| `variables` | dict | 否 | 附加模板变量，例如 `{"instance": "prod-1"}` |
| `width` | int | 否 | 覆盖全局宽度 |
| `height` | int | 否 | 覆盖全局高度 |

示例：

```json
{
  "name": "cpu",
  "title": "CPU 使用率",
  "dashboard_uid": "prod-overview",
  "panel_id": 4,
  "slug": "prod-overview",
  "org_id": 1,
  "default_range": "1h",
  "allowed_ranges": ["1h", "6h", "24h"],
  "variables": {
    "instance": "prod-1"
  },
  "width": 1000,
  "height": 600
}
```

### 8.3 变量处理

`variables` 中的键值转换为 Grafana 模板变量参数：

```text
var-instance=prod-1
var-job=api
```

所有变量必须使用 URL 编码。

变量来源只能是插件配置，不能由普通群成员直接输入。

## 9. Grafana 请求设计

### 9.1 请求地址

```text
GET {grafana_base_url}/render/d-solo/{dashboard_uid}/{slug}
```

查询参数：

| 参数 | 说明 |
|---|---|
| `orgId` | Grafana Org ID |
| `panelId` | Panel ID |
| `from` | `now-{range}` |
| `to` | `now` |
| `width` | 图片宽度 |
| `height` | 图片高度 |
| `kiosk` | 隐藏 Grafana 导航和界面元素 |
| `tz` | 渲染时区，例如 `Asia/Shanghai`；未配置时省略 |
| `var-*` | 视图配置中的模板变量 |

### 9.2 认证

请求头：

```http
Authorization: Bearer <grafana_token>
Accept: image/png,image/jpeg
```

Token 只存在于插件配置和服务端请求中。

### 9.3 响应校验

只接受：

- `200 OK`；
- `Content-Type` 为 `image/png` 或 `image/jpeg`；
- 响应体非空；
- 响应体大小不超过 `max_image_bytes`。

其他情况统一转换为用户可理解的错误。

## 10. 缓存设计

缓存键建议包含：

```text
视图名
时间范围
Dashboard UID
Panel ID
变量集合
图片宽高
```

缓存策略：

- 默认缓存 20 秒；
- 最大缓存条目建议 100 个；
- 使用 LRU 淘汰；
- 只缓存成功图片；
- 错误默认不缓存，或只做短时间负缓存；
- 管理员执行 `/grafana test` 时不使用缓存，确保测试真实渲染链路。

## 11. 限流与并发

建议：

- 每个群 `cooldown_seconds` 内最多触发一次新的图片渲染；
- 全局同时最多 2 个 Grafana 渲染请求；
- 每个群同时最多 1 个渲染请求；
- 请求超时默认 10 秒；
- 单张图片最大 8 MiB；
- 渲染失败不重试超过 1 次；
- 对相同视图和范围的并发请求合并为一次请求。

## 12. 权限与访问控制

### 12.1 普通群成员

可以执行：

```text
/grafana
/grafana list
/grafana show <视图> [范围]
```

### 12.2 管理员

额外可以执行：

```text
/grafana status
/grafana test [视图]
```

### 12.3 群访问控制

`allowed_groups` 用于限制插件只在指定群工作。

建议生产环境显式填写群号，不使用空列表的“全部允许”模式。

非允许群：

- 忽略命令，或返回简短提示；
- 不执行 Grafana 请求；
- 记录一次受限访问日志，但不输出敏感信息。

## 13. 错误处理

用户侧错误消息建议：

| 场景 | 消息 |
|---|---|
| 视图不存在 | `未找到该视图，请使用 /grafana list 查看可用视图。` |
| 范围不允许 | `该视图不支持此时间范围，可选范围：1h / 6h / 24h。` |
| 未配置 Token | `Grafana 认证未配置，请联系管理员。` |
| Grafana 不可达 | `Grafana 连接失败，请稍后重试。` |
| 认证失败 | `Grafana 认证失败，请联系管理员检查配置。` |
| 渲染器不可用 | `Grafana 图片渲染器不可用，请联系管理员。` |
| 面板不存在 | `面板不存在或配置已失效，请联系管理员。` |
| 渲染超时 | `Grafana 渲染超时，请稍后重试。` |
| 图片过大 | `渲染结果过大，已拒绝发送。` |
| 频率限制 | `操作过于频繁，请稍后再试。` |

详细错误信息写入 AstrBot logger，不直接返回给群成员。

## 14. 安全设计

- 不允许用户输入任意 URL；
- 不允许用户输入任意 Dashboard UID 或 Panel ID；
- 不允许用户输入 PromQL；
- 不允许在群消息中输出 Grafana Token；
- 不允许把带认证信息的 Grafana 渲染 URL 发送给 QQ；
- `grafana_base_url` 必须是配置项，不能由命令参数覆盖；
- 图片响应设置大小上限；
- 管理命令只允许管理员执行；
- 对群聊命令设置冷却和并发限制；
- 第一版不发送页面链接，因此不需要配置公开 Grafana 地址。

## 15. 建议代码结构

```text
astrbot_plugin_grafana_viewer/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── docs/
│   └── design.md
└── core/
    ├── config.py
    ├── grafana_client.py
    ├── cache.py
    ├── ranges.py
    └── errors.py
```

职责建议：

- `main.py`：AstrBot 插件入口、命令注册、生命周期；
- `config.py`：配置读取和视图校验；
- `grafana_client.py`：Grafana 渲染请求、认证、响应校验；
- `cache.py`：图片缓存和并发控制；
- `ranges.py`：时间范围解析和校验；
- `errors.py`：统一错误类型和用户提示。

## 16. 命令实现示意

使用 AstrBot 命令组：

```python
@filter.command_group("grafana")
async def grafana(self, event: AstrMessageEvent):
    """Grafana 面板助手"""
    yield event.plain_result("Grafana 面板助手...")

@grafana.command("list")
async def grafana_list(self, event: AstrMessageEvent):
    ...

@grafana.command("show")
async def grafana_show(self, event: AstrMessageEvent, args: GreedyStr):
    ...

@grafana.command("status")
async def grafana_status(self, event: AstrMessageEvent):
    ...

@grafana.command("test")
async def grafana_test(self, event: AstrMessageEvent, args: GreedyStr):
    ...
```

说明：

- 使用 `GreedyStr` 解析 `<视图名> [时间范围]`；
- 命令处理函数的 docstring 会用于帮助信息；
- 不在命令处理函数中直接拼接未经校验的 URL；
- 所有 Grafana 请求统一走 `GrafanaClient`。

## 17. 验收标准

第一版完成后应满足：

- `/grafana` 能显示中文帮助；
- `/grafana list` 能列出已配置视图；
- `/grafana show cpu` 能发送 PNG 图片；
- `/grafana show cpu 6h` 能使用指定时间范围；
- 不在允许列表中的群无法触发渲染；
- 非管理员无法执行 `/grafana status` 和 `/grafana test`；
- 非法视图名和时间范围有明确提示；
- Grafana Token 不出现在群消息、日志或异常输出中；
- 不提供 Grafana 页面链接命令；
- Grafana 认证失败、渲染器不可用、超时和图片过大均有可读错误；
- 缓存和冷却机制能避免短时间重复渲染；
- 插件卸载/停用时关闭 HTTP 客户端。

## 18. 测试计划

### 单元测试

- 时间范围解析；
- 视图配置校验；
- 视图别名查找；
- Grafana 渲染 URL 构造；
- 变量 URL 编码；
- 缓存键生成；
- 管理员权限判断；
- 群访问控制。

### 集成测试

使用本地模拟 HTTP 服务覆盖：

- 200 图片响应；
- 401/403 认证失败；
- 404 面板不存在；
- 500 渲染器错误；
- 超时；
- 非图片响应；
- 图片超过大小限制。

### 手工测试

1. 在 Grafana 中确认 `/render/...` 可以生成图片；
2. 通过 `/grafana test cpu` 测试真实链路；
3. 在目标 QQ 群执行 `/grafana show cpu`；
4. 测试不同时间范围；
5. 测试非管理员权限；
6. 测试非允许群；
7. 测试 Grafana 停止运行和 Token 失效时的提示。

## 19. 后续版本候选

以下功能不进入第一版：

- `/grafana alerts`：只读查询 Grafana 当前告警；
- 告警订阅与推送；
- 自定义绝对时间范围；
- 多条面板拼图；
- 管理员通过 WebUI 管理视图；
- LLM 工具调用；
- 直接把 Prometheus 查询结果渲染成自定义卡片。

## 20. 结论

第一版保持插件职责单一：

```text
Grafana 面板 -> 图片 -> QQ 群
```

命令只保留：

```text
/grafana
/grafana list
/grafana show <视图> [范围]
/grafana status
/grafana test [视图]
```

不发送 Grafana 页面链接，不承担告警推送。