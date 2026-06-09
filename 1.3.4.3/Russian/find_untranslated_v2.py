import os
import re
import xml.etree.ElementTree as ET

BASE = r"C:\Users\Ivan\projects\DW2-Russian\1.3.4.3"
RUSSIAN_DIR = os.path.join(BASE, "Russian")
ENGLISH_DIR = os.path.join(BASE, "English")
OUTPUT_FILE = os.path.join(RUSSIAN_DIR, "untranslated_report.txt")

# Tags that should contain user-facing text
TEXT_TAGS = {
    "Name", "Description", "Title", "Subtitle", "Summary", "Text", "Message",
    "Header", "Body", "Label", "Quote", "string",
    "CabinetTitle", "CabinetSubtitle",
    "GeneratedItemName", "ActionLocationItemName",
}

def get_tag(elem):
    tag = elem.tag
    if '}' in tag:
        tag = tag.split('}', 1)[1]
    return tag

def has_cyrillic(text):
    return bool(re.search(r'[а-яА-ЯёЁ]', text))

def looks_translatable(text):
    """Check if text is English words (not a path/id/number)."""
    t = text.strip()
    if not t: return False
    if re.match(r'^[+-]?\d+(\.\d+)?$', t): return False
    if t.lower() in ('true', 'false'): return False
    if '/' in t or '\\' in t: return False
    if has_cyrillic(t): return False
    if not re.search(r'[a-zA-Z]{3,}', t): return False
    return True

def compare(en_elem, ru_elem, path="", parent_tag=""):
    """Compare two XML trees element-by-element in order, returning untranslated paths."""
    results = []
    
    en_tag = get_tag(en_elem)
    ru_tag = get_tag(ru_elem)
    
    if en_tag != ru_tag:
        return results  # Structure mismatch; can't reliably compare
    
    current_path = f"{path}/{en_tag}" if path else en_tag
    
    en_children = list(en_elem)
    ru_children = list(ru_elem)
    
    en_text = (en_elem.text or "").strip()
    ru_text = (ru_elem.text or "").strip()
    
    if en_text and en_children:
        # Both text and children? Use children only
        pass
    elif en_text and not en_children:
        # Leaf node with text - compare
        tag_ok = en_tag in TEXT_TAGS
        # Skip string elements under file/resource parents
        if en_tag == "string" and ("Filename" in parent_tag or "File" in parent_tag or "Resource" in parent_tag or "ChildType" in parent_tag or "Model" in parent_tag):
            tag_ok = False
        # Skip Name under Bonuses/conditions etc.
        if en_tag == "Name" and parent_tag in ("Bonus", "GameEventAction", "GameEventCondition", "OrbTypeFactor",
                                                "StarProbability", "ResourcePrevalence"):
            tag_ok = False
        
        if tag_ok and looks_translatable(en_text):
            if ru_text and not has_cyrillic(ru_text) and looks_translatable(ru_text):
                # English in RU file too - untranslated
                results.append(current_path)
            elif not ru_text:
                pass  # empty Russian text - ignore
            # else has Cyrillic - translated
        
    # Compare children in order
    for i in range(max(len(en_children), len(ru_children))):
        if i < len(en_children) and i < len(ru_children):
            results.extend(compare(en_children[i], ru_children[i], current_path, en_tag))
        # else: structure mismatch, skip
    
    return results

def main():
    results = {}
    total = 0
    processed = 0
    
    for root_dir, dirs, files in os.walk(RUSSIAN_DIR):
        for filename in sorted(files):
            if not filename.endswith('.xml'):
                continue
            
            ru_file = os.path.join(root_dir, filename)
            rel = os.path.relpath(ru_file, RUSSIAN_DIR)
            en_file = os.path.join(ENGLISH_DIR, rel)
            
            if not os.path.exists(en_file):
                continue
            
            processed += 1
            
            try:
                en_tree = ET.parse(en_file)
                ru_tree = ET.parse(ru_file)
                en_root = en_tree.getroot()
                ru_root = ru_tree.getroot()
            except Exception as e:
                print(f"  ERROR {filename}: {e}")
                continue
            
            untranslated = compare(en_root, ru_root)
            
            if untranslated:
                results[filename] = untranslated
                total += len(untranslated)
                print(f"  {filename}: {len(untranslated)}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for filename in sorted(results.keys()):
            for path in results[filename]:
                f.write(f"Имя файла: {filename}\n")
                f.write(f"Путь до недопереведённого тега: {path}\n\n")
    
    print(f"\n{'='*60}")
    print(f"Processed: {processed} files")
    print(f"Files with issues: {len(results)}")
    print(f"Total untranslated: {total}")
    print(f"Report: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
