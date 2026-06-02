#!/usr/bin/env python3
"""
Find suspicious brace patterns that could cause FormatException.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
import re


def find_suspicious_braces(text: str):
    """
    Find patterns that could cause format string issues:
    - Single braces without pairs
    - Empty braces
    - Mismatched braces
    """
    issues = []
    
    # Find all brace patterns
    patterns = [
        (r'\{(?![\d\]])', 'Possible unmatched opening brace'),
        (r'(?<![}0-9\[])\}', 'Possible unmatched closing brace'),
        (r'\{\}', 'Empty braces'),
    ]
    
    for pattern, desc in patterns:
        matches = list(re.finditer(pattern, text))
        if matches:
            for match in matches:
                issues.append({
                    'pattern': desc,
                    'position': match.start(),
                    'match': match.group()
                })
    
    return issues


def scan_russian_xml_files():
    """
    Scan Russian XML files for suspicious brace patterns.
    """
    russian_dir = Path('./1.3.4.3/Russian')
    
    total_issues = []
    
    for xml_file in sorted(russian_dir.rglob('*.xml')):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            # Iterate through all elements
            for elem in root.iter():
                for attr_name, attr_value in elem.attrib.items():
                    issues = find_suspicious_braces(attr_value)
                    if issues:
                        rel_path = xml_file.relative_to(russian_dir)
                        for issue in issues:
                            total_issues.append({
                                'file': rel_path,
                                'tag': elem.tag,
                                'type': f'attribute {attr_name}',
                                'text': attr_value,
                                'issue': issue
                            })
                
                if elem.text and elem.text.strip():
                    issues = find_suspicious_braces(elem.text)
                    if issues:
                        rel_path = xml_file.relative_to(russian_dir)
                        for issue in issues:
                            total_issues.append({
                                'file': rel_path,
                                'tag': elem.tag,
                                'type': 'text content',
                                'text': elem.text.strip(),
                                'issue': issue
                            })
        
        except ET.ParseError as e:
            print(f"Parse error: {xml_file}: {e}")
    
    # Display results
    if total_issues:
        current_file = None
        for issue in sorted(total_issues, key=lambda x: str(x['file'])):
            if issue['file'] != current_file:
                current_file = issue['file']
                print(f"\n{'='*80}")
                print(f"File: {current_file}")
                print('='*80)
            
            print(f"\nTag: {issue['tag']} ({issue['type']})")
            print(f"Pattern: {issue['issue']['pattern']}")
            print(f"Position: {issue['issue']['position']}")
            print(f"Text: {issue['text'][:100]}...")
    else:
        print("No suspicious brace patterns found!")


if __name__ == "__main__":
    scan_russian_xml_files()
