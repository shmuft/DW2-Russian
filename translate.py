import argparse
import re
import sys
import threading
import xml.etree.ElementTree as ET
import lmstudio as lms
from concurrent.futures import ThreadPoolExecutor
from system_prompt import SYSTEM_PROMPT
import pickle
import os

try:
    import keyboard
except ImportError:
    print("Библиотека 'keyboard' не установлена. Установите её командой: pip install keyboard")
    sys.exit(1)

DEFAULT_MODEL = "qwen/qwen3.5-35b-a3b"
# DEFAULT_MODEL = "google/gemma-4-26b-a4b"
DEFAULT_POOL_TIMEOUT = 120  # timeout in seconds (was 120000ms)

paused = False

# Глобальный кеш для словаря
_glossary_cache = None
_glossary_lock = threading.Lock()

def pause_handler():
    global paused
    paused = True
    print("\nПауза запрошена. Сохраняю прогресс и выхожу...")


def _build_glossary():
    """Парсит SYSTEM_PROMPT и строит словарь переводов."""
    glossary = {}
    # Ищем все строки вида: "English Text"→"Русский текст"
    pattern = r'"([^"]+)"→"([^"]+)"'
    matches = re.findall(pattern, SYSTEM_PROMPT)
    for english, russian in matches:
        glossary[english.strip()] = russian.strip()
    return glossary


def _get_glossary():
    """Возвращает закешированный словарь (потокобезопасно)."""
    global _glossary_cache
    if _glossary_cache is None:
        with _glossary_lock:
            if _glossary_cache is None:
                _glossary_cache = _build_glossary()
                print(f"[INFO] Загружено {len(_glossary_cache)} требований из системного промпта")
    return _glossary_cache


def _translate_from_glossary(text: str) -> str:
    """
    Проверяет словарь и возвращает перевод, если найден.
    Возвращает None, если перевод не найден.
    """
    glossary = _get_glossary()
    return glossary.get(text.strip())


def translate_text(text: str) -> str:
    """
    Перевод текста через LM Studio (локальный LLM) используя lmstudio-python SDK.
    Сначала проверяет словарь из SYSTEM_PROMPT, если текст есть там - возвращает готовый перевод.
    Требуется запущенный LM Studio с включённым локальным сервером.
    """
    
    # Сначала проверяем словарь
    glossary_translation = _translate_from_glossary(text)
    if glossary_translation:
        print(f"[GLOSSARY] {text} -> {glossary_translation}")
        return glossary_translation

    # Получаем модель
    model = lms.llm(DEFAULT_MODEL)
    
    # Создаём чат с системным промптом
    chat = lms.Chat(SYSTEM_PROMPT)
    chat.add_user_message(f"Text: {text}")
    
    # Получаем ответ
    result = model.respond(
        chat,
        config={
            "temperature": 0.1,
            "maxTokens": 1024*10,
        }
    )
    
    translated = result.content.strip()
    translated = translated.replace('‑', '-')
    return translated


class LocalTranslator:
    def translate_many(self, texts):
        return [translate_text(text) for text in texts]


class RemotePoolTranslator:
    def __init__(self, addresses, model=DEFAULT_MODEL, timeout=DEFAULT_POOL_TIMEOUT):
        self.api_hosts = [self._normalize_host(a) for a in addresses]
        if not self.api_hosts:
            raise ValueError("Pool must contain at least one API host")
        self.model_name = model
        self.timeout = timeout
        self.lock = threading.Lock()
        self.next_index = 0
        self.executor = ThreadPoolExecutor(max_workers=len(self.api_hosts))

    def _normalize_host(self, address: str) -> str:
        """Normalize host address (remove protocol, keep only host:port)."""
        address = address.strip()
        if not address:
            raise ValueError("Empty pool address")
        # Remove protocol if present
        if "://" in address:
            address = address.split("://", 1)[1]
        return address.rstrip('/')

    def translate_many(self, texts):
        if not texts:
            return []
        futures = [self.executor.submit(self._translate_with_failover, text) for text in texts]
        return [future.result() for future in futures]

    def _translate_with_failover(self, text):
        last_error = None
        for _ in range(len(self.api_hosts)):
            api_host = self._next_host()
            try:
                return self._translate_with_host(text, api_host)
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError(f"All pool endpoints failed: {last_error}") from last_error

    def _next_host(self):
        with self.lock:
            host = self.api_hosts[self.next_index]
            self.next_index = (self.next_index + 1) % len(self.api_hosts)
            return host

    def _translate_with_host(self, text, api_host):
        # Сначала проверяем словарь
        glossary_translation = _translate_from_glossary(text)
        if glossary_translation:
            print(f"[GLOSSARY] {text} -> {glossary_translation}")
            return glossary_translation
        
        # Проверяем, запущен ли API сервер на этом хосте
        if not lms.Client.is_valid_api_host(api_host):
            raise RuntimeError(f"No API server available at {api_host}")
        
        # Получаем модель с указанным хостом
        model = lms.llm(self.model_name, api_host=api_host)
        
        # Создаём чат с системным промптом
        chat = lms.Chat(SYSTEM_PROMPT)
        chat.add_user_message(f"Text: {text}")
        
        # Получаем ответ
        result = model.respond(
            chat,
            config={
                "temperature": 0.1,
                "maxTokens": 1024*10,
            }
        )
        
        translated = result.content.strip()
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

    # Загрузка прогресса
    progress_file = f"{output_file}.progress.pkl"
    start_index = 0
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'rb') as f:
                progress = pickle.load(f)
            if progress.get('file') == output_file:
                start_index = progress.get('task_index', 0)
                print(f"Найден сохранённый прогресс. Продолжаю с задачи {start_index + 1}")
            else:
                print("Прогресс для другого файла, начинаю заново.")
        except Exception as e:
            print(f"Ошибка загрузки прогресса: {e}. Начинаю заново.")

    # Process translations one by one to show logs in real time
    for i in range(start_index, len(tasks)):
        if paused:
            break
        elem, tag, original = tasks[i]
        if translator is None:
            translated = translate_text(original)
        else:
            translated = translator.translate_many([original])[0]
        
        elem.text = translated
        log_msg = f"{tag}: {original}\n->\n{translated}\n"
        print(log_msg, flush=True)
        log_entries.append(log_msg)

        # Сохранение прогресса
        progress = {
            'file': output_file,
            'task_index': i + 1
        }
        with open(progress_file, 'wb') as f:
            pickle.dump(progress, f)

    if not paused:
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

        # Удаление файла прогресса
        if os.path.exists(progress_file):
            os.remove(progress_file)
    else:
        print(f"Прогресс сохранён в {progress_file}. Запустите скрипт снова для продолжения.")

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

    # Настройка обработчика паузы
    keyboard.add_hotkey('pause', pause_handler)

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