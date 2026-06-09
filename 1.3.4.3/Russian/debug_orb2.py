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

def walk_and_print(elem, path=''):
    tag = get_tag(elem)
    cur = f'{path}/{tag}' if path else tag
    text = (elem.text or '').strip()
    kids = list(elem)
    if text and not kids:
        print(f'LEAF {cur}: "{text[:60]}"')
    for c in kids:
        walk_and_print(c, cur)

en = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\English\DW2\OrbTypes.xml')
ru = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\Russian\DW2\OrbTypes.xml')

# Check if there are any string elements with English text
print("=== EN string elements with English text ===")
en_texts = []
def collect_strings(elem, path=''):
    tag = get_tag(elem)
    cur = f'{path}/{tag}' if path else tag
    text = (elem.text or '').strip()
    kids = list(elem)
    if text and not kids and tag in ('string',):
        en_texts.append((cur, text))
    for c in kids:
        collect_strings(c, cur)

collect_strings(en.getroot())
ru_texts = {}
def collect_ru_strings(elem, path=''):
    tag = get_tag(elem)
    cur = f'{path}/{tag}' if path else tag
    text = (elem.text or '').strip()
    kids = list(elem)
    if text and not kids and tag in ('string',):
        ru_texts[cur] = text
    for c in kids:
        collect_ru_strings(c, cur)

collect_ru_strings(ru.getroot())

print(f'EN strings: {len(en_texts)}')
print(f'RU strings: {len(ru_texts)}')

# Find untranslated
for path, en_t in en_texts:
    ru_t = ru_texts.get(path)
    if ru_t and en_t == ru_t:
        translatable = looks_translatable(en_t)
        print(f'  MATCH [{path}] = "{en_t[:50]}" translatable={translatable}')
    elif ru_t and en_t != ru_t:
        print(f'  DIFF  [{path}] EN="{en_t[:40]}" RU="{ru_t[:40]}"')

print(f'\nTotal string elements with matching EN/RU:')
count = 0
for path, en_t in en_texts:
    ru_t = ru_texts.get(path)
    if ru_t and en_t == ru_t and looks_translatable(en_t):
        count += 1
        print(f'  "{en_t[:50]}"')
        if count >= 20:
            break
print(f'... total {count} (but we limited to 20)')
