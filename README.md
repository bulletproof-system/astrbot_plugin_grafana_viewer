# Grafana 面板助手

`astrbot_plugin_grafana_viewer` 是一个 AstrBot 插件。它把管理员预先配置好的 Grafana Dashboard Panel 渲染成图片，并发送到当前聊天会话。

插件只接受配置中的视图别名，不允许聊天成员输入 Grafana URL、Dashboard UID、Panel ID、PromQL 或任意时间表达式。

## 功能

- `/grafana list [关键词]`：快速查看可用视图
- `/grafana show <视图> [范围]`：渲染并发送面板图片
- `/grafana status`：管理员检查配置、连接和缓存状态
- `/grafana test [视图]`：管理员绕过缓存实际测试渲染链路
- 图片 TTL + LRU 缓存
- 群级冷却、群级并发限制、全局渲染并发限制
- 相同视图和参数的并发请求合并
- 白名单群访问控制
- Grafana Token 仅在服务端请求中使用，不进入聊天消息或日志
- 插件停用时关闭 HTTP 客户端

## 前置条件

1. AstrBot `>=4.10.4,<5`
2. Grafana 已正常运行
3. Grafana 已部署并配置 `grafana-image-renderer`
4. 已配置 `GF_RENDERING_SERVER_URL` 和 `GF_RENDERING_CALLBACK_URL`
5. 已创建具备 Dashboard 查看权限的 Grafana Service Account Token

## 安装

把仓库放入 AstrBot 的插件目录，或在 AstrBot 插件管理中通过仓库地址安装。

插件依赖：

```text
httpx>=0.27,<1
```

## 配置

配置项由 `_conf_schema.json` 生成，主要配置如下：

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `grafana_base_url` | `http://grafana:3000` | 插件服务端访问 Grafana 的内部地址 |
| `grafana_token` | 空 | Grafana Service Account Token |
| `grafana_org_id` | `1` | 默认 Org ID |
| `render_timezone` | `Asia/Shanghai` | 渲染时区；留空则使用 Grafana 默认时区 |
| `request_timeout` | `10` | 单次请求超时（秒） |
| `render_width` | `1000` | 默认图片宽度 |
| `render_height` | `600` | 默认图片高度 |
| `cache_seconds` | `20` | 成功图片缓存时间；0 表示关闭 |
| `cooldown_seconds` | `15` | 同一群发起新渲染的冷却时间 |
| `allowed_groups` | `[]` | 允许使用的群号；空列表表示不限制 |
| `max_image_bytes` | `8388608` | 单张图片最大字节数 |

### 视图示例

管理面板中每个 `views` 条目包含以下字段：

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
  "variables": "{\"instance\":\"prod-1\",\"job\":\"api\"}",
  "width": 1000,
  "height": 600
}
```

说明：

- `name` 是聊天中唯一可输入的视图别名，不能包含空白。
- `slug` 留空时使用 `dashboard_uid`。
- `org_id`、`width`、`height` 为 `0` 时使用全局配置。
- `variables` 在管理面板中填写 JSON 对象；配置文件里也兼容直接对象写法。
- `default_range` 必须包含在 `allowed_ranges` 中。
- 支持的范围固定为 `15m`、`1h`、`6h`、`24h`、`7d`、`30d`。

示例：

```json
{
  "name": "db",
  "title": "数据库连接数",
  "dashboard_uid": "db-overview",
  "panel_id": 7,
  "default_range": "6h",
  "allowed_ranges": ["6h", "24h"]
}
```

## 命令

```text
/grafana
/grafana list
/grafana list api
/grafana show cpu
/grafana show cpu 6h
/grafana status
/grafana test
/grafana test cpu
```

`status` 和 `test` 仅管理员可执行。`test` 不使用缓存，也不触发群级冷却，便于确认 Grafana 与图片渲染器链路。

## 错误提示

插件会把 Grafana 认证失败、连接失败、面板不存在、渲染器不可用、渲染超时、非图片响应和图片过大转换为可读消息。详细原因仅写入 AstrBot 日志，不向聊天成员输出完整 URL、Authorization 请求头或 Token。

## 开发与测试

```bash
uv run --with pytest --with httpx python -m pytest -q
uv run --with ruff ruff check .
uv run --with httpx python -m compileall -q main.py core
```

测试覆盖配置校验、时间范围、URL 与变量编码、HTTP 错误分类、图片大小限制、并发请求合并、缓存命中和群级冷却。

## 安全边界

- 不提供 Grafana 页面链接命令。
- 聊天成员不能覆盖 Grafana 地址、Dashboard UID、Panel ID、模板变量或时间表达式。
- 非允许群不会触发 Grafana 请求。
- 管理员命令在插件内部再次校验管理员身份。
- 图片按流式响应读取，并在超过 `max_image_bytes` 时立即拒绝。
