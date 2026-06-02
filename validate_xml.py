#!/usr/bin/env python3
"""
Validate translated XML files against English originals.

Checks:
- Tag structure and order
- Unmatched braces in text content (format string issues)
- Missing or extra tags
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple


def check_braces(text: str) -> Tuple[bool, str]:
    """
    Check for balanced braces in text (format string compatibility).
    
    Returns:
        (is_valid, error_message)
    """
    open_count = text.count('{')
    close_count = text.count('}')
    
    if open_count != close_count:
        return False, f"Unmatched braces: {open_count} open, {close_count} close"
    
    # Check that they're in the right order
    depth = 0
    for i, char in enumerate(text):
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
        if depth < 0:
            return False, f"Unexpected closing brace at position {i}"
    
    return True, ""


def get_element_structure(element, path: str = "") -> dict:
    """
    Get the structure of an XML element (tag names and order).
    """
    current_path = f"{path}/{element.tag}"
    structure = {
        "tag": element.tag,
        "path": current_path,
        "children": []
    }
    
    for child in element:
        structure["children"].append(get_element_structure(child, current_path))
    
    return structure


def compare_structures(eng_elem, rus_elem, path: str = "") -> List[str]:
    """
    Compare English and Russian element structures.
    
    Returns list of differences.
    """
    issues = []
    
    if eng_elem.tag != rus_elem.tag:
        issues.append(f"Tag mismatch at {path}: {eng_elem.tag} vs {rus_elem.tag}")
        return issues
    
    current_path = f"{path}/{eng_elem.tag}"
    
    # Compare text content
    if eng_elem.text and eng_elem.text.strip():
        if not rus_elem.text or not rus_elem.text.strip():
            issues.append(f"Missing text in {current_path}")
        else:
            # Check for brace issues in Russian translation
            is_valid, error_msg = check_braces(rus_elem.text)
            if not is_valid:
                issues.append(f"Format string error in {rus_elem.tag} at {current_path}: {error_msg}")
                issues.append(f"  English: {eng_elem.text.strip()}")
                issues.append(f"  Russian: {rus_elem.text.strip()}")
    
    # Compare children tags
    eng_children = list(eng_elem)
    rus_children = list(rus_elem)
    
    if len(eng_children) != len(rus_children):
        issues.append(f"Child count mismatch at {current_path}: {len(eng_children)} vs {len(rus_children)}")
    
    # Compare each child
    for i, (eng_child, rus_child) in enumerate(zip(eng_children, rus_children)):
        child_issues = compare_structures(eng_child, rus_child, current_path)
        issues.extend(child_issues)
    
    return issues


def validate_file(english_path: Path, russian_path: Path) -> List[str]:
    """
    Validate a Russian XML file against its English original.
    
    Returns list of issues found.
    """
    issues = []
    
    if not english_path.exists():
        return [f"English file not found: {english_path}"]
    
    if not russian_path.exists():
        return [f"Russian file not found: {russian_path}"]
    
    try:
        eng_tree = ET.parse(english_path)
        rus_tree = ET.parse(russian_path)
    except ET.ParseError as e:
        return [f"XML parse error: {e}"]
    
    eng_root = eng_tree.getroot()
    rus_root = rus_tree.getroot()
    
    # Compare structures
    structure_issues = compare_structures(eng_root, rus_root)
    issues.extend(structure_issues)
    
    return issues


def main():
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(
        description="Validate translated XML files"
    )
    parser.add_argument(
        "--file",
        help="Specific XML filename to validate (e.g. ShipHulls_Atuuk.xml)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all files"
    )
    
    args = parser.parse_args()
    
    english_dir = Path('./1.3.4.3/English')
    russian_dir = Path('./1.3.4.3/Russian')
    
    if args.file:
        # Find the file in English directory
        matching_files = list(english_dir.rglob(args.file))
        
        if not matching_files:
            print(f"Error: File '{args.file}' not found in {english_dir}")
            return 1
        
        eng_file = matching_files[0]
        rel_path = eng_file.relative_to(english_dir)
        rus_file = russian_dir / rel_path
        
        print(f"Validating: {rel_path}")
        issues = validate_file(eng_file, rus_file)
        
        if issues:
            print(f"Issues found:")
            for issue in issues:
                print(f"  {issue}")
            return 1
        else:
            print("OK")
            return 0
    
    elif args.all:
        # Validate all files
        xml_files = list(english_dir.rglob('*.xml'))
        
        total_issues = 0
        files_with_issues = []
        
        for xml_file in sorted(xml_files):
            rel_path = xml_file.relative_to(english_dir)
            rus_file = russian_dir / rel_path
            
            issues = validate_file(xml_file, rus_file)
            
            if issues:
                files_with_issues.append((rel_path, issues))
                total_issues += len(issues)
        
        if files_with_issues:
            for rel_path, issues in files_with_issues:
                print(f"Issues in {rel_path}:")
                for issue in issues:
                    print(f"  {issue}")
                print()
        
        if total_issues == 0:
            print("All files validated successfully!")
            return 0
        else:
            print(f"Total issues found: {total_issues}")
            return 1
    
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
