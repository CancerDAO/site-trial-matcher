# ChiCTR / WHO ICTRP XML 字段映射

本映射以 `Chictr20260831171716.xml` 的实际结构为标准。XML 根节点为 `trials`，每个 `trial` 是一项试验；所有来源内容同时完整保存在 `trials.raw_json`，规范化字段只用于检索与排除判断。

## 主记录

| XML 路径 | 数据库字段 | 用途 |
|---|---|---|
| `main/trial_id` | `trials.partner_trial_id`、`trial_registry_ids.registry_id` | 主身份、去重、结果展示 |
| `main/reg_name` | `trials.primary_registry` | 注册库来源 |
| `main/utrn` | `trials.utrn` | WHO 通用注册号 |
| `main/public_title` | `trials.title_zh/title_en` | 检索、疾病范围证据 |
| `main/scientific_title` | `trials.scientific_title` | 补充检索与人工复核 |
| `main/recruitment_status` | `trials.recruitment_status` | 前置排除 |
| `main/study_type`、`study_design`、`phase` | 同名规范化字段 | 范围展示，不做疗效判断 |
| `main/hc_freetext` | `trials.primary_disease` | 疾病范围判断 |
| `main/i_freetext` | `trials.intervention_summary`、队列干预 | 展示和队列解析 |
| `main/target_size` | 目标例数及队列表 | 展示和队列解析 |
| `main/date_registration`、`date_enrolment` | 注册、首例计划日期 | 新旧排序、展示 |
| `main/primary_sponsor` | 主办单位和机构表 | 合作关系管理 |
| `main/url` | `trials.source_url` | 来源追溯、中国证据 |

## 一对多记录

| XML 节点 | 数据库表 | 说明 |
|---|---|---|
| `secondary_ids/secondary_id` | `trial_registry_ids` | 保留签发机构、辅助注册号与链接 |
| `contacts/contact` | `trial_contacts` | 类型、姓名、单位、国家、城市、地址、电话、邮箱 |
| `countries/country2` | `trial_sites` | 仅作为国家证据，不冒充具名中心 |
| `criteria/inclusion_criteria` | `eligibility_criteria` | 保存原文并按可识别编号拆条；可靠模式才结构化 |
| `criteria/exclusion_criteria` | `eligibility_criteria` | 同上；换行片段不直接当独立医学事实 |
| `criteria/agemin`、`agemax`、`gender` | `trials` 基础范围字段 | 确定性年龄/性别排除 |
| `primary_outcome`、`secondary_outcome` | `trial_outcomes` | 为档案完整性和人工复核保存，不进入模型决策 |
| `ethics_reviews/ethics_review` | `trial_ethics_reviews` | 档案信息，不进入匹配决策 |
| `source_support/source_name` | `trial_support_sources` | 资助/支持来源，不进入匹配决策 |

## 当前不用于自动排除的内容

结果发表字段、论文链接、疗效结果、风险评价、结局指标、伦理联系人、资助来源不会进入自动排除逻辑；它们只为来源完整性和医学人工复核保留。结构化值与原文冲突时，以原文为审计依据并转人工复核。
