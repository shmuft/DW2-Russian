import argparse
import re
import sys
import xml.etree.ElementTree as ET
import requests
from system_prompt import SYSTEM_PROMPT

def translate_text(text: str) -> str:
    """
    Перевод текста через LM Studio (локальный LLM).
    Требуется запущенный LM Studio с включённым локальным сервером.
    """

    prompt = (
        f"Text: {text}"
    )

    response = requests.post(
        "http://localhost:1234/v1/chat/completions",
        json={
            "model": "qwen/qwen3.5-35b-a3b",  # название модели в LM Studio
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
                ],
            "temperature": 0.1,
        }
    )

    result = response.json()
    translated = result["choices"][0]["message"]["content"].strip()
    translated = translated.replace('‑', '-')
    return translated


def iter_with_path(elem, path=''):
    current_path = f"{path}/{elem.tag}" if path else elem.tag
    yield elem, current_path
    for child in elem:
        yield from iter_with_path(child, current_path)


def check_translated_file(file_path: str) -> int:
    tree = ET.parse(file_path)
    root = tree.getroot()
    english_re = re.compile(r'[A-Za-z]')
    found = []

    def check_text(elem, path):
        if elem.text and elem.text.strip() and english_re.search(elem.text):
            found.append((path, elem.text.strip()))

    for elem in root.iter():
        elem_tag = elem.tag
        if elem_tag == 'Artifact':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")
        elif elem_tag == 'GameEvent':
            for action in elem.findall('.//TriggerActions/GameEventAction'):
                for tag in ['MessageTitle', 'Description', 'ChoiceButtonText']:
                    for child in action.iter(tag):
                        check_text(child, f"{elem_tag}/TriggerActions/GameEventAction/{tag}")
        elif elem_tag == 'PlanetaryFacilityDefinition':
            for child in elem.iter('Name'):
                check_text(child, f"{elem_tag}/Name")
        elif elem_tag == 'Race':
            for child in elem.iter('Description'):
                check_text(child, f"{elem_tag}/Description")
            for string_elem in elem.findall('.//FeatureExplanations/string'):
                check_text(string_elem, f"{elem_tag}/FeatureExplanations/string")
        elif elem_tag == 'ResearchProjectDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")
        elif elem_tag == 'ShipHull':
            for child in elem.iter('Name'):
                check_text(child, f"{elem_tag}/Name")
        elif elem_tag == 'TroopDefinition':
            for child in elem.iter('Name'):
                check_text(child, f"{elem_tag}/Name")
        elif elem_tag == 'ArmyTemplate':
            for child in elem.iter('Name'):
                check_text(child, f"{elem_tag}/Name")
        elif elem_tag == 'ColonyEventDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")
        elif elem_tag == 'ComponentDefinition':
            for child in elem.iter('Name'):
                check_text(child, f"{elem_tag}/Name")
        elif elem_tag == 'FleetTemplate':
            for child in elem.iter('Name'):
                check_text(child, f"{elem_tag}/Name")
        elif elem_tag == 'Government':
            for tag in ['Name', 'Description', 'string']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")
        elif elem_tag == 'OrbType':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")
        elif elem_tag == 'Resource':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")
        elif elem_tag == 'SpaceItemDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")
        elif elem_tag == 'TourItem':
            for tag in ['StepTitle', 'MarkupText']:
                for child in elem.iter(tag):
                    check_text(child, f"{elem_tag}/{tag}")

    if not found:
        print(f"Проверка пройдена: английских слов в {file_path} не найдено.")
        return 0

    print(f"Найдено {len(found)} строк с английскими символами в {file_path}:")
    for path, text in found:
        print(f"[{path}] {text}")
    return 1


def translate_tags(element, tags, log_entries=None, words_set=None, words_mode=False):
    """Translate specified tags within an element and/or collect words to translate."""
    if log_entries is None:
        log_entries = []
    if words_set is None:
        words_set = set()

    for tag in tags:
        for elem in element.iter(tag):
            if elem.text and elem.text.strip():
                original = elem.text.strip()
                if words_mode:
                    words_set.add(original)
                else:
                    translated = translate_text(original)
                    elem.text = translated
                    log_msg = f"{tag}: {original}\n->\n{translated}\n"
                    print(log_msg)
                    log_entries.append(log_msg)
    return log_entries, words_set

