"""Bound extraction requests without losing page/line provenance."""
def bounded_chunks(chunks, max_chars=2400):
    result=[]
    for chunk in chunks:
        if len(chunk.get('text','')) <= max_chars:
            result.append(chunk); continue
        lines=chunk['text'].split('\n')
        pages=chunk.get('page_numbers') or [chunk['page_start']]*len(lines)
        numbers=chunk.get('line_numbers') or list(range(chunk['line_start'],chunk['line_start']+len(lines)))
        groups=[]; group=[]; size=0
        for i,line in enumerate(lines):
            if group and size+len(line)+1>max_chars:
                groups.append(group);group=[];size=0
            group.append(i);size+=len(line)+1
        if group: groups.append(group)
        for index,group in enumerate(groups,1):
            first,last=group[0],group[-1]
            result.append({**chunk,'chunk_id':f"{chunk['chunk_id']}_s{index}",
                           'parent_chunk_id':chunk['chunk_id'],'page_start':pages[first],'page_end':pages[last],
                           'line_start':numbers[first],'line_end':numbers[last],
                           'page_numbers':[pages[i] for i in group],'line_numbers':[numbers[i] for i in group],
                           'text':'\n'.join(lines[i] for i in group)})
    return result
