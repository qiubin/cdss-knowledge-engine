import json
p=json.load(open(r'data\uploads\DOC-ead50a9c.parsed.json',encoding='utf-8'))
for c in p['parsed']['chunks']:
    print(c['chunk_id'], '|', c['section'][:80], '| pages', c['page_start'], '-', c['page_end'], '| lines', c['line_start'], '-', c['line_end'], '| chars', len(c['text']))
    for t in ['淋巴瘤','霍奇金','弥漫大B','发热','盗汗','淋巴结肿大','LDH','PET-CT','骨髓']:
        print(t, c['text'].lower().count(t.lower()))
