# 疾病术语管理

疾病术语分为四层：标准参考库、启用实体字典、医院本地码映射和待确认术语。

- 标准参考库用于检索、审核和批量启用，不直接参与指南解析。
- 启用实体字典是解析、图谱和规则实际使用的疾病集合。
- 医院本地码只有在映射状态为 `approved` 时才参与病历标准化。
- 模糊匹配或未匹配的抽取结果进入待确认队列，不自动写入启用字典。

## 导入授权标准库

管理界面入口：`实体词典 → 疾病 → 标准术语库 → 导入授权标准库文件`。

支持 UTF-8 编码的 JSON 和 CSV，单个文件最大 20MB、最多 100000 条。建议先执行“仅校验”，确认无重复编码和非法编码后再合并。正式导入必须提供标准版本和数据来源。

CSV 支持以下中英文列名：

```csv
疾病编码,疾病名称,别名,专科
I10,原发性高血压,高血压|高血压病,心血管内科
J18.9,肺炎，病原体未特指,社区获得性肺炎|肺部感染,呼吸内科
```

JSON 可以是条目数组，也可以包含元数据：

```json
{
  "standard": "ICD-10",
  "version": "医院实际采用的标准版本",
  "source": "授权文件名称或发布机构",
  "entries": [
    {
      "code": "I10",
      "name": "原发性高血压",
      "aliases": ["高血压", "高血压病"],
      "specialty": "心血管内科"
    }
  ]
}
```

导入采用 `merge` 模式：相同编码更新参考条目，新编码追加，原参考库中的其他编码保留。新导入的参考术语不会自动进入启用字典。

## 专科疾病包

当前提供心血管、肿瘤与血液、呼吸、消化、内分泌代谢、神经、感染和泌尿生殖八个包。专科包按 ICD-10 章节筛选当前参考库，页面会显示参考条目数、已启用数和可启用数。

启用专科包只复制缺失条目，不覆盖已启用实体，也不会修改已有规则和图谱。

## API

- `POST /api/vocab/reference/icd10/catalog/import`：校验或合并 JSON/CSV 文件。
- `GET /api/vocab/reference/icd10/bundles`：预览专科疾病包。
- `POST /api/vocab/reference/icd10/bundles/{bundle_id}/activate`：启用专科包缺失条目。
- `GET /api/vocab/terminology/candidates`：查询待确认术语。
- `POST /api/vocab/terminology/candidates/{candidate_id}/review`：通过或拒绝候选术语。
- `GET/POST/PUT /api/vocab/terminology/mappings`：维护医院本地码映射。
