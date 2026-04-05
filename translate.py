import argparse
import re
import sys
import threading
import xml.etree.ElementTree as ET
import requests
from concurrent.futures import ThreadPoolExecutor
from system_prompt import SYSTEM_PROMPT

DEFAULT_MODEL = "qwen/qwen3.5-35b-a3b"
# DEFAULT_MODEL = "google/gemma-4-26b-a4b"
DEFAULT_LOCAL_ENDPOINT = "http://localhost:1234/v1/chat/completions"
DEFAULT_POOL_TIMEOUT = 120000


def translate_text(text: str) -> str:
    """
    Перевод текста через LM Studio (локальный LLM).
    Требуется запущенный LM Studio с включённым локальным сервером.
    """

    prompt = (
        f"Text: {text}"
    )

    response = requests.post(
        DEFAULT_LOCAL_ENDPOINT,
        json={
            "model": DEFAULT_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
        },
        timeout=DEFAULT_POOL_TIMEOUT,
    )

    response.raise_for_status()
    result = response.json()
    translated = result["choices"][0]["message"]["content"].strip()
    translated = translated.replace('‑', '-')
    return translated


def normalize_pool_address(address: str) -> str:
    address = address.strip()
    if not address:
        raise ValueError("Empty pool address")
    if not address.startswith(("http://", "https://")):
        address = "http://" + address
    return address.rstrip('/')


class LocalTranslator:
    def translate_many(self, texts):
        return [translate_text(text) for text in texts]