def translate_xml(input_file: str, output_file: str, words_mode=False):
    tree = ET.parse(input_file)
    root = tree.getroot()
    log_entries = []
    words_set = set()

    for elem in root.iter():
        elem_tag = elem.tag
        if elem_tag == 'Artifact':
            log_entries, words_set = translate_tags(elem, ['Name', 'Description'], log_entries, words_set, words_mode)
        elif elem_tag == 'GameEvent':
            # Translate in GameEventAction under TriggerActions
            for action in elem.findall('.//TriggerActions/GameEventAction'):
                log_entries, words_set = translate_tags(action, ['MessageTitle', 'Description', 'ChoiceButtonText'], log_entries, words_set, words_mode)
        elif elem_tag == 'PlanetaryFacilityDefinition':
            log_entries, words_set = translate_tags(elem, ['Name'], log_entries, words_set, words_mode)
        elif elem_tag == 'Race':
            log_entries, words_set = translate_tags(elem, ['Description'], log_entries, words_set, words_mode)
            # Also translate strings in FeatureExplanations
            for string_elem in elem.findall('.//FeatureExplanations/string'):
                if string_elem.text and string_elem.text.strip():
                    original = string_elem.text.strip()
                    if words_mode:
                        words_set.add(original)
                    else:
                        translated = translate_text(original)
                        string_elem.text = translated
                        log_msg = f"string (FeatureExplanations): {original}\n->\n{translated}\n"
                        print(log_msg)
                        log_entries.append(log_msg)
        elif elem_tag == 'ResearchProjectDefinition':
            log_entries, words_set = translate_tags(elem, ['Name', 'Description'], log_entries, words_set, words_mode)
        elif elem_tag == 'ShipHull':
            log_entries, words_set = translate_tags(elem, ['Name'], log_entries, words_set, words_mode)
        elif elem_tag == 'TroopDefinition':
            log_entries, words_set = translate_tags(elem, ['Name'], log_entries, words_set, words_mode)
        elif elem_tag == 'ArmyTemplate':
            log_entries, words_set = translate_tags(elem, ['Name'], log_entries, words_set, words_mode)
        elif elem_tag == 'ColonyEventDefinition':
            log_entries, words_set = translate_tags(elem, ['Name', 'Description'], log_entries, words_set, words_mode)
        elif elem_tag == 'ComponentDefinition':
            log_entries, words_set = translate_tags(elem, ['Name'], log_entries, words_set, words_mode)
        elif elem_tag == 'FleetTemplate':
            log_entries, words_set = translate_tags(elem, ['Name'], log_entries, words_set, words_mode)
        elif elem_tag == 'Government':
            log_entries, words_set = translate_tags(elem, ['Name', 'Description', 'string'], log_entries, words_set, words_mode)
        elif elem_tag == 'OrbType':
            log_entries, words_set = translate_tags(elem, ['Name', 'Description'], log_entries, words_set, words_mode)
        elif elem_tag == 'Resource':
            log_entries, words_set = translate_tags(elem, ['Name', 'Description'], log_entries, words_set, words_mode)
        elif elem_tag == 'SpaceItemDefinition':
            log_entries, words_set = translate_tags(elem, ['Name', 'Description'], log_entries, words_set, words_mode)
        elif elem_tag == 'TourItem':
            log_entries, words_set = translate_tags(elem, ['StepTitle', 'MarkupText'], log_entries, words_set, words_mode)
        # For unknown elements, do nothing

    if words_mode:
        words_file = output_file.replace('.xml', '_words.txt')
        with open(words_file, 'w', encoding='utf-8') as f:
            for word in sorted(words_set):
                f.write(word + "\n")
        print(f"Список слов для словаря сохранён: {words_file}")
    else:
        tree.write(output_file, encoding="utf-8", xml_declaration=True)
        print(f"\nФайл сохранён: {output_file}")
        
        # Save log file
        log_file = output_file.replace('.xml', '_log.txt')
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"Лог перевода: {input_file}\n")
            f.write(f"Выходной файл: {output_file}\n")
            f.write(f"Всего переводов: {len(log_entries)}\n")
            f.write("="*80 + "\n\n")
            for entry in log_entries:
                f.write(entry + "\n")
        print(f"Лог сохранён: {log_file}")

def main():
    parser = argparse.ArgumentParser(
        description="CLI‑утилита для перевода XML‑файла на основе типа корневого элемента."
    )

    parser.add_argument("input", help="Путь к исходному XML‑файлу")
    parser.add_argument("output", nargs='?', help="Путь к выходному XML‑файлу (необязательно для --check)")
    parser.add_argument(
        '--words',
        action='store_true',
        help='Собрать список всех исходных строк для словаря штатным выводом'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Проверить XML на оставшиеся английские слова вместо перевода'
    )

    args = parser.parse_args()

    if args.check:
        if args.words:
            parser.error('--check и --words нельзя использовать одновременно')
        return check_translated_file(args.input)

    if args.output is None:
        parser.error('output обязателен, если не задан --check')

    translate_xml(args.input, args.output, words_mode=args.words)


if __name__ == "__main__":
    sys.exit(main())