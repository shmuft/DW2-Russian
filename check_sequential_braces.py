#!/usr/bin/env python3
"""
Check for unbalanced braces that might span across elements.
"""

import xml.etree.ElementTree as ET
from pathlib import Path


def check_sequential_braces(root_elem) -> list:
    """
    Check if braces are balanced when processing text sequentially through the document.
    """
    issues = []
    depth = 0
    position = 0
    
    def traverse(elem):
        nonlocal depth, position, issues
        
        if elem.text:
            for i, char in enumerate(elem.text):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth < 0:
                        issues.append({
                            'element': elem.tag,
                            'position_in_doc': position + i,
                            'text_preview': elem.text[max(0, i-20):min(len(elem.text), i+20)],
                            'char_position_in_text': i,
                            'depth': depth
                        })
                position += 1
        
        for child in elem:
            traverse(child)
            if child.tail:
                for i, char in enumerate(child.tail):
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth < 0:
                            issues.append({
                                'element': child.tag + '[tail]',
                                'position_in_doc': position + i,
                                'text_preview': child.tail[max(0, i-20):min(len(child.tail), i+20)],
                                'char_position_in_text': i,
                                'depth': depth
                            })
                    position += 1
    
    traverse(root_elem)
    
    return issues, depth


def main():
    russian_dir = Path('./1.3.4.3/Russian')
    
    found_issues = False
    
    for xml_file in sorted(russian_dir.rglob('*.xml')):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            issues, final_depth = check_sequential_braces(root)
            
            if issues or final_depth != 0:
                if not found_issues:
                    found_issues = True
                
                rel_path = xml_file.relative_to(russian_dir)
                print(f"\nFile: {rel_path}")
                
                if final_depth != 0:
                    print(f"  FINAL DEPTH: {final_depth} (should be 0)")
                
                for issue in issues:
                    print(f"  Issue at position {issue['position_in_doc']} in {issue['element']}:")
                    print(f"    Text context: ...{issue['text_preview']}...")
        
        except ET.ParseError as e:
            print(f"Parse error: {xml_file}: {e}")
    
    if not found_issues:
        print("No sequential brace issues found!")


if __name__ == "__main__":
    main()
