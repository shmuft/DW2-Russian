import re, xml.etree.ElementTree as ET

def get_tag(elem):
    tag = elem.tag
    if '}' in tag: tag = tag.split('}', 1)[1]
    return tag

def has_cyrillic(text):
    return bool(re.search(r'[а-яА-ЯёЁ]', text))

def looks_translatable(text):
    t = text.strip()
    if not t: return False
    if re.match(r'^[+-]?\d+(\.\d+)?$', t): return False
    if t.lower() in ('true', 'false'): return False
    if '/' in t or '\\' in t: return False
    if has_cyrillic(t): return False
    if not re.search(r'[a-zA-Z]{3,}', t): return False
    return True

def compare(en_elem, ru_elem, path='', parent_tag=''):
    TEXT_TAGS = {'Name', 'Description', 'Title', 'Subtitle', 'Summary', 'Text', 'Message',
        'Header', 'Body', 'Label', 'Quote', 'string',
        'CabinetTitle', 'CabinetSubtitle',
        'GeneratedItemName', 'ActionLocationItemName'}
    
    results = []
    en_tag = get_tag(en_elem)
    ru_tag = get_tag(ru_elem)
    if en_tag != ru_tag: return results
    
    current_path = f'{path}/{en_tag}' if path else en_tag
    en_children = list(en_elem)
    ru_children = list(ru_elem)
    en_text = (en_elem.text or '').strip()
    ru_text = (ru_elem.text or '').strip()
    
    if en_text and not en_children:
        tag_ok = en_tag in TEXT_TAGS
        if en_tag == 'string' and ('Filename' in parent_tag or 'File' in parent_tag or 'Resource' in parent_tag or 'ChildType' in parent_tag or 'Model' in parent_tag):
            tag_ok = False
        if en_tag == 'Name' and parent_tag in ('Bonus', 'GameEventAction', 'GameEventCondition', 'OrbTypeFactor', 'StarProbability', 'ResourcePrevalence'):
            tag_ok = False
        
        if tag_ok and looks_translatable(en_text):
            if ru_text and not has_cyrillic(ru_text) and looks_translatable(ru_text):
                results.append((current_path, en_text))
                print(f"FOUND: {current_path} = \"{en_text}\"")
    
    for i in range(max(len(en_children), len(ru_children))):
        if i < len(en_children) and i < len(ru_children):
            results.extend(compare(en_children[i], ru_children[i], current_path, en_tag))
    
    return results

en = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\English\DW2\OrbTypes.xml')
ru = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\Russian\DW2\OrbTypes.xml')

result = compare(en.getroot(), ru.getroot())
print(f"\n=== Total: {len(result)} ===")
