#!/usr/bin/env python3
"""
Find all strings containing format placeholders {0}, {1}, etc.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
import re


def find_format_strings(element, path: str = "") -> list:
    """
    Find all text nodes containing format placeholders like {0}, {1}, etc.
    """
    results = []
    current_path = f"{path}/{element.tag}"
    
    if element.text and element.text.strip():
        if '{' in element.text and '}' in element.text:
            results.append({
                'path': current_path,
                'type': 'text',
                'content': element.text.strip(),
                'file_context': element
            })
    
    if element.tail and element.tail.strip():
        if '{' in element.tail and '}' in element.tail:
            results.append({
                'path': current_path + "[tail]",
                'type': 'tail',
                'content': element.tail.strip(),
                'file_context': element
            })
    
    for child in element:
        child_results = find_format_strings(child, current_path)
        results.extend(child_results)
    
    return results


def compare_files(english_file: Path, russian_file: Path):
    """
    Compare format strings between English and Russian versions.
    """
    try:
        eng_tree = ET.parse(english_file)
        rus_tree = ET.parse(russian_file)
    except ET.ParseError as e:
        return []
    
    eng_root = eng_tree.getroot()
    rus_root = rus_tree.getroot()
    
    eng_strings = find_format_strings(eng_root)
    rus_strings = find_format_strings(rus_root)
    
    issues = []
    
    for eng in eng_strings:
        # Find corresponding Russian string
        matching = [r for r in rus_strings if r['path'] == eng['path']]
        
        if matching:
            rus = matching[0]
            eng_text = eng['content']
            rus_text = rus['content']
            
            # Count braces
            eng_open = eng_text.count('{')
            eng_close = eng_text.count('}')
            rus_open = rus_text.count('{')
            rus_close = rus_text.count('}')
            
            if rus_open != rus_close:
                issues.append({
                    'path': eng['path'],
                    'english': eng_text,
                    'russian': rus_text,
                    'issue': f"Russian has {rus_open} opening and {rus_close} closing braces"
                })
    
    return issues


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Find format string issues")
    parser.add_argument("--file", help="Specific file to check")
    parser.add_argument("--all", action="store_true", help="Check all files")
    
    args = parser.parse_args()
    
    english_dir = Path('./1.3.4.3/English')
    russian_dir = Path('./1.3.4.3/Russian')
    
    if args.file:
        matching = list(english_dir.rglob(args.file))
        if matching:
            eng_file = matching[0]
            rus_file = russian_dir / eng_file.relative_to(english_dir)
            
            issues = compare_files(eng_file, rus_file)
            
            if issues:
                print(f"Issues in {args.file}:")
                for issue in issues:
                    print(f"\n  Path: {issue['path']}")
                    print(f"  Issue: {issue['issue']}")
                    print(f"  English: {issue['english']}")
                    print(f"  Russian: {issue['russian']}")
            else:
                print(f"No issues in {args.file}")
    
    elif args.all:
        all_issues = []
        
        for eng_file in sorted(english_dir.rglob('*.xml')):
            rus_file = russian_dir / eng_file.relative_to(english_dir)
            issues = compare_files(eng_file, rus_file)
            
            if issues:
                rel_path = eng_file.relative_to(english_dir)
                for issue in issues:
                    issue['file'] = rel_path
                    all_issues.append(issue)
        
        if all_issues:
            for issue in all_issues:
                print(f"\nFile: {issue['file']}")
                print(f"Path: {issue['path']}")
                print(f"Issue: {issue['issue']}")
                print(f"English: {issue['english']}")
                print(f"Russian: {issue['russian']}")
        else:
            print("No format string issues found!")


if __name__ == "__main__":
    main()
