#!/usr/bin/env python3
"""
Deep scan for all brace and bracket patterns.
"""

import xml.etree.ElementTree as ET
from pathlib import Path


def scan_all_text(element, path: str = "") -> list:
    """
    Scan all text in the document.
    """
    results = []
    current_path = f"{path}/{element.tag}"
    
    if element.text:
        results.append({
            'path': current_path,
            'text': element.text,
            'type': 'text'
        })
    
    if element.tail:
        results.append({
            'path': current_path + "[tail]",
            'text': element.tail,
            'type': 'tail'
        })
    
    for child in element:
        child_results = scan_all_text(child, current_path)
        results.extend(child_results)
    
    return results


def main():
    russian_dir = Path('./1.3.4.3/Russian')
    
    all_with_braces = []
    
    for xml_file in sorted(russian_dir.rglob('*.xml')):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            all_text = scan_all_text(root)
            
            for item in all_text:
                if '{' in item['text'] or '}' in item['text']:
                    rel_path = xml_file.relative_to(russian_dir)
                    all_with_braces.append({
                        'file': rel_path,
                        'path': item['path'],
                        'type': item['type'],
                        'text': item['text']
                    })
        
        except ET.ParseError as e:
            print(f"Parse error: {xml_file}: {e}")
    
    if all_with_braces:
        print(f"Found {len(all_with_braces)} text nodes with braces:\n")
        for i, item in enumerate(all_with_braces[:50]):  # Show first 50
            print(f"{i+1}. File: {item['file']}")
            print(f"   Path: {item['path']}")
            print(f"   Text: {item['text'][:120]}")
            print()
    else:
        print("No braces found in any Russian XML files!")


if __name__ == "__main__":
    main()
