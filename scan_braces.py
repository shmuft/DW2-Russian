#!/usr/bin/env python3
"""
Deep scan for format string issues in Russian XML files.
"""

import xml.etree.ElementTree as ET
from pathlib import Path


def find_brace_issues(element, path: str = "") -> list:
    """
    Recursively find all text nodes with brace issues.
    
    Returns list of (xpath, text, issue_description) tuples.
    """
    issues = []
    current_path = f"{path}/{element.tag}"
    
    if element.text and element.text.strip():
        open_count = element.text.count('{')
        close_count = element.text.count('}')
        
        if open_count != close_count:
            issues.append((
                current_path,
                element.text.strip(),
                f"Unmatched braces: {open_count} open, {close_count} close"
            ))
        else:
            # Check order
            depth = 0
            for i, char in enumerate(element.text):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                if depth < 0:
                    issues.append((
                        current_path,
                        element.text.strip(),
                        f"Unexpected closing brace at position {i}"
                    ))
                    break
    
    # Check tail text
    if element.tail and element.tail.strip():
        open_count = element.tail.count('{')
        close_count = element.tail.count('}')
        
        if open_count != close_count:
            issues.append((
                current_path + "[tail]",
                element.tail.strip(),
                f"Unmatched braces in tail: {open_count} open, {close_count} close"
            ))
    
    # Recurse through children
    for child in element:
        child_issues = find_brace_issues(child, current_path)
        issues.extend(child_issues)
    
    return issues


def scan_russian_files():
    """
    Scan all Russian XML files for brace issues.
    """
    russian_dir = Path('./1.3.4.3/Russian')
    xml_files = list(russian_dir.rglob('*.xml'))
    
    total_issues = 0
    
    for xml_file in sorted(xml_files):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            issues = find_brace_issues(root)
            
            if issues:
                rel_path = xml_file.relative_to(russian_dir)
                print(f"\nFile: {rel_path}")
                print(f"Issues found: {len(issues)}")
                
                for xpath, text, description in issues:
                    print(f"\n  Path: {xpath}")
                    print(f"  Issue: {description}")
                    print(f"  Text: {text[:100]}..." if len(text) > 100 else f"  Text: {text}")
                    total_issues += 1
                    
        except ET.ParseError as e:
            print(f"Parse error in {xml_file}: {e}")
    
    if total_issues == 0:
        print("No brace issues found in any Russian XML files!")
    else:
        print(f"\n\nTotal issues: {total_issues}")


if __name__ == "__main__":
    scan_russian_files()
