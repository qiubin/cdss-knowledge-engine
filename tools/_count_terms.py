import json,re
p=json.load(open(r'data\uploads\DOC-ead50a9c.parsed.json',encoding='utf-8'))
text='\n'.join(x['text'] for x in p['parsed']['lines'])
terms=['淋巴瘤','霍奇金','非霍奇金','弥漫大B','DLBCL','滤泡性淋巴瘤','边缘区淋巴瘤','MALT','套细胞淋巴瘤','Burkitt','伯基特','小淋巴细胞淋巴瘤','慢性淋巴细胞白血病','无痛性','淋巴结肿大','发热','盗汗','体重下降','瘙痒','脾大','PET-CT','PET/CT','CT','MRI','超声','骨髓穿刺','骨髓活检','病理活检','免疫组化','流式细胞术','FISH','PCR','血常规','LDH','乳酸脱氢酶','β2微球蛋白','白蛋白','血沉','乙肝','乙型肝炎','HIV','EBV']
res={t:len(re.findall(t,text,flags=re.I)) for t in terms}
open(r'tools\lymphoma_term_counts.json','w',encoding='utf-8').write(json.dumps(res,ensure_ascii=False,indent=2))
