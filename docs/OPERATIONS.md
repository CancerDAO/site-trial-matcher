# 数据库建立与更新

## 数据边界

生产匹配读取服务器本地 SQLite，不在患者请求期间调用 MCP。数据库来源可以组合：

1. WHO MCP 中国、招募中、干预性肿瘤试验快照；
2. 合作方 Excel 试验信息表；
3. ChiCTR / WHO ICTRP XML。

所有来源最终写入相同的试验、注册号、中心、队列、干预和入排标准表。原始注册库对象保存在 `raw_json`，供后续审计。

## 首次建立生产数据库

MCP 地址和密钥只放在服务端环境文件：

```dotenv
WHO_MCP_URL=https://mcp.example.com/mcp
WHO_MCP_API_KEY=server-side-secret
```

建立只有 MCP 数据的数据库：

```bash
.venv/bin/china-trial-demo refresh-database \
  --db /data/trials.db \
  --snapshot /data/snapshots/who-mcp-china.json \
  --limit 300 \
  --scan-limit 10000 \
  --workers 8 \
  --min-trials 100
```

同时导入合作方来源时，可重复传入 `--workbook` 和 `--xml`：

```bash
.venv/bin/china-trial-demo refresh-database \
  --db /data/trials.db \
  --snapshot /data/snapshots/who-mcp-china.json \
  --workbook /data/partner/acme-trials.xlsx \
  --xml /data/partner/chictr-export.xml \
  --min-trials 100
```

命令在目标数据库目录创建临时数据库，依次完成导入、`integrity_check`、外键检查、试验数、入排标准数和招募中试验数检查。只有全部通过才用 `os.replace` 原子切换生产库；下载、导入或校验失败不会修改现有数据库。

`--reuse-snapshot` 可用于从已经审核的快照重建数据库，不访问 MCP。`--allow-partial` 只适用于已经确认上游分页存在固定上限的情况；报告会保留该数据边界，不得将其描述为注册库全量快照。

## 合作方采集表

- `templates/合作方单试验填写表_v4.xlsx`：一个试验一个文件，适合研究中心人工提交；
- `templates/合作方临床试验数据填写模板_v3.xlsx`：多工作表批量格式，适合合作方数据团队；
- `scripts/build_single_trial_form.mjs` 和 `scripts/build_partner_template.mjs`：模板生成源代码；
- `src/china_trial_demo/workbook_v4.py` 和 `workbook_v3.py`：导入与校验实现。

模板只收集试验资料，不填写患者姓名、联系方式、证件号或病历。入排标准原文始终保留，只有可可靠计算的条件才进入结构化字段。

## 每周更新

仓库提供 `deploy/systemd/site-trial-database-refresh.service` 和 `.timer` 示例。安装前按服务器真实数据目录调整 `ExecStart`，并确认 `.env` 仅允许服务账户读取。

```bash
sudo install -m 0644 deploy/systemd/site-trial-database-refresh.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/site-trial-database-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now site-trial-database-refresh.timer
sudo systemctl list-timers site-trial-database-refresh.timer
```

首次启用定时器前先手工运行并检查结果：

```bash
sudo systemctl start site-trial-database-refresh.service
sudo systemctl status site-trial-database-refresh.service --no-pager -l
sudo journalctl -u site-trial-database-refresh.service -n 200 --no-pager
```

## 更新验收

```bash
.venv/bin/python scripts/audit_demo_database.py \
  --db /data/trials.db \
  --snapshot /data/snapshots/who-mcp-china.json

curl -fsS http://127.0.0.1:8080/api/dataset | python3 -m json.tool
```

至少记录数据库时间、试验数、入排标准数、来源注册库、快照是否完整以及失败详情数。若服务配置了 `SITE_TRIAL_API_TOKEN`，调用 `/api/dataset` 时还需提供对应 Bearer 请求头。

