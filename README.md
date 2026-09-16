# 中国临床试验排除优先匹配 Demo

本项目对多个患者分别筛查本地中国癌症试验库：只排除“有明确证据证明明显不相关或不能入组”的试验，其余全部作为潜在试验返回医学人员复核。项目不做疗效、风险、论文、机制或治疗推荐分析。

## 数据入口

- ChiCTR/WHO ICTRP XML：合作方可直接提交从 ChiCTR 页面下载的 XML，原始层级和原文存入 `raw_json`，同时拆分为可检索关系表。
- 中文 Excel 模板提供两种模式：第4版“一个试验一个文件、一个可见工作表”供人工填写；第3版多工作表格式供数据团队批量提交。
- WHO MCP：周期性拉取较新的中国地区、招募中、干预性癌症试验到本地快照，再导入 SQLite；患者匹配运行时不依赖远程 MCP。

中国试验采用证据口径：ChiCTR 注册号、ChiCTR 详情链接，或注册数据明确列出 China/中国。国家级记录与已核验研究中心分别保存，不能互相替代。合作关系仅决定输出分组，不参与医学排除判断。

字段映射见 [XML字段映射](docs/XML_FIELD_MAPPING.md)，架构与保守排除逻辑见 [设计说明](docs/DESIGN.md)。

## 合作方模板

- [单试验单页面模板（推荐）](templates/合作方单试验填写表_v4.xlsx)：适合人工逐个试验填写。
- [批量多工作表模板](templates/合作方临床试验数据填写模板_v3.xlsx)：适合合作方数据团队批量提交。

合作方提交的试验默认标记为合作试验；合作关系只影响结果分组，不影响医学排除。仓库不包含患者真实身份数据、API Key 或模型原始调用记录。

## 运行

```powershell
$env:PYTHONPATH = "src"

# 导入合作方/ChiCTR XML
python -m china_trial_demo.cli import-xml --db data/demo_recent.db --xml path\partner.xml

# 导入合作方中文模板
python -m china_trial_demo.cli import-workbook --db data/demo_recent.db --workbook path\合作方模板.xlsx

# MCP 密钥只放环境变量，不写入配置或日志
$env:WHO_MCP_URL = "http://43.163.116.103/mcp"
$env:WHO_MCP_API_KEY = Read-Host "WHO MCP API Key"
$env:WHO_MCP_ALLOW_INSECURE_HTTP = "1"
# 严格模式会完整分页后再按解析日期排序；未到结果末尾会拒绝生成
python -m china_trial_demo.cli fetch-mcp --out data/who-mcp-china-latest-200.json --limit 200 --scan-limit 20000 --workers 8

# 当前服务端 offset 封顶 10000，demo 快照因此明确标记为“可访问候选集中的最近200条”。
# 仅在接受这一限制时使用诊断开关；不得对外宣称全库全局最新。
python -m china_trial_demo.cli fetch-mcp --out data/who-mcp-china-latest-200.json --limit 200 --scan-limit 20000 --workers 8 --allow-partial
python -m china_trial_demo.cli init-db --db data/demo_latest_200.db
python -m china_trial_demo.cli import-mcp --db data/demo_latest_200.db --snapshot data/who-mcp-china-latest-200.json

# 本地确定性预筛；默认不截断候选
python -m china_trial_demo.cli match-batch --db data/demo_latest_200.db --patients examples/patients.jsonl --out outputs/demo-results-latest200.json

# 为未被确定性排除的试验生成 Skill 模型任务
python -m china_trial_demo.cli prepare-model-jobs --db data/demo_latest_200.db --patients examples/patients.jsonl --deterministic-results outputs/demo-results-latest200.json --run-dir outputs/model-run-latest200
python -m china_trial_demo.cli run-model-jobs --run-dir outputs/model-run-latest200 --workers 8
python -m china_trial_demo.cli merge-model-results --run-dir outputs/model-run-latest200 --out outputs/demo-results-latest200-final.json
```

模型任务只允许输出 `exclude` 或 `retain_for_review`。排除必须逐字绑定试验标准、指向非空患者字段并通过本地校验；缺失、歧义、断句不完整或证据不足一律保留复核。

## 输出

- `potential_trials.partner/non_partner`：当前没有充分排除证据的合作/非合作试验。
- `excluded_trials.partner/non_partner`：明确关闭招募、疾病不相容、结构化条件冲突或证据绑定的模型排除。
- `registry_id`：任意主注册号；`chictr_registration_number` 仅在确为 ChiCTR 时填写。
- `timing_ms`：本地各阶段耗时；模型阶段另有断点任务执行摘要。

本项目是预筛工具，不替代研究者确认和临床判断。

最新 demo 数据与医学流程审计见 [2026-09-03 审计报告](docs/MEDICAL_AUDIT_2026-09-03.md)。
