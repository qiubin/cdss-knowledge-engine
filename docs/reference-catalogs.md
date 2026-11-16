# 多类型标准参考术语库

实体字典中的疾病、检查、检验、药品、干预操作均提供“标准术语库”入口。疾病沿用 ICD-10 及专科包；手术通过干预操作字典接入现有模板。

| 分类 | 试用子集 | 初始条数 | 活跃字典 |
|---|---|---:|---|
| 检查 | LOINC 检查报告概念 | 2 | exam |
| 检验 | LOINC（保留标本、方法或量纲限定） | 6 | lab |
| 药品 | ATC 分类，不是具体规格药品标识 | 2 | medication |
| 手术 | 历史 ICD-9-CM-3，非中国国家临床版 | 2 | intervention |

这些是公开来源核对的试用子集，不是完整标准库，中文名称由本项目翻译。每条保存来源网址，参考库不直接参与指南识别。用户选用后才复制到活跃字典，不会重写已有实体编码。已有同编码、同名或同标准映射的条目跳过，避免覆盖现有知识引用。

参考条目与版本文件位于 `data/vocab/reference/`。检验须核对标本、方法和量纲；检查报告概念不等同于院内收费项目；ATC 条目也不等同于厂家、剂型和规格。

## 扩展标准文件

每类均支持 UTF-8 CSV 或 JSON，最大 20MB、最多 100000 行。文件校验仅验证结构与基本编码格式，不替代标准发布机构的真实性核对。可先“仅校验”，再“校验并合并”；正式导入须有版本和来源，导入参考库后仍须勾选启用。

CSV 列可使用 `code,name,aliases,standard_system,standard_code,source,specialty`；别名以分号分隔。也支持“编码、名称、别名、编码体系、标准编码”等中文列名。`code` 为字典编码，`standard_code` 为外部标准编码，未填时沿用 code。JSON 可含属性：

```json
{
  "entity_type": "lab",
  "standard": "院内检验目录",
  "version": "本院目录-v1",
  "source": "本院检验科维护文件",
  "entries": [
    {
      "code": "LOCAL_LAB_001",
      "name": "院内检验项目示例",
      "aliases": ["本地别名"],
      "standard_system": "院内检验目录",
      "standard_code": "LOCAL_LAB_001",
      "attributes": {"specimen": "请填写实际标本"}
    }
  ]
}
```

## API

- `GET /api/vocab/reference/catalogs`：四类目录及元数据。
- `GET /api/vocab/reference/catalogs/{catalog}?q=关键词&limit=50`：搜索并标记是否已在对应活跃字典。
- `POST /api/vocab/reference/catalogs/{catalog}/activate`：提交 `{"codes":["编码"]}`，最多 200 条。
- `POST /api/vocab/reference/catalogs/{catalog}/import`：multipart 文件导入，包含 `file/version/source/mode/dry_run`。

catalog 可为 `exam/lab/medication/surgery`。原疾病 `/api/vocab/reference/icd10` 接口不变。

## 初始子集出处

- 检查：[LOINC 心电图报告](https://loinc.org/11524-6)、[DICOM 收录的 LOINC 检查报告代码](https://dicom.nema.org/MEDICAL/dicom/2017c/output/chtml/part16/chapter_H.html)。
- 检验：[LOINC 血红蛋白](https://loinc.org/718-7)、[LOINC 白细胞自动计数](https://loinc.org/6690-2)、[AHRQ 检验数据说明](https://hcup-us.ahrq.gov/datainnovations/clinicalcontentenhancementtoolkit/hi26.pdf)。
- 药品：[ATC 乙酰水杨酸抗血小板分类](https://atcddd.fhi.no/atc_ddd_index/?code=B01AC06&showdescription=no)、[ATC 阿托伐他汀](https://atcddd.fhi.no/atc_ddd_index/?code=C10AA05&showdescription=no)。
- 手术：[CMS 腹腔镜胆囊切除术历史资料](https://www.cms.gov/Regulations-and-Guidance/Guidance/Transmittals/Downloads/R127CIM.pdf)、[AHRQ 历史手术编码表](https://www.ncbi.nlm.nih.gov/books/NBK43845/table/tr-psi-tu80/)。