class RemotePoolTranslator:
    def __init__(self, addresses, model=DEFAULT_MODEL, timeout=DEFAULT_POOL_TIMEOUT):
        self.endpoints = [normalize_pool_address(a) for a in addresses]
        if not self.endpoints:
            raise ValueError("Pool must contain at least one endpoint")
        self.model = model
        self.timeout = timeout
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.next_index = 0
        self.executor = ThreadPoolExecutor(max_workers=len(self.endpoints))

    def translate_many(self, texts):
        if not texts:
            return []
        futures = [self.executor.submit(self._translate_with_failover, text) for text in texts]
        return [future.result() for future in futures]

    def _translate_with_failover(self, text):
        last_error = None
        for _ in range(len(self.endpoints)):
            endpoint = self._next_endpoint()
            try:
                return self._send_request(text, endpoint)
            except requests.RequestException as exc:
                last_error = exc
                continue
        raise RuntimeError(f"All pool endpoints failed: {last_error}") from last_error

    def _next_endpoint(self):
        with self.lock:
            endpoint = self.endpoints[self.next_index]
            self.next_index = (self.next_index + 1) % len(self.endpoints)
            return endpoint

    def _send_request(self, text, endpoint):
        url = f"{endpoint}/v1/chat/completions"
        prompt = f"Text: {text}"
        response = self.session.post(
            url,
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        translated = result["choices"][0]["message"]["content"].strip()
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

    def should_ignore_text(text: str) -> bool:
        """Check if text should be ignored during English word detection."""
        # Remove newlines for checking
        text = text.replace('\n', '').strip()
        if not text:
            return True
            
        # Find all bracketed content
        bracket_pattern = re.compile(r'\[([^\]]*)\]|\{([^\}]*)\}')
        brackets = bracket_pattern.findall(text)
        
        # Remove bracketed content from text
        text_without_brackets = bracket_pattern.sub('', text).strip()
        
        # Check if remaining text has English letters (but not Cyrillic or other non-ASCII)
        # Only consider it English if it contains ASCII letters
        ascii_letters = re.findall(r'[A-Za-z\+\-%]+', text_without_brackets)
        if ascii_letters:
            # Check if these are actual English words (not just single letters or codes)
            for word in ascii_letters:
                if len(word) > 2:  # Ignore short codes/abbreviations
                    return False
            
        # Check bracketed content - should only contain digits, English letters, or closing tags
        for bracket_content in brackets:
            content = bracket_content[0] or bracket_content[1]  # Either from [] or {}
            # Allow digits, English letters, spaces, and closing tags like </tag>
            if not re.match(r'^[\d\sA-Za-z\+\-%,</>]*$', content):
                return False
                
        return True

    def check_text(elem, path):
        # Skip technical tags that contain game constants/identifiers
        technical_tags = {'Type', 'ImageFilename', 'AppliesTo', 'ArtifactId', 'DiscoveryLevel', 
                         'BonusesOnlyWhenAtColony', 'BonusesOnlyWhenAtCapital', 'PsychicResistance', 
                         'RaceId', 'Amount'}
        if elem.tag in technical_tags:
            return
            
        if elem.text and elem.text.strip() and not should_ignore_text(elem.text):
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

def translate_xml(input_file: str, output_file: str, words_mode=False, translator=None):
    tree = ET.parse(input_file)
    root = tree.getroot()
    log_entries = []
    words_set = set()
    tasks = []

    def add_task(elem, tag, original):
        tasks.append((elem, tag, original))

    for elem in root.iter():
        elem_tag = elem.tag
        if elem_tag == 'Artifact':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        elif elem_tag == 'GameEvent':
            for action in elem.findall('.//TriggerActions/GameEventAction'):
                for tag in ['MessageTitle', 'Description', 'ChoiceButtonText']:
                    for child in action.iter(tag):
                        original = child.text.strip() if child.text else ''
                        if original:
                            if words_mode:
                                words_set.add(original)
                            else:
                                add_task(child, tag, original)
        elif elem_tag == 'PlanetaryFacilityDefinition':
            for child in elem.iter('Name'):
                original = child.text.strip() if child.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(child, 'Name', original)
        elif elem_tag == 'Race':
            for child in elem.iter('Description'):
                original = child.text.strip() if child.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(child, 'Description', original)
            for string_elem in elem.findall('.//FeatureExplanations/string'):
                original = string_elem.text.strip() if string_elem.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(string_elem, 'string (FeatureExplanations)', original)
        elif elem_tag == 'ResearchProjectDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        elif elem_tag == 'ShipHull':
            for child in elem.iter('Name'):
                original = child.text.strip() if child.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(child, 'Name', original)
        elif elem_tag == 'TroopDefinition':
            for child in elem.iter('Name'):
                original = child.text.strip() if child.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(child, 'Name', original)
        elif elem_tag == 'ArmyTemplate':
            for child in elem.iter('Name'):
                original = child.text.strip() if child.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(child, 'Name', original)
        elif elem_tag == 'ColonyEventDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        elif elem_tag == 'ComponentDefinition':
            for child in elem.iter('Name'):
                original = child.text.strip() if child.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(child, 'Name', original)
        elif elem_tag == 'FleetTemplate':
            for child in elem.iter('Name'):
                original = child.text.strip() if child.text else ''
                if original:
                    if words_mode:
                        words_set.add(original)
                    else:
                        add_task(child, 'Name', original)
        elif elem_tag == 'Government':
            for tag in ['Name', 'Description', 'string']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        elif elem_tag == 'OrbType':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        elif elem_tag == 'Resource':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        elif elem_tag == 'SpaceItemDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        elif elem_tag == 'TourItem':
            for tag in ['StepTitle', 'MarkupText']:
                for child in elem.iter(tag):
                    original = child.text.strip() if child.text else ''
                    if original:
                        if words_mode:
                            words_set.add(original)
                        else:
                            add_task(child, tag, original)
        # For unknown elements, do nothing

    if words_mode:
        words_file = output_file.replace('.xml', '_words.txt')
        with open(words_file, 'w', encoding='utf-8') as f:
            for word in sorted(words_set):
                f.write(word + "\n")
        print(f"Список слов для словаря сохранён: {words_file}")
        return

    # Process translations one by one to show logs in real time
    for elem, tag, original in tasks:
        if translator is None:
            translated = translate_text(original)
        else:
            translated = translator.translate_many([original])[0]
        
        elem.text = translated
        log_msg = f"{tag}: {original}\n->\n{translated}\n"
        print(log_msg, flush=True)
        log_entries.append(log_msg)

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
    parser.add_argument(
        '--pool',
        action='append',
        help='Список адресов пул воркеров, разделённых запятыми или повторным ключом'
    )
    parser.add_argument(
        '--pool-timeout',
        type=int,
        default=DEFAULT_POOL_TIMEOUT,
        help='Таймаут для запроса к пулу в секундах'
    )

    args = parser.parse_args()

    if args.check:
        if args.words:
            parser.error('--check и --words нельзя использовать одновременно')
        return check_translated_file(args.input)

    if args.output is None:
        parser.error('output обязателен, если не задан --check')

    pool_addresses = []
    if args.pool:
        for chunk in args.pool:
            pool_addresses.extend([addr.strip() for addr in chunk.split(',') if addr.strip()])

    translator = None
    if pool_addresses:
        translator = RemotePoolTranslator(pool_addresses, timeout=args.pool_timeout)

    translate_xml(args.input, args.output, words_mode=args.words, translator=translator)


if __name__ == "__main__":
    sys.exit(main())