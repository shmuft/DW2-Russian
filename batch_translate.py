#!/usr/bin/env python3
"""
Batch translation script for DW2 XML files.

Recursively scans the English directory for XML files and translates them to Russian
using the existing translate.py script.
"""

import argparse
import json
import shutil
import subprocess
import sys
import os
import pickle
import xml.etree.ElementTree as ET
from pathlib import Path
from translate import (
    _is_technical_string,
    _read_text_file_lines,
    build_translation_cache_from_paired_files,
    extract_translatable_texts,
    iter_translatable_elements,
    translate_text,
)
from rag.rag import (
    count_missing_embeddings,
    generate_missing_embeddings,
    load_translations_to_db,
    migrate_db,
    register_pending_translations,
    search_similar_translations,
    unload_all_models,
)

DEFAULT_TARGET_VERSION = '1.3.5.7'
DEFAULT_CACHE_VERSIONS = ('1.3.4.3',)
DEFAULT_CACHE_FILE_NAME = '.translation_cache.pkl'

XML_ENTITY_KEYS = {
    'GameEvent': 'Name',
    'TourItem': 'Title',
}

XML_LIST_ITEM_KEYS = {
    'Bonus': 'Type',
    'Component': 'ComponentId',
    'ComponentBay': 'ComponentBayId',
    'OrbTypeFactor': 'OrbTypeId',
    'RaceFactor': 'RaceId',
    'ResourceQuantity': 'ResourceId',
}


def get_galactopedia_translated_rel_path(rel_path: Path) -> Path:
    """Возвращает путь к русскому имени файла для Galactopedia, как это делает основной цикл перевода."""
    if 'Galactopedia' not in str(rel_path):
        return rel_path

    original_stem = rel_path.stem + rel_path.suffix
    translated_stem = translate_text(original_stem)
    return rel_path.with_name(translated_stem)


def resolve_russian_rel_path(english_rel_path: Path, russian_dir: Path) -> Path:
    """Находит корректный путь к русскому файлу, учитывая переведённые имена Galactopedia."""
    candidate = russian_dir / english_rel_path
    if candidate.exists():
        return english_rel_path

    translated_rel_path = get_galactopedia_translated_rel_path(english_rel_path)
    translated_candidate = russian_dir / translated_rel_path
    if translated_candidate.exists():
        return translated_rel_path

    return english_rel_path


def collect_pending_translation_texts(
    english_files: list[Path],
    english_dir: Path,
    cache_translations: dict[str, str],
) -> set[str]:
    """Возвращает английские тексты target-версии, отсутствующие в cache-from."""
    pending = set()
    cached_english = {
        text.strip()
        for text in cache_translations
        if text and text.strip()
    }

    for english_file in english_files:
        try:
            if english_file.suffix.lower() == '.xml':
                english_root = ET.parse(english_file).getroot()
                english_texts = extract_translatable_texts(english_root)
                for english_text, _ in english_texts:
                    if (english_text and english_text not in cached_english
                            and not _is_technical_string(english_text)):
                        pending.add(english_text)
            else:
                english_lines = _read_text_file_lines(english_file)
                for line in english_lines:
                    english_line = line.strip()
                    if not english_line:
                        continue
                    if ';' in english_line:
                        english_text = english_line.split(';', 1)[1].strip()
                    else:
                        english_text = english_line
                    if (english_text and english_text not in cached_english
                            and not _is_technical_string(english_text)):
                        pending.add(english_text)
        except Exception as exc:
            print(f"[WARNING] Не удалось просканировать {english_file}: {exc}")
    return pending


def has_untranslated_txt_content(english_file: Path, russian_file: Path) -> bool:
    """Проверяет TXT по ключам до ';', включая неполные и смещённые файлы."""
    if not russian_file.exists():
        return True

    try:
        english_lines = _read_text_file_lines(english_file)
        russian_lines = _read_text_file_lines(russian_file)
    except Exception as exc:
        print(f"[WARNING] Не удалось проверить перевод {russian_file}: {exc}")
        return True

    russian_by_key = {}
    for line in russian_lines:
        content = line.strip()
        if not content:
            continue
        if ';' in content:
            key, translated = content.split(';', 1)
            russian_by_key[key.strip()] = translated.strip()

    for line in english_lines:
        content = line.strip()
        if not content:
            continue
        if ';' in content:
            key, english_text = content.split(';', 1)
            english_text = english_text.strip()
            if not english_text or _is_technical_string(english_text):
                continue
            translated = russian_by_key.get(key.strip())
            if translated is None or translated == english_text:
                return True
        elif not _is_technical_string(content) and content not in russian_lines:
            return True

    return False


def _element_key(element: ET.Element) -> tuple[str, str] | None:
    """Return the patch key for an entity or keyed list item."""
    preferred_key = XML_ENTITY_KEYS.get(
        element.tag,
        XML_LIST_ITEM_KEYS.get(element.tag),
    )
    key_element = element.find(preferred_key) if preferred_key else None
    if key_element is None:
        possible_keys = (
            f'{element.tag}Id',
            f'{element.tag.removesuffix("Definition")}Id',
        )
        for candidate_name in possible_keys:
            key_element = element.find(candidate_name)
            if key_element is not None:
                break
    if key_element is None and element.tag == 'Bonus':
        key_element = element.find('Type')
    if key_element is None:
        return None

    key_value = (key_element.text or '').strip()
    if not key_value:
        return None
    return key_element.tag, key_value


def _is_key_element(parent: ET.Element, child: ET.Element) -> bool:
    key = _element_key(parent)
    return key is not None and child.tag == key[0]


def _children_are_list_items(parent: ET.Element, children: list[ET.Element]) -> bool:
    if parent.tag.startswith('ArrayOf'):
        return True
    if not children:
        return False
    if len(children) > 1 and all(child.tag == children[0].tag for child in children):
        return True
    if len(children) == 1:
        child = children[0]
        return (
            _element_key(child) is not None
            or (
                parent.tag.endswith('s')
                and not parent.tag.endswith(('ss', 'us', 'is'))
            )
        )
    return False


def _match_xml_child(
    english_child: ET.Element,
    russian_children: list[ET.Element],
    occurrence: int,
    is_list: bool,
) -> ET.Element | None:
    candidates = [child for child in russian_children if child.tag == english_child.tag]
    if not candidates:
        return None

    key = _element_key(english_child) if is_list else None
    if key is not None:
        for candidate in candidates:
            if _element_key(candidate) == key:
                return candidate

    if occurrence < len(candidates):
        return candidates[occurrence]
    return None


def _build_xml_patch(
    english: ET.Element,
    russian: ET.Element,
    translatable_element_ids: set[int],
    parent_is_list: bool = False,
    list_index: int = 0,
) -> ET.Element | None:
    """Build a minimal launcher XML patch containing translated text only."""
    patch = ET.Element(english.tag)

    if parent_is_list:
        key = _element_key(english)
        if key is not None:
            patch.set('id', key[1])
        else:
            patch.set('index', str(list_index + 1))

    english_children = list(english)
    russian_children = list(russian)
    english_is_list = _children_are_list_items(english, english_children)
    occurrences: dict[str, int] = {}

    for english_child in english_children:
        occurrence = occurrences.get(english_child.tag, 0)
        occurrences[english_child.tag] = occurrence + 1
        russian_child = _match_xml_child(
            english_child,
            russian_children,
            occurrence,
            english_is_list,
        )
        if russian_child is None:
            continue
        if _is_key_element(english, english_child):
            continue

        matching_siblings = [
            child for child in english_children if child.tag == english_child.tag
        ]
        child_is_list_item = english_is_list
        child_index = occurrence
        if not child_is_list_item and len(matching_siblings) > 1:
            child_is_list_item = True

        child_patch = _build_xml_patch(
            english_child,
            russian_child,
            translatable_element_ids,
            parent_is_list=child_is_list_item,
            list_index=child_index,
        )
        if child_patch is not None:
            patch.append(child_patch)

    english_text = (english.text or '').strip()
    russian_text = (russian.text or '').strip()
    if not english_children and english_text and russian_text:
        if (
            id(english) in translatable_element_ids
            and english_text != russian_text
            and not _is_technical_string(english_text)
        ):
            patch.text = russian_text
            return patch
        return None

    return patch if len(patch) else None


def _make_xml_translation_patch(english_root: ET.Element, russian_root: ET.Element) -> ET.Element | None:
    if english_root.tag != russian_root.tag:
        raise ValueError(
            f'Не совпадают корневые XML-элементы: '
            f'{english_root.tag} и {russian_root.tag}'
        )

    patch_root = ET.Element(english_root.tag)
    translatable_element_ids = {
        id(element)
        for element, _, _ in iter_translatable_elements(english_root)
    }
    english_entities = list(english_root)
    russian_entities = list(russian_root)
    russian_by_key = {
        _element_key(entity): entity
        for entity in russian_entities
        if _element_key(entity) is not None
    }
    english_is_list = _children_are_list_items(english_root, english_entities)

    for index, english_entity in enumerate(english_entities):
        key = _element_key(english_entity) if english_is_list else None
        russian_entity = russian_by_key.get(key) if key is not None else None

        # TourItems in the legacy translation output contain translated copies
        # first, followed by the original English items.
        if russian_entity is None and english_root.tag == 'ArrayOfTourItem':
            matching = [
                entity for entity in russian_entities
                if entity.tag == english_entity.tag
            ]
            russian_entity = matching[index] if index < len(matching) else None

        if russian_entity is None:
            russian_entity = _match_xml_child(
                english_entity,
                russian_entities,
                index,
                english_is_list,
            )
        if russian_entity is None:
            continue

        entity_patch = _build_xml_patch(
            english_entity,
            russian_entity,
            translatable_element_ids,
            parent_is_list=english_is_list,
            list_index=index,
        )
        if entity_patch is not None:
            patch_root.append(entity_patch)

    return patch_root if len(patch_root) else None


def _line_parts(line: str) -> tuple[str, str]:
    content = line.rstrip('\r\n')
    return content, line[len(content):]


def _build_galactopedia_title_map(english_dir: Path, russian_dir: Path) -> dict[str, str]:
    english_file = english_dir / 'DW2' / 'GameText.txt'
    russian_file = russian_dir / 'DW2' / 'GameText.txt'
    if not english_file.exists() or not russian_file.exists():
        return {}

    english_by_key = {}
    for line in _read_text_file_lines(english_file):
        content, _ = _line_parts(line)
        if ';' in content:
            key, text = content.split(';', 1)
            english_by_key[key.strip()] = text.strip()

    translated_titles = {}
    for line in _read_text_file_lines(russian_file):
        content, _ = _line_parts(line)
        if ';' not in content:
            continue
        key, translated = content.split(';', 1)
        key = key.strip()
        translated = translated.strip()
        if key in english_by_key and translated and translated != english_by_key[key]:
            translated_titles[key] = translated
    return translated_titles


def _resolve_export_russian_path(
    english_file: Path,
    english_dir: Path,
    russian_dir: Path,
    galactopedia_titles: dict[str, str],
) -> Path:
    relative_path = english_file.relative_to(english_dir)
    direct_path = russian_dir / relative_path
    if direct_path.exists():
        return direct_path

    if 'Galactopedia' in relative_path.parts:
        translated_title = galactopedia_titles.get(relative_path.stem)
        if translated_title:
            translated_path = russian_dir / relative_path.with_name(
                translated_title + relative_path.suffix
            )
            if translated_path.exists():
                return translated_path
    return direct_path


def _merge_translated_txt(
    english_file: Path,
    russian_file: Path,
) -> tuple[str, bool]:
    english_lines = _read_text_file_lines(english_file)
    russian_lines = _read_text_file_lines(russian_file)
    russian_by_key: dict[str, list[str]] = {}
    for line in russian_lines:
        content, _ = _line_parts(line)
        if ';' in content:
            key, text = content.split(';', 1)
            russian_by_key.setdefault(key.strip(), []).append(text.strip())

    merged_lines = []
    translated_any = False
    used_keys: dict[str, int] = {}
    for index, english_line in enumerate(english_lines):
        english_content, newline = _line_parts(english_line)
        if not english_content.strip():
            merged_lines.append(english_line)
            continue

        if ';' in english_content:
            key, english_text = english_content.split(';', 1)
            key_value = key.strip()
            candidates = russian_by_key.get(key_value, [])
            candidate_index = used_keys.get(key_value, 0)
            used_keys[key_value] = candidate_index + 1
            if candidate_index >= len(candidates):
                merged_lines.append(english_line)
                continue

            russian_text = candidates[candidate_index]
            if russian_text and russian_text != english_text.strip():
                delimiter = english_content.index(';') + 1
                spacing = english_content[delimiter:]
                spacing = spacing[:len(spacing) - len(spacing.lstrip())]
                merged_lines.append(
                    english_content[:delimiter] + spacing + russian_text + newline
                )
                translated_any = True
            else:
                merged_lines.append(english_line)
            continue

        if index < len(russian_lines):
            russian_content, _ = _line_parts(russian_lines[index])
            if (
                russian_content.strip()
                and russian_content.strip() != english_content.strip()
            ):
                merged_lines.append(russian_content + newline)
                translated_any = True
                continue
        merged_lines.append(english_line)

    return ''.join(merged_lines), translated_any


def export_modloader_mod(
    version: str,
    english_dir: Path,
    russian_dir: Path,
    output_dir: Path,
    project_root: Path,
) -> int:
    """Export translated entries into the DW2 Mod Launcher localization format."""
    if not english_dir.exists() or not russian_dir.exists():
        print(f"[ERROR] Не найдена папка English или Russian версии {version}")
        return 1
    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"[ERROR] Папка вывода не пуста: {output_dir}")
        print("[INFO] Укажите новую или пустую папку для экспорта.")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    galactopedia_titles = _build_galactopedia_title_map(english_dir, russian_dir)
    written_xml = 0
    written_txt = 0
    skipped = 0

    for english_file in sorted(english_dir.rglob('*')):
        if not english_file.is_file() or english_file.suffix.lower() not in {'.xml', '.txt'}:
            continue

        russian_file = _resolve_export_russian_path(
            english_file,
            english_dir,
            russian_dir,
            galactopedia_titles,
        )
        if not russian_file.exists():
            skipped += 1
            continue

        relative_path = english_file.relative_to(english_dir)
        if english_file.suffix.lower() == '.xml':
            try:
                english_root = ET.parse(english_file).getroot()
                russian_root = ET.parse(russian_file).getroot()
                patch_root = _make_xml_translation_patch(english_root, russian_root)
            except (ET.ParseError, OSError, ValueError) as exc:
                print(f"[ERROR] Не удалось подготовить XML-патч {english_file}: {exc}")
                return 1
            if patch_root is None:
                skipped += 1
                continue

            if relative_path.parts[0] == 'DW2':
                patch_relative_path = Path('patches') / 'data' / Path(*relative_path.parts[1:])
            else:
                patch_relative_path = Path('patches') / relative_path
            patch_path = output_dir / patch_relative_path
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            ET.indent(patch_root, space='  ')
            ET.ElementTree(patch_root).write(
                patch_path,
                encoding='utf-8',
                xml_declaration=True,
            )
            written_xml += 1
            continue

        merged_text, translated_any = _merge_translated_txt(english_file, russian_file)
        if not translated_any:
            skipped += 1
            continue

        if relative_path.parts[0] == 'DW2':
            text_relative_path = Path(*relative_path.parts[1:])
        else:
            text_relative_path = relative_path
        if 'Galactopedia' in relative_path.parts:
            text_relative_path = russian_file.relative_to(russian_dir)
            if text_relative_path.parts[0] == 'DW2':
                text_relative_path = Path(*text_relative_path.parts[1:])
        text_path = output_dir / text_relative_path
        text_path.parent.mkdir(parents=True, exist_ok=True)
        with text_path.open('w', encoding='utf-8', newline='') as output_file:
            output_file.write(merged_text)
        written_txt += 1

    mod_json_path = project_root / 'mod.json'
    if not mod_json_path.exists():
        print(f"[ERROR] Не найден манифест игры: {mod_json_path}")
        return 1
    try:
        mod_json = json.loads(mod_json_path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Не удалось прочитать {mod_json_path}: {exc}")
        return 1
    mod_json['version'] = version
    (output_dir / 'mod.json').write_text(
        json.dumps(mod_json, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )

    launcher_manifest = {
        'language': 'ru',
        'font': 'RussianFont',
        'galactopedia': 'replace',
        'minLauncherVersion': '1.0.5',
    }
    (output_dir / 'dw2modlauncher.json').write_text(
        json.dumps(launcher_manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )

    font_bundles = sorted((project_root / 'data' / 'db' / 'bundles').glob('RussianFont*.bundle'))
    if not font_bundles:
        print(f"[ERROR] Не найдены файлы шрифта в {project_root / 'data' / 'db' / 'bundles'}")
        return 1
    for bundle in font_bundles:
        shutil.copyfile(bundle, output_dir / bundle.name)

    print(f"[INFO] Мод загрузчика собран в {output_dir}")
    print(f"[INFO] XML-патчей: {written_xml}; TXT-файлов: {written_txt}; пропущено: {skipped}")
    print("[INFO] В TXT сохраняется английский оригинал, заменяются только переведённые строки.")
    return 0


def translate_file(input_path: Path, output_path: Path, words_mode: bool = False, check_mode: bool = False, pool_addresses=None, pool_timeout: int = 120, cache_file: str = None, file_cache_file: str = None, fix_untranslated: bool = False, fix_newlines: bool = False) -> int:
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
        if fix_newlines and file_type == 'xml':
            cmd.append('--fix-newlines')
        if pool_addresses:
            for addr in pool_addresses:
                cmd.extend(['--pool', addr])
            if pool_timeout is not None:
                cmd.extend(['--pool-timeout', str(pool_timeout)])

    # Передаём файл кэша через переменную окружения
    env = os.environ.copy()
    env['DW2_GLOSSARY_CACHE'] = '.glossary_cache.pkl'
    if cache_file:
        env['DW2_TRANSLATION_CACHE'] = cache_file
    if file_cache_file:
        env['DW2_FILE_TRANSLATION_CACHE'] = file_cache_file

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

def fix_newlines_files(xml_files, english_dir, russian_dir):
    """
    Найти и доперевести недопереведённые элементы в уже переведённых XML файлах.
    TXT файлы пропускаются.
    """
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
        # Запускаем доперевод
        print(f"Доперенос {xml_file} -> {output_file}")
        result_code = translate_file(
            xml_file,
            output_file,
            words_mode=False,
            check_mode=False,
            pool_addresses=None,
            pool_timeout=0,
            cache_file=None,  # Не используем кэш для доперевода
            fix_untranslated=False,
            fix_newlines=True
        )
            
        if result_code == 0:
            fixed_count += 1
            print(f"Перенос строки завершён: {output_file}")
        else:
            print(f"Ошибка переноса строки: {output_file}")
    
    print(f"\nРезультат переносов:")
    print(f"Файлов с проблемами: {total_problems}")
    print(f"Успешно перенесено: {fixed_count}")
    return 0 if fixed_count == total_problems else 1


def collect_files_in_progress(english_dir: Path, russian_dir: Path):
    """Собирает пути файлов, которые сейчас находятся в процессе перевода."""
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
    return files_in_progress


def build_cache_from_versions(cache_versions, base_dir: Path, target_english_dir: Path, target_russian_dir: Path):
    """
    Строит единый кэш переводов из последовательности предыдущих версий
    и текущей целевой версии. Порядок важен: старые версии идут сначала,
    а целевая версия добавляется последней.
    """
    merged_cache = {}

    if cache_versions:
        for version in cache_versions:
            version_dir = base_dir / version
            english_dir = version_dir / 'English'
            russian_dir = version_dir / 'Russian'

            if not english_dir.exists() or not russian_dir.exists():
                print(f"[WARNING] Пропускаю версию {version}: отсутствуют {english_dir} или {russian_dir}")
                continue

            files_in_progress = collect_files_in_progress(english_dir, russian_dir)
            version_cache = build_translation_cache_from_paired_files(
                str(english_dir),
                str(russian_dir),
                exclude_files=files_in_progress,
            )

            if version_cache:
                merged_cache.update(version_cache)
                print(f"[INFO] Из версии {version} добавлено {len(version_cache)} переводов в общий кэш")

    if target_english_dir.exists() and target_russian_dir.exists():
        files_in_progress = collect_files_in_progress(target_english_dir, target_russian_dir)
        target_cache = build_translation_cache_from_paired_files(
            str(target_english_dir),
            str(target_russian_dir),
            exclude_files=files_in_progress,
        )

        if target_cache:
            merged_cache.update(target_cache)
            print(f"[INFO] Из целевой версии {target_english_dir.parent.parent.name} добавлено {len(target_cache)} переводов в общий кэш")

    return merged_cache


def get_cache_file_name(target_version: str, cache_versions) -> str:
    """Возвращает путь к постоянному файлу кэша для данного набора версий."""
    versions_key = '_'.join([target_version, *cache_versions])
    safe_key = ''.join(ch if ch.isalnum() else '_' for ch in versions_key)
    return f'.translation_cache_{safe_key}.pkl'


def get_previous_versions_cache_file_name(cache_versions) -> str:
    """Возвращает путь к постоянному файлу кэша только из предыдущих версий."""
    versions_key = '_'.join(cache_versions)
    safe_key = ''.join(ch if ch.isalnum() else '_' for ch in versions_key)
    return f'.previous_translation_cache_{safe_key}.pkl'


def build_previous_versions_cache(cache_versions, base_dir: Path):
    """Собирает кэш только из указанных предыдущих версий (без целевой)."""
    cache_file = get_previous_versions_cache_file_name(tuple(cache_versions))
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'rb') as f:
                cached = pickle.load(f)
            print(f"[INFO] Использую сохранённый кэш предыдущих версий из {cache_file} ({len(cached)} записей)")
            return cached
        except Exception as exc:
            print(f"[WARNING] Не удалось загрузить сохранённый кэш предыдущих версий: {exc}")

    merged_cache = {}

    for version in cache_versions:
        version_dir = base_dir / version
        english_dir = version_dir / 'English'
        russian_dir = version_dir / 'Russian'

        if not english_dir.exists() or not russian_dir.exists():
            print(f"[WARNING] Пропускаю версию {version}: отсутствуют {english_dir} или {russian_dir}")
            continue

        version_cache = build_translation_cache_from_paired_files(
            str(english_dir),
            str(russian_dir),
            exclude_files=collect_files_in_progress(english_dir, russian_dir),
        )
        if version_cache:
            merged_cache.update(version_cache)
            print(f"[INFO] Из версии {version} добавлено {len(version_cache)} переводов в базовый кэш")

    if merged_cache:
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(merged_cache, f)
            print(f"[INFO] Сохранён кэш предыдущих версий в {cache_file} ({len(merged_cache)} записей)")
        except Exception as exc:
            print(f"[WARNING] Не удалось сохранить кэш предыдущих версий: {exc}")

    return merged_cache


def build_file_translation_cache(cache_versions, base_dir: Path, rel_path: Path) -> dict:
    """
    Строит локальный приоритетный кэш только для конкретного файла из предыдущих версий.
    Используется перед общим кэшем и словарём, чтобы файл мог иметь свои собственные
    сокращения/переводы без конфликтов с общими правилами.
    """
    file_cache = {}

    for version in cache_versions:
        version_dir = base_dir / version
        english_file = version_dir / 'English' / rel_path
        russian_file = version_dir / 'Russian' / resolve_russian_rel_path(rel_path, version_dir / 'Russian')

        if not english_file.exists() or not russian_file.exists():
            continue

        if english_file.name != russian_file.name:
            file_cache[english_file.name] = russian_file.name

        pairs = collect_translation_pairs(english_file, russian_file)
        for eng_text, rus_text in pairs:
            if eng_text and rus_text and eng_text != rus_text:
                file_cache[eng_text] = rus_text

    return file_cache


def save_translation_cache(cache: dict, rel_path: Path) -> str | None:
    """Сохраняет кэш в временный pickle-файл для передачи в translate.py."""
    if not cache:
        return None

    safe_name = ''.join(ch if ch.isalnum() else '_' for ch in str(rel_path))
    cache_file = Path(f'.translation_cache_file_{safe_name}.pkl')

    try:
        with open(cache_file, 'wb') as f:
            pickle.dump(cache, f)
        return str(cache_file)
    except Exception as exc:
        print(f"[WARNING] Не удалось сохранить локальный кэш для {rel_path}: {exc}")
        return None


def _read_txt_lines(file: Path) -> list[str] | None:
    """Read a text file, trying utf-8 first, then falling back to cp1251."""
    for enc in ('utf-8', 'cp1251'):
        try:
            with open(file, 'r', encoding=enc) as f:
                return f.readlines()
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def collect_translation_pairs(english_file: Path, russian_file: Path):
    """Возвращает пары (английский текст, русский текст) для одного файла."""
    if english_file.suffix.lower() == '.txt':
        eng_lines = _read_txt_lines(english_file)
        if eng_lines is None:
            print(f"[WARNING] Не удалось прочитать txt-файл {english_file}: не удалось определить кодировку")
            return []
        rus_lines = _read_txt_lines(russian_file)
        if rus_lines is None:
            print(f"[WARNING] Не удалось прочитать txt-файл {russian_file}: не удалось определить кодировку")
            return []

        line_count = min(len(eng_lines), len(rus_lines))
        pairs = []
        for i in range(line_count):
            eng_line = eng_lines[i].rstrip('\n')
            rus_line = rus_lines[i].rstrip('\n')

            eng_original = eng_line.strip()
            rus_original = rus_line.strip()
            if not eng_original or not rus_original:
                continue

            if ';' in eng_original:
                eng_parts = eng_original.split(';', 1)
                eng_text = eng_parts[1].strip() if len(eng_parts) > 1 else eng_original
                rus_parts = rus_original.split(';', 1)
                rus_text = rus_parts[1].strip() if len(rus_parts) > 1 else rus_original
            else:
                eng_text = eng_original
                rus_text = rus_original

            if eng_text and rus_text and not _is_technical_string(eng_text):
                pairs.append((eng_text, rus_text))
        return pairs

    try:
        eng_tree = ET.parse(english_file)
        rus_tree = ET.parse(russian_file)
    except Exception as exc:
        print(f"[WARNING] Не удалось разобрать XML-файл {english_file}: {exc}")
        return []

    eng_texts = extract_translatable_texts(eng_tree.getroot())
    rus_texts = extract_translatable_texts(rus_tree.getroot())

    pairs = []
    for (eng_text, tag_desc), (rus_text, _) in zip(eng_texts, rus_texts):
        if not eng_text or not rus_text:
            continue
        if rus_text == eng_text:
            continue
        pairs.append((eng_text, rus_text))
    return pairs


def write_new_translated_lines_report(target_english_dir: Path, target_russian_dir: Path, previous_versions: list[str], base_dir: Path):
    """Создаёт рядом с русскими файлами отчёты *_new_translated_lines.txt.

    Для сравнения используется только локальный кэш по тому же файлу из предыдущих версий,
    без общего кэша и без glossary.
    """
    written_files = 0

    for english_file in sorted(target_english_dir.rglob('*')):
        if english_file.suffix.lower() not in ('.xml', '.txt'):
            continue

        rel_path = english_file.relative_to(target_english_dir)
        file_old_cache = build_file_translation_cache(previous_versions, base_dir, rel_path)

        russian_file = target_russian_dir / resolve_russian_rel_path(rel_path, target_russian_dir)
        if not russian_file.exists():
            continue

        pairs = collect_translation_pairs(english_file, russian_file)

        new_pairs = []
        for eng_text, rus_text in pairs:
            previous_translation = file_old_cache.get(eng_text)
            if previous_translation is None or previous_translation != rus_text:
                new_pairs.append((eng_text, rus_text))

        # В отчёт должны попадать только те строки, где текущий перевод
        # отличается от перевода из предыдущей версии игры. Если таких строк
        # нет, файл отчёта не создаётся.

        report_path = russian_file.with_name(russian_file.stem + '_new_translated_lines.txt')
        report_lines = []
        for eng_text, rus_text in new_pairs:
            report_lines.append(f"\n{eng_text}\n->\n{rus_text}")

        if not report_lines:
            continue

        report_path.write_text('\n'.join(report_lines) + '\n', encoding='utf-8')
        written_files += 1
        print(f"[REPORT] Создан {report_path} ({len(new_pairs)} новых фраз)")

    print(f"[INFO] Создано {written_files} отчётов *_new_translated_lines.txt")
    return 0


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
    parser.add_argument(
        '--fix-newlines',
        action='store_true',
        help='Постобработка: заменить переносы строк на \\n во всех переведённых XML файлах в русской директории'
    )
    parser.add_argument(
        '--show-new-translated-diff-ver',
        '--show_new_translated_diff_ver',
        dest='show_new_translated_diff_ver',
        action='store_true',
        help='Сгенерировать рядом с файлами целевой версии отчёты *_new_translated_lines.txt с новыми фразами'
    )
    parser.add_argument(
        '--target-version',
        default=DEFAULT_TARGET_VERSION,
        help='Целевая версия для перевода (по умолчанию: 1.3.5.7)'
    )
    parser.add_argument(
        '--cache-from',
        dest='cache_versions',
        nargs='*',
        default=list(DEFAULT_CACHE_VERSIONS),
        help='Последовательность предыдущих версий для кэша, от старой к новой (например: 1.3.4.3 1.3.5.7)'
    )
    parser.add_argument(
        '--check-rag',
        '--check_rag',
        dest='check_rag',
        help='Проверить найденные RAG-примеры для английского текста без запуска перевода'
    )
    parser.add_argument(
        '--export-modloader',
        metavar='OUTPUT_DIR',
        help='Собрать в указанную папку мод локализации для DW2 Mod Launcher'
    )

    args = parser.parse_args()

    if args.export_modloader:
        incompatible_options = (
            args.force
            or args.all
            or args.words
            or args.check
            or args.pool
            or args.fix_untranslated
            or args.fix_newlines
            or args.show_new_translated_diff_ver
            or args.check_rag
        )
        if incompatible_options:
            parser.error('--export-modloader нельзя использовать с другими режимами обработки')

        project_root = Path(__file__).resolve().parent
        english_dir = project_root / args.target_version / 'English'
        russian_dir = project_root / args.target_version / 'Russian'
        output_dir = Path(args.export_modloader).resolve()
        return export_modloader_mod(
            args.target_version,
            english_dir,
            russian_dir,
            output_dir,
            project_root,
        )

    if args.check_rag and not os.environ.get('DW2_PG_PASSWORD', '').strip():
        parser.error('--check-rag требует DW2_PG_PASSWORD для доступа к PostgreSQL')

    if args.words and args.check:
        parser.error('--words и --check нельзя использовать одновременно')
    
    if args.fix_untranslated and (args.words or args.check or args.force):
        parser.error('--fix-untranslated нельзя использовать с --words, --check или --force')
    
    if args.fix_newlines and (args.words or args.check or args.force or args.fix_untranslated):
        parser.error('--fix-newlines нельзя использовать с другими опциями')

    db_password = os.environ.get('DW2_PG_PASSWORD', '').strip()
    db_config = None
    if db_password:
        print("\n[INFO] Проверка и применение миграций базы данных...")
        try:
            db_config = {
                'host': os.environ.get('DW2_PG_HOST', 'localhost'),
                'port': int(os.environ.get('DW2_PG_PORT', 5432)),
                'dbname': os.environ.get('DW2_PG_DB', 'dw2russian'),
                'user': os.environ.get('DW2_PG_USER', 'postgres'),
                'password': db_password,
            }
            migrate_result = migrate_db(db_config=db_config, show_status=False)
            if migrate_result.get('status') == 'success':
                print(f"[INFO] Миграции применены: {migrate_result['migrations_applied']}")
            elif migrate_result.get('status') == 'error':
                print(f"[WARNING] Ошибка миграции: {migrate_result.get('error', 'unknown')}")
                print("[INFO] Продолжаем работу без миграций...")
                return 1
            else:
                print("[INFO] База данных актуальна. Миграции не требуются.")
        except SystemExit:
            print("[WARNING] Migrate DB вызвал SystemExit; продолжаем без прерывания batch-операции.")
        except Exception as e:
            print(f"[WARNING] Не удалось применить миграции: {e}")
            print("[INFO] Продолжаем работу без миграций...")
    else:
        print("[INFO] DW2_PG_PASSWORD не задан. Пропускаю автоматическую миграцию базы данных.")

    pool_addresses = []
    if args.pool:
        for chunk in args.pool:
            pool_addresses.extend([addr.strip() for addr in chunk.split(',') if addr.strip()])

    english_dir = Path('.') / args.target_version / 'English'
    russian_dir = Path('.') / args.target_version / 'Russian'

    if not english_dir.exists():
        print(f"Error: English directory '{english_dir}' does not exist")
        return 1

    xml_files = list(english_dir.rglob('*.xml'))
    txt_files = list(english_dir.rglob('*.txt'))
    all_files = xml_files + txt_files

    if db_config and not (args.words or args.check or args.fix_untranslated or args.fix_newlines):
        cache_versions = [v for v in args.cache_versions if v != args.target_version]
        cache_translations = build_previous_versions_cache(cache_versions, Path('.'))
        reference_versions = cache_versions or [args.target_version]
        print(f"\n[INFO] Загрузка справочных переводов для RAG: {reference_versions}")
        try:
            for version_name in reference_versions:
                version_dir = Path('.') / version_name
                english_version_dir = version_dir / 'English'
                russian_version_dir = version_dir / 'Russian'
                if not english_version_dir.exists() or not russian_version_dir.exists():
                    print(f"[WARNING] Пропускаю версию {version_name}: отсутствуют директории {english_version_dir} или {russian_version_dir}")
                    continue

                version_cache = build_translation_cache_from_paired_files(
                    str(english_version_dir),
                    str(russian_version_dir),
                    exclude_files=collect_files_in_progress(english_version_dir, russian_version_dir),
                )
                if version_cache:
                    load_translations_to_db(
                        reference_pairs=version_cache,
                        source_version=version_name,
                        db_config=db_config,
                        generate_embeddings=False,
                    )
        except SystemExit:
            print("[WARNING] Загрузка RAG-данных вызвала SystemExit; прекращаю batch-процесс.")
            return 1
        except Exception as e:
            print(f"[WARNING] Не удалось загрузить переводы в RAG: {e}")
            return 1

        pending_texts = collect_pending_translation_texts(
            all_files,
            english_dir,
            cache_translations,
        )
        print(f"\n[INFO] Найдено {len(pending_texts)} фраз для регистрации в базе данных (target_version={args.target_version})")
        register_pending_translations(
            pending_texts,
            source_version=args.target_version,
            db_config=db_config,
        )

        missing_embeddings = count_missing_embeddings(db_config=db_config)
        if missing_embeddings:
            generate_missing_embeddings(db_config=db_config)
            remaining_embeddings = count_missing_embeddings(db_config=db_config)
            if remaining_embeddings:
                print(
                    f"[ERROR] Осталось {remaining_embeddings} строк без embedding. "
                    "Перевод не запускается; повторите подготовительный этап."
                )
                return 1
            if not unload_all_models():
                print("[ERROR] Перевод остановлен: не удалось выгрузить модели из LM Studio.")
                return 1

        if args.check_rag:
            if not unload_all_models():
                print("[ERROR] Проверка RAG остановлена: не удалось выгрузить модели из LM Studio.")
                return 1
            print(f"\n[INFO] Проверка RAG для: {args.check_rag!r}")
            try:
                results = search_similar_translations(
                    query=args.check_rag,
                    top_k=10,
                    db_config=db_config,
                    use_hybrid=True,
                    generate_query_embedding=True,
                )
            except Exception as exc:
                print(f"[ERROR] RAG search failed: {type(exc).__name__}: {exc!r}")
                return 1

            if not results:
                print("[INFO] RAG не вернул релевантных переводов.")
            else:
                print(f"[INFO] Найдено примеров: {len(results)}")
                for index, item in enumerate(results, 1):
                    print(
                        f"{index}. similarity={item.get('similarity', 0):.4f} "
                        f"version={item.get('source_version', '')} "
                        f"EN: {item.get('english', '')!r} -> RU: {item.get('russian', '')!r}"
                    )
            return 0
        elif not unload_all_models():
            print("[ERROR] Перевод остановлен: не удалось выгрузить модели из LM Studio.")
            return 1

    # return 1
    # Загружаем кэш переводов из уже переведённых файлов.
    # Если файл уже был посчитан ранее, переиспользуем его вместо повторного построения.
    import pickle
    cache_file = get_cache_file_name(args.target_version, tuple(args.cache_versions))

    if russian_dir.exists() and not args.check and not args.words and not args.fix_untranslated and not args.fix_newlines:
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    cache = pickle.load(f)
                print(f"[INFO] Использую сохранённый кэш из {cache_file} ({len(cache)} записей)")
            except Exception as exc:
                print(f"[WARNING] Не удалось загрузить сохранённый кэш {cache_file}: {exc}")
                cache = None
        else:
            print("\n[INFO] Построение кэша переводов из уже переведённых файлов...")
            print("[INFO] Пропускаем файлы в процессе перевода...")

            cache = build_cache_from_versions(args.cache_versions, Path('.'), english_dir, russian_dir)

        if cache:
            try:
                with open(cache_file, 'wb') as f:
                    pickle.dump(cache, f)
                print(f"[INFO] Кэш сохранён в {cache_file} ({len(cache)} записей)")
            except Exception as exc:
                print(f"[WARNING] Ошибка сохранения кэша: {exc}")
                cache_file = None
        else:
            cache_file = None
    else:
        cache_file = None

    if cache_file:
        os.environ['DW2_TRANSLATION_CACHE'] = cache_file

    if args.fix_untranslated:
        print(f"Поиск недопереведённых элементов в {len(all_files)} файлах...")
        return fix_untranslated_files(all_files, english_dir, russian_dir, pool_addresses, args.pool_timeout)

    if args.fix_newlines:
        print(f"\n[INFO] Постобработка переносов строк в русской директории...")
        return fix_newlines_files(xml_files, english_dir, russian_dir)

    if args.show_new_translated_diff_ver:
        previous_versions = [version for version in args.cache_versions if version != args.target_version]
        print(f"\n[INFO] Генерация отчётов новых фраз для {args.target_version}...")
        return write_new_translated_lines_report(english_dir, russian_dir, previous_versions, Path('.'))
  
    print(f"Found {len(xml_files)} XML files and {len(txt_files)} TXT files to process")

    success_count = 0
    skip_count = 0
    created_cache_files = []

    # If not --all, translate only one eligible file and exit.
    for file_path in all_files:
        untranslated_found = False
        # Compute relative path from English directory
        rel_path = file_path.relative_to(english_dir)
        
        # Special handling for Galactopedia: translate filenames in the output path.
        rel_path_for_output = get_galactopedia_translated_rel_path(rel_path)
        output_file = russian_dir / rel_path_for_output

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
            output_is_incomplete = (
                file_path.suffix.lower() == '.txt'
                and has_untranslated_txt_content(file_path, output_file)
            )
            if output_file.exists() and not args.force and not is_paused and not output_is_incomplete:
                print(f"Skipping {file_path} (output exists: {output_file})")
                skip_count += 1
                continue

            if output_is_incomplete:
                print(f"Найден неполный перевод, обрабатываю заново: {file_path}")
            
            # Если файл на паузе, продолжаем перевод (не пропускаем)
            if is_paused:
                print(f"Resuming paused translation: {file_path}")

            # Ensure output directory exists
            output_file.parent.mkdir(parents=True, exist_ok=True)

        file_cache_file = None
        if not args.check and not args.words and not args.fix_untranslated and not args.fix_newlines:
            file_cache = build_file_translation_cache(args.cache_versions, Path('.'), rel_path)
            if file_cache:
                file_cache_file = save_translation_cache(file_cache, rel_path)
                if file_cache_file:
                    created_cache_files.append(file_cache_file)
                    print(f"[INFO] Локальный кэш для {rel_path}: {len(file_cache)} переводов")

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
            file_cache_file=file_cache_file,
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

    for cache_file_path in created_cache_files:
        try:
            if os.path.exists(cache_file_path):
                os.remove(cache_file_path)
        except Exception as exc:
            print(f"[WARNING] Ошибка удаления локального кэша {cache_file_path}: {exc}")

    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())