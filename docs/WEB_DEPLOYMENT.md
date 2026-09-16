# 网页 Demo 与平台接入

## 运行边界

浏览器只提交结构化患者临床字段、查询任务状态和读取结果。浏览器不能访问 SQLite、MCP Key 或模型 Key。试验库是服务器本地只读快照，患者匹配运行时不调用 MCP。

```text
Browser -> Nginx -> site-trial-matcher API -> read-only SQLite snapshot
                                      \-> optional model API
scheduled updater -> MCP snapshot -> validated SQLite replacement
```

服务阶段与 CancerDAO 网页端保持一致：`prepare`、`execute`、`generate`、`deliver`、`complete`。取消接口先进入 `stopping`；正在进行的外部模型请求返回后停止继续派发任务，随后删除该次运行的临床中间文件。

## 本地启动

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
china-trial-web
```

默认监听 `127.0.0.1:8080`。首次启动会从仓库内经过审计的 MCP 快照构建 `var/trials.db`。生产服务器也可以提前通过 `SITE_TRIAL_DB` 指向已经构建并校验的数据库。

Docker：

```bash
cp .env.example .env
docker compose up --build -d
curl -fsS http://127.0.0.1:8080/healthz
```

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/healthz` | 存活检查 |
| `GET` | `/api/dataset` | 数据版本、试验数量、模型状态 |
| `POST` | `/api/runs` | 创建批量预筛任务 |
| `GET` | `/api/runs/{id}` | 状态、阶段和进度 |
| `POST` | `/api/runs/{id}/cancel` | 停止任务并清理中间数据 |
| `GET` | `/api/runs/{id}/results` | 分页读取患者结果 |
| `GET` | `/api/runs/{id}/results/{index}` | 读取一名患者的潜在/排除试验明细 |

创建任务示例：

```json
{
  "mode": "deterministic",
  "patients": [
    {
      "patient_id": "DEMO-001",
      "country": "中国",
      "age": 58,
      "sex": "男",
      "cancer_type": "非小细胞肺癌",
      "disease_stage": "IV期",
      "biomarkers": ["EGFR L858R"],
      "ecog": 1,
      "treatment_lines_completed": 1
    }
  ]
}
```

`SITE_TRIAL_API_TOKEN` 非空时，所有 `/api` 业务请求必须携带 `Authorization: Bearer <token>`。平台接入建议由 CancerDAO 后端持有这个 token 并代理请求，不要写进前端 JavaScript。独立浏览器 Demo 可不设置该变量，改由 Nginx 访问控制限制演示范围。

## Nginx

```nginx
location /trial-matcher/ {
    proxy_pass http://127.0.0.1:8080/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;
    client_max_body_size 512k;
}
```

服务只发布 JSON API 和薄前端。正式平台仍负责账号、健康档案和页面导航；后续集成时由平台把结构化字段转换成 `POST /api/runs` 请求，并复用现有阶段和取消 UI。

## 数据更新

不要在患者请求期间原地修改数据库。定时任务应当：

1. 拉取 MCP 快照；
2. 在临时路径构建新 SQLite；
3. 检查 `database_as_of`、记录数量和数据库完整性；
4. 原子替换当前数据库；
5. 重启单 worker 服务或让下一次部署加载新版本。

当前 Demo 保持单实例、单 Web worker。SQLite 适合只读匹配；只有扩展到多实例在线写入时才迁移 PostgreSQL。
