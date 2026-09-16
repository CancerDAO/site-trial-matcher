# 输出契约

只返回一个紧凑JSON对象，不加Markdown代码块、思考过程或解释文字：

```json
{
  "analyzed_trials": [
    {
      "trial_id": "ChiCTR...",
      "disposition": "exclude | retain_for_review",
      "exclusion_basis": "scope_mismatch | inclusion_failed | exclusion_triggered | null",
      "confidence": "排除时0.80至1.00；保留时可为null",
      "evidence": {
        "source_kind": "trial_scope | inclusion | exclusion",
        "trial_text": "逐字复制输入中的标题、疾病范围或标准原文",
        "patient_field": "输入患者对象中的字段名",
        "assessment": "一句简短中文事实关系",
        "certainty": "clear",
        "cohort_id": "ALL"
      },
      "review_note": "最多一句简短中文复核提示"
    }
  ]
}
```

约束：

- `analyzed_trials`必须与任务给出的试验ID完全一致，不得遗漏、重复或增加。
- `exclude`必须提供一个 `certainty=clear` 的`evidence`对象，`exclusion_basis`不得为空，且`confidence`不得低于0.80。
- `retain_for_review`的 `exclusion_basis`和`evidence`必须为 `null`；不要重复未知标准，`review_note`保持最短。
- `trial_text`必须逐字复制输入内容，不能改写或编造。
- `patient_field`必须是输入患者对象中已有且非空的字段。
- `scope_mismatch`使用 `source_kind=trial_scope`；`inclusion_failed`使用`inclusion`；`exclusion_triggered`使用`exclusion`。
- `scope_mismatch`只能引用疾病、病理、分期、年龄、性别或分子标志物字段；不能从“未报告耐药/进展/某项治疗”推断不相容。
- 输出不包含患者值；执行器会从患者对象恢复权威值，避免模型改写患者事实。
