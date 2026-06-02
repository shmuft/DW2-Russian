#!/usr/bin/env python3
"""
Find all elements with unmatched braces.
"""

import xml.etree.ElementTree as ET
from pathlib import Path


def find_unmatched(element, path: str = "") -> list:
    """
    Find all text nodes with unmatched braces.
    """
    issues = []
    current_path = f"{path}/{element.tag}"
    
    if element.text:
        opens = element.text.count('{')
        closes = element.text.count('}')
        if opens != closes:
            issues.append({
                'path': current_path,
                'text': element.text,
                'opens': opens,
                'closes': closes,
                'type': 'text'
            })
    
    if element.tail:
        opens = element.tail.count('{')
        closes = element.tail.count('}')
        if opens != closes:
            issues.append({
                'path': current_path + "[tail]",
                'text': element.tail,
                'opens': opens,
                'closes': closes,
                'type': 'tail'
            })
    
    for child in element:
        child_issues = find_unmatched(child, current_path)
        issues.extend(child_issues)
    
    return issues


def main():
    russian_dir = Path('./1.3.4.3/Russian')
    
    all_unmatched = []
    
    for xml_file in sorted(russian_dir.rglob('*.xml')):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            issues = find_unmatched(root)
            
            for issue in issues:
                rel_path = xml_file.relative_to(russian_dir)
                issue['file'] = rel_path
                all_unmatched.append(issue)
        
        except ET.ParseError as e:
            print(f"Parse error: {xml_file}: {e}")
    
    if all_unmatched:
        print(f"Found {len(all_unmatched)} elements with unmatched braces:\n")
        for i, item in enumerate(all_unmatched):
            print(f"{i+1}. File: {item['file']}")
            print(f"   Path: {item['path']}")
            print(f"   Opens: {item['opens']}, Closes: {item['closes']}")
            text_preview = item['text'].replace('\n', '\\n')[:120]
            print(f"   Text: {text_preview}")
            print()
    else:
        print("All braces are balanced!")


if __name__ == "__main__":
    main()
