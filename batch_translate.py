#!/usr/bin/env python3
"""
Batch translation script for DW2 XML files.

Recursively scans the English directory for XML files and translates them to Russian
using the existing translate.py script.
"""

import argparse
import subprocess
import sys
from pathlib import Path

def translate_file(input_path: Path, output_path: Path, words_mode: bool = False, check_mode: bool = False, pool_addresses=None, pool_timeout: int = 120) -> int:
    """
    Run translate.py for a single XML file.

    Returns:
        0 if successful,
        1 if untranslated English words were found in check mode,
        2 if another error occurred.
    """
    cmd = [sys.executable, 'translate.py']
    if check_mode:
        cmd.extend([str(output_path), '--check'])
    else:
        cmd.extend([str(input_path), str(output_path)])
        if words_mode:
            cmd.append('--words')
        if pool_addresses:
            for addr in pool_addresses:
                cmd.extend(['--pool', addr])
            if pool_timeout is not None:
                cmd.extend(['--pool-timeout', str(pool_timeout)])

    try:
        subprocess.run(cmd, check=True)
        return 0
    except subprocess.CalledProcessError as e:
        if check_mode and e.returncode == 1:
            print(f"Найдены английские слова в {output_path}")
            return 1
        print(f"Error processing {input_path if not check_mode else output_path}: {e}")
        return 2
    except Exception as e:
        print(f"Unexpected error processing {input_path if not check_mode else output_path}: {e}")
        return 2


def translate_file(input_path: Path, output_path: Path, words_mode: bool = False, check_mode: bool = False, pool_addresses=None, pool_timeout: int = 120) -> int:
    """
    Run translate.py for a single XML file.

    Returns:
        0 if successful,
        1 if untranslated English words were found in check mode,
        2 if another error occurred.
    """
    cmd = [sys.executable, 'translate.py']
    if check_mode:
        cmd.extend([str(output_path), '--check'])
    else:
        cmd.extend([str(input_path), str(output_path)])
        if words_mode:
            cmd.append('--words')
        if pool_addresses:
            for addr in pool_addresses:
                cmd.extend(['--pool', addr])
            if pool_timeout is not None:
                cmd.extend(['--pool-timeout', str(pool_timeout)])

    try:
        subprocess.run(cmd, check=True)
        return 0
    except subprocess.CalledProcessError as e:
        if check_mode and e.returncode == 1:
            print(f"Найдены английские слова в {output_path}")
            return 1
        print(f"Error processing {input_path if not check_mode else output_path}: {e}")
        return 2
    except Exception as e:
        print(f"Unexpected error processing {input_path if not check_mode else output_path}: {e}")
        return 2


def main():
    parser = argparse.ArgumentParser(
        description="Batch translate XML files from English to Russian"
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force translation even if output file already exists'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Translate all eligible files (default: only first eligible file)'
    )
    parser.add_argument(
        '--words',
        action='store_true',
        help='Собрать список исходных фраз для словаря, не выполнять перевод'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Проверить уже переведённые файлы на наличие английских слов'
    )
    parser.add_argument(
        '--pool',
        action='append',
        help='Адреса пул воркеров (например: host:port или host:port,host2:port)'
    )
    parser.add_argument(
        '--pool-timeout',
        type=int,
        default=120,
        help='Таймаут запроса к пулу в секундах'
    )

    args = parser.parse_args()

    if args.words and args.check:
        parser.error('--words и --check нельзя использовать одновременно')

    pool_addresses = []
    if args.pool:
        for chunk in args.pool:
            pool_addresses.extend([addr.strip() for addr in chunk.split(',') if addr.strip()])

    english_dir = Path('./1.3.4.3/English')
    russian_dir = Path('./1.3.4.3/Russian')

    if not english_dir.exists():
        print(f"Error: English directory '{english_dir}' does not exist")
        return 1

    # Find all XML files recursively
    xml_files = list(english_dir.rglob('*.xml'))

    if not xml_files:
        print("No XML files found in the English directory")
        return 0

    print(f"Found {len(xml_files)} XML files to process")

    success_count = 0
    skip_count = 0

    # If not --all, translate only one eligible file and exit.
    for xml_file in xml_files:
        untranslated_found = False
        # Compute relative path from English directory
        rel_path = xml_file.relative_to(english_dir)
        output_file = russian_dir / rel_path

        if args.check:
            if not output_file.exists():
                print(f"Skipping проверку {xml_file}: отсутствует файл перевода {output_file}")
                skip_count += 1
                continue
        else:
            if output_file.exists() and not args.force:
                print(f"Skipping {xml_file} (output exists: {output_file})")
                skip_count += 1
                continue

            # Ensure output directory exists
            output_file.parent.mkdir(parents=True, exist_ok=True)

        action = 'Проверка' if args.check else 'Translating'
        print(f"{action} {xml_file} -> {output_file}")

        result_code = translate_file(
        xml_file,
        output_file,
        words_mode=args.words,
        check_mode=args.check,
        pool_addresses=pool_addresses,
        pool_timeout=args.pool_timeout,
    )
        if result_code == 0:
            success_count += 1
            print("OK")
        elif result_code == 1:
            untranslated_found = True
            print("FAIL")
        else:
            print("FAIL")
        
        if args.check and untranslated_found:
            input("Найдены непереведённые английские слова. Нажмите Enter для выхода...")

        if not args.all:
            print("Done single-file mode: exiting after first translation.")
            break
        

    print("\nBatch translation complete:")
    print(f"  Translated: {success_count}")
    print(f"  Skipped: {skip_count}")
    print(f"  Total files: {len(xml_files)}")

    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())