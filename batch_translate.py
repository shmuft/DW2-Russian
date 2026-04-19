#!/usr/bin/env python3
"""
Batch translation script for DW2 XML files.

Recursively scans the English directory for XML files and translates them to Russian
using the existing translate.py script.
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path
from translate import build_translation_cache_from_paired_files, set_translation_cache, translate_text

def translate_file(input_path: Path, output_path: Path, words_mode: bool = False, check_mode: bool = False, pool_addresses=None, pool_timeout: int = 120, cache_file: str = None, fix_untranslated: bool = False) -> int:
    """
    Run translate.py for a single XML or TXT file.

    Returns:
        0 if successful,
        1 if untranslated English words were found in check mode,
        2 if another error occurred.
    """
    file_type = 'txt' if input_path.suffix.lower() == '.txt' else 'xml'
    cmd = [sys.executable, 'translate.py', '--type', file_type]
    if check_mode:
        cmd.extend([str(output_path), '--check'])
    else:
        cmd.extend([str(input_path), str(output_path)])
        if words_mode:
            cmd.append('--words')
        if fix_untranslated and file_type == 'xml':
            cmd.append('--fix-untranslated')
        if pool_addresses:
            for addr in pool_addresses:
                cmd.extend(['--pool', addr])
            if pool_timeout is not None:
                cmd.extend(['--pool-timeout', str(pool_timeout)])

    # Передаём файл кэша через переменную окружения
    env = os.environ.copy()
    if cache_file:
        env['DW2_TRANSLATION_CACHE'] = cache_file

    try:
        subprocess.run(cmd, check=True, env=env)
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


def fix_untranslated_files(xml_files, english_dir, russian_dir, pool_addresses, pool_timeout):
    """
    Найти и доперевести недопереведённые элементы в уже переведённых XML файлах.
    TXT файлы пропускаются.
    """
    from translate import check_translated_file
    
    fixed_count = 0
    total_problems = 0
    
    for xml_file in xml_files:
        if xml_file.suffix.lower() != '.xml':
            continue  # Пропускаем не XML файлы
        rel_path = xml_file.relative_to(english_dir)
        output_file = russian_dir / rel_path
        
        if not output_file.exists():
            continue
            
        # Проверяем файл на непереведённые элементы
        result = check_translated_file(str(output_file))
        if result == 1:  # Есть непереведённые элементы
            print(f"\nНайдены недопереведённые элементы в {output_file}")
            total_problems += 1
            
            # Запускаем доперевод
            print(f"Доперевод {xml_file} -> {output_file}")
            result_code = translate_file(
                xml_file,
                output_file,
                words_mode=False,
                check_mode=False,
                pool_addresses=pool_addresses,
                pool_timeout=pool_timeout,
                cache_file=None,  # Не используем кэш для доперевода
                fix_untranslated=True
            )
            
            if result_code == 0:
                fixed_count += 1
                print(f"Доперевод завершён: {output_file}")
            else:
                print(f"Ошибка доперевода: {output_file}")
    
    print(f"\nРезультат доперевода:")
    print(f"Файлов с проблемами: {total_problems}")
    print(f"Успешно допереведено: {fixed_count}")
    return 0 if fixed_count == total_problems else 1


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
    parser.add_argument(
        '--fix-untranslated',
        action='store_true',
        help='Найти и доперевести недопереведённые элементы в уже переведённых файлах'
    )

    args = parser.parse_args()

    if args.words and args.check:
        parser.error('--words и --check нельзя использовать одновременно')
    
    if args.fix_untranslated and (args.words or args.check or args.force):
        parser.error('--fix-untranslated нельзя использовать с --words, --check или --force')

    pool_addresses = []
    if args.pool:
        for chunk in args.pool:
            pool_addresses.extend([addr.strip() for addr in chunk.split(',') if addr.strip()])

    english_dir = Path('./1.3.4.3/English')
    russian_dir = Path('./1.3.4.3/Russian')

    if not english_dir.exists():
        print(f"Error: English directory '{english_dir}' does not exist")
        return 1

    # Загружаем кэш переводов из уже переведённых файлов
    import pickle
    cache_file = '.translation_cache.pkl'
    
    if russian_dir.exists() and not args.check and not args.words and not args.fix_untranslated:
        print("\n[INFO] Построение кэша переводов из уже переведённых файлов...")
        print("[INFO] Пропускаем файлы в процессе перевода...")
        
        # Найдём все файлы на паузе
        files_in_progress = set()
        for xml_file in english_dir.rglob('*.xml'):
            rel_path = xml_file.relative_to(english_dir)
            output_file = russian_dir / rel_path
            progress_file = f"{output_file}.progress.pkl"
            if os.path.exists(progress_file):
                files_in_progress.add(str(output_file))
                print(f"[INFO] Пропускаю файл на паузе: {output_file}")
        for txt_file in english_dir.rglob('*.txt'):
            rel_path = txt_file.relative_to(english_dir)
            output_file = russian_dir / rel_path
            progress_file = f"{output_file}.progress.pkl"
            if os.path.exists(progress_file):
                files_in_progress.add(str(output_file))
                print(f"[INFO] Пропускаю файл на паузе: {output_file}")
        
        cache = build_translation_cache_from_paired_files(str(english_dir), str(russian_dir), exclude_files=files_in_progress)
        
        if cache:
            # Сохраняем кэш в файл для передачи в translate.py
            try:
                with open(cache_file, 'wb') as f:
                    pickle.dump(cache, f)
                print(f"[INFO] Кэш сохранён ({len(cache)} записей)")
            except Exception as e:
                print(f"[WARNING] Ошибка сохранения кэша: {e}")
                cache_file = None
        else:
            cache_file = None
    else:
        cache_file = None

    # Find all XML and TXT files recursively
    xml_files = list(english_dir.rglob('*.xml'))
    txt_files = list(english_dir.rglob('*.txt'))
    all_files = xml_files + txt_files

    if args.fix_untranslated:
        print(f"Поиск недопереведённых элементов в {len(all_files)} файлах...")
        return fix_untranslated_files(all_files, english_dir, russian_dir, pool_addresses, args.pool_timeout)

    print(f"Found {len(xml_files)} XML files and {len(txt_files)} TXT files to process")

    success_count = 0
    skip_count = 0

    # If not --all, translate only one eligible file and exit.
    for file_path in all_files:
        untranslated_found = False
        # Compute relative path from English directory
        rel_path = file_path.relative_to(english_dir)
        
        # Special handling for Galactopedia: translate filenames
        if 'Galactopedia' in str(rel_path):
            # Translate the filename (stem only)
            env = os.environ.copy()
            if cache_file:
                print(f"transl cache {cache_file}")
                env['DW2_TRANSLATION_CACHE'] = cache_file
            original_stem = rel_path.stem + rel_path.suffix
            print(f"original_stem={original_stem}")
            print(f"rel_path={rel_path}")
            print("1")
            translated_stem = translate_text(original_stem)
            print("2")
            new_filename = translated_stem
            rel_path = rel_path.with_name(new_filename)
        
        output_file = russian_dir / rel_path

        if args.check:
            check_file = output_file
            if 'Galactopedia' in str(rel_path) and not output_file.exists():
                # Try with original filename for check
                original_rel_path = file_path.relative_to(english_dir)
                check_file = russian_dir / original_rel_path
            if not check_file.exists():
                print(f"Skipping проверку {file_path}: отсутствует файл перевода {check_file}")
                skip_count += 1
                continue
        else:
            # Проверяем, есть ли файл на паузе
            progress_file = f"{output_file}.progress.pkl"
            is_paused = os.path.exists(progress_file)
            
            # Пропускаем только если файл ПОЛНОСТЬЮ перевёден и это не на паузе
            if output_file.exists() and not args.force and not is_paused:
                print(f"Skipping {file_path} (output exists: {output_file})")
                skip_count += 1
                continue
            
            # Если файл на паузе, продолжаем перевод (не пропускаем)
            if is_paused:
                print(f"Resuming paused translation: {file_path}")

            # Ensure output directory exists
            output_file.parent.mkdir(parents=True, exist_ok=True)

        action = 'Проверка' if args.check else 'Translating'
        print(f"{action} {file_path} -> {output_file}")

        result_code = translate_file(
        file_path,
        output_file,
        words_mode=args.words,
        check_mode=args.check,
        pool_addresses=pool_addresses,
        pool_timeout=args.pool_timeout,
        cache_file=cache_file,
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

    # Очищаем файл кэша
    if cache_file and os.path.exists(cache_file):
        try:
            os.remove(cache_file)
        except Exception as e:
            print(f"[WARNING] Ошибка удаления кэша: {e}")

    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())