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
from pathlib import Path
from openai import OpenAI

try:
    import keyboard
except ImportError:
    print("Библиотека 'keyboard' не установлена. Установите её командой: pip install keyboard")
    sys.exit(1)

DEFAULT_MODEL = "qwen/qwen3.6-35b-a3b"
# DEFAULT_MODEL = "google/gemma-4-26b-a4b"
DEFAULT_POOL_TIMEOUT = 120  # timeout in seconds (was 120000ms)

paused = False

DEFAULT_GLOSSARY_CACHE_FILE = '.glossary_cache.pkl'

# Глобальный кеш для словаря
_glossary_cache = None
_glossary_lock = threading.Lock()

# Глобальный кеш для переводов из уже переведённых файлов
_translation_cache = None
_translation_cache_lock = threading.Lock()

# Локальный приоритетный кэш для конкретного файла
_file_translation_cache = None
_file_translation_cache_lock = threading.Lock()

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


def _get_glossary_cache_path() -> str:
    """Возвращает путь к файлу, где хранится словарь из системного промпта."""
    return os.environ.get('DW2_GLOSSARY_CACHE') or DEFAULT_GLOSSARY_CACHE_FILE


def _get_glossary():
    """Возвращает закешированный словарь (потокобезопасно)."""
    global _glossary_cache
    if _glossary_cache is None:
        with _glossary_lock:
            if _glossary_cache is None:
                cache_file = _get_glossary_cache_path()
                if cache_file and os.path.exists(cache_file):
                    try:
                        with open(cache_file, 'rb') as f:
                            _glossary_cache = pickle.load(f)
                        print(f"[INFO] Загружено {len(_glossary_cache)} требований из кэша словаря")
                    except Exception as exc:
                        print(f"[WARNING] Ошибка загрузки кэша словаря: {exc}")
                        _glossary_cache = None

                if _glossary_cache is None:
                    _glossary_cache = _build_glossary()
                    try:
                        with open(cache_file, 'wb') as f:
                            pickle.dump(_glossary_cache, f)
                        print(f"[INFO] Сохранён кэш словаря в {cache_file} ({len(_glossary_cache)} записей)")
                    except Exception as exc:
                        print(f"[WARNING] Не удалось сохранить кэш словаря: {exc}")

                print(f"[INFO] Использовано {len(_glossary_cache)} требований из системного промпта")
    return _glossary_cache


def _translate_from_glossary(text: str) -> str:
    """
    Проверяет словарь и возвращает перевод, если найден.
    Возвращает None, если перевод не найден.
    """
    glossary = _get_glossary()
    return glossary.get(text.strip())


def set_translation_cache(cache_dict: dict):
    """Устанавливает глобальный кэш переводов из уже переведённых файлов."""
    global _translation_cache
    with _translation_cache_lock:
        _translation_cache = cache_dict if cache_dict else {}
        if _translation_cache:
            print(f"[INFO] Загружено {len(_translation_cache)} переводов в кэш")


def _get_translation_cache() -> dict:
    """Возвращает кэш переводов (потокобезопасно)."""
    global _translation_cache
    if _translation_cache is None:
        with _translation_cache_lock:
            if _translation_cache is None:
                _translation_cache = {}
                # Попытаемся загрузить кэш из переменной окружения
                cache_file = os.environ.get('DW2_TRANSLATION_CACHE')
                if cache_file and os.path.exists(cache_file):
                    try:
                        with open(cache_file, 'rb') as f:
                            _translation_cache = pickle.load(f)
                        print(f"[INFO] Загружено {len(_translation_cache)} переводов из кэша")
                    except Exception as e:
                        print(f"[WARNING] Ошибка загрузки кэша: {e}")
                        _translation_cache = {}
    return _translation_cache


def _read_text_file_lines(file_path: str, encodings=None) -> list[str]:
    """Читает текстовый файл, пробуя несколько кодировок до успешного декодирования."""
    if encodings is None:
        encodings = ['utf-8', 'utf-8-sig', 'cp1252', 'cp1251', 'iso-8859-1']

    last_error = None
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                lines = f.readlines()
            if encoding != 'utf-8':
                print(f"[INFO] Файл {file_path} прочитан в кодировке {encoding}")
            return lines
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    print(f"[WARNING] Файл {file_path} прочитан с заменой недопустимых символов: {last_error}")
    return lines


def _get_file_translation_cache() -> dict:
    """Возвращает локальный приоритетный кэш для текущего файла."""
    global _file_translation_cache
    if _file_translation_cache is None:
        with _file_translation_cache_lock:
            if _file_translation_cache is None:
                _file_translation_cache = {}
                cache_file = os.environ.get('DW2_FILE_TRANSLATION_CACHE')
                if cache_file and os.path.exists(cache_file):
                    try:
                        with open(cache_file, 'rb') as f:
                            _file_translation_cache = pickle.load(f)
                        print(f"[INFO] Загружено {len(_file_translation_cache)} переводов из локального кэша")
                    except Exception as e:
                        print(f"[WARNING] Ошибка загрузки локального кэша: {e}")
                        _file_translation_cache = {}
    return _file_translation_cache


def _translate_from_cache(text: str) -> str:
    """
    Проверяет кэш переводов и возвращает перевод, если найден.
    Возвращает None, если перевод не найден.
    """
    cache = _get_translation_cache()
    return cache.get(text.strip())


def _translate_from_file_cache(text: str) -> str:
    """Проверяет локальный кэш конкретного файла перед общим словарём и кэшем."""
    cache = _get_file_translation_cache()
    return cache.get(text.strip())

def translate_text(text: str) -> str:
    """
    Перевод текста через LM Studio (локальный LLM) используя lmstudio-python SDK.
    Проверяет в порядке приоритета:
    1. Локальный кэш конкретного файла
    2. Словарь из SYSTEM_PROMPT
    3. Общий кэш из уже переведённых файлов
    4. LLM (Qwen)
    """

    file_cache_translation = _translate_from_file_cache(text)
    if file_cache_translation:
        print(f"[FILE_CACHE] {text} -> {file_cache_translation}")
        return file_cache_translation

    glossary_translation = _translate_from_glossary(text)
    if glossary_translation:
        print(f"[GLOSSARY] {text} -> {glossary_translation}")
        return glossary_translation

    cache_translation = _translate_from_cache(text)
    if cache_translation:
        print(f"[CACHE] {text} -> {cache_translation}")
        return cache_translation

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
    translated = translated.replace('-', '-')
    translated = translated.replace('\n', '\\n')

    return translated


class LocalTranslator:
    def translate_many(self, texts):
        return [translate_text(text) for text in texts]

class FixNewLinesTranslator:
    def translate_many(self, texts):
        return [self._replace_new_lines(text) for text in texts]

    def _replace_new_lines(self, text):
        return text.replace('\n', '\\n')

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
        file_cache_translation = _translate_from_file_cache(text)
        if file_cache_translation:
            print(f"[FILE_CACHE] {text} -> {file_cache_translation}")
            return file_cache_translation

        glossary_translation = _translate_from_glossary(text)
        if glossary_translation:
            print(f"[GLOSSARY] {text} -> {glossary_translation}")
            return glossary_translation

        cache_translation = _translate_from_cache(text)
        if cache_translation:
            print(f"[CACHE] {text} -> {cache_translation}")
            return cache_translation
        
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
        translated = translated.replace('‑', '-')
        translated = translated.replace('-', '-')
        translated = translated.replace('\n', '\\n')

        return translated


def iter_with_path(elem, path=''):
    current_path = f"{path}/{elem.tag}" if path else elem.tag
    yield elem, current_path
    for child in elem:
        yield from iter_with_path(child, current_path)


def iter_translatable_elements(root, skip_technical=False):
    """
    Итерирует все переводимые элементы XML в том же порядке,
    что и в translate_xml/check_translated_file/collect_words_and_tasks.
    Возвращает (elem, tag, tag_desc) кортежи.
    skip_technical=True пропускает технические теги (Type, RaceId и т.д.)
    """
    technical_tags = {'Type', 'ImageFilename', 'AppliesTo', 'ArtifactId', 'DiscoveryLevel',
                      'BonusesOnlyWhenAtColony', 'BonusesOnlyWhenAtCapital', 'PsychicResistance',
                      'RaceId', 'Amount'}

    for elem in root.iter():
        elem_tag = elem.tag
        if elem_tag == 'Artifact':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'GameEvent':
            for action in elem.findall('.//TriggerActions/GameEventAction'):
                for tag in ['MessageTitle', 'Description', 'ChoiceButtonText']:
                    for child in action.iter(tag):
                        if skip_technical and child.tag in technical_tags:
                            continue
                        yield (child, tag, f"{elem_tag}/TriggerActions/GameEventAction/{tag}")
            for action in elem.findall('.//PlacementActions/GameEventAction'):
                if action.find('Type').text in ['BuildFacility', 'GeneratePlanetarySystem', 'GenerateIndependentColony', 'GenerateAbandonedShipBase']:
                    for child in action.iter('GeneratedItemName'):
                        yield (child, 'GeneratedItemName', f"{elem_tag}/PlacementActions/GameEventAction/GeneratedItemName")
                for tag in ['MessageTitle', 'Description']:
                    for child in action.iter(tag):
                        if skip_technical and child.tag in technical_tags:
                            continue
                        yield (child, tag, f"{elem_tag}/PlacementActions/GameEventAction/{tag}")
            for tag in ['Title', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'PlanetaryFacilityDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'Race':
            for tag in ['DescriptionBonuses', 'DescriptionObjective', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
            for string_elem in elem.findall('.//FeatureExplanations/string'):
                yield (string_elem, 'string', f"{elem_tag}/FeatureExplanations/string")
            for string_elem in elem.findall('.//DesignNames/string'):
                yield (string_elem, 'string', f"{elem_tag}/DesignNames/string")
            for string_elem in elem.findall('.//CharacterFirstNames/string'):
                yield (string_elem, 'string', f"{elem_tag}/CharacterFirstNames/string")
            for string_elem in elem.findall('.//CharacterLastNames/string'):
                yield (string_elem, 'string', f"{elem_tag}/CharacterLastNames/string")
        elif elem_tag == 'ResearchProjectDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'ShipHull':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'TroopDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'ArmyTemplate':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'ColonyEventDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'ComponentDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'FleetTemplate':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'Government':
            for tag in ['LeaderTitle', 'Name', 'Description', 'string', 'Title']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'OrbType':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
            for string_elem in elem.findall('.//CommonBonuses/BonusRange/Descriptions/string'):
                yield (string_elem, 'string', f"{elem_tag}/CommonBonuses/BonusRange/Descriptions/string")
            for string_elem in elem.findall('.//RuinLocationDescriptions/string'):
                yield (string_elem, 'string', f"{elem_tag}/RuinLocationDescriptions/string")
        elif elem_tag == 'Resource':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'SpaceItemDefinition':
            for tag in ['Name', 'Description']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")
        elif elem_tag == 'TourItem':
            for tag in ['StepTitle', 'MarkupText', 'Title']:
                for child in elem.iter(tag):
                    if skip_technical and child.tag in technical_tags:
                        continue
                    yield (child, tag, f"{elem_tag}/{tag}")


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

    for elem, tag, tag_desc in iter_translatable_elements(root, skip_technical=True):
        if elem.text and elem.text.strip() and not should_ignore_text(elem.text):
            found.append((tag_desc, elem.text.strip()))

    if not found:
        print(f"Проверка пройдена: английских слов в {file_path} не найдено.")
        return 0

    print(f"Найдено {len(found)} строк с английскими символами в {file_path}:")
    for path, text in found:
        try:
            print(f"[{path}] {text}")
        except UnicodeEncodeError:
            # Если есть проблемы с кодировкой, выводим без текста
            print(f"[{path}] [Текст содержит неподдерживаемые символы]")
    return 1


def check_translated_txt_file(file_path: str) -> int:
    """
    Проверяет текстовый файл на наличие английских слов.
    """
    english_re = re.compile(r'[A-Za-z]')
    found = []

    try:
        lines = _read_text_file_lines(file_path)
        
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if line and english_re.search(line):
                # Проверяем, не является ли это технической строкой
                if not _is_technical_string(line):
                    found.append((i, line))
    except Exception as e:
        print(f"Ошибка чтения файла {file_path}: {e}")
        return 2

    if not found:
        print(f"Проверка пройдена: английских слов в {file_path} не найдено.")
        return 0

    print(f"Найдено {len(found)} строк с английскими символами в {file_path}:")
    for line_num, text in found:
        try:
            print(f"[Line {line_num}] {text}")
        except UnicodeEncodeError:
            print(f"[Line {line_num}] [Текст содержит неподдерживаемые символы]")
    return 1


def translate_txt_file(input_file: str, output_file: str, words_mode=False, translator=None):
    """
    Переводит текстовый файл построчно.
    Определяет формат: если строка содержит ';', переводит только часть после ';',
    иначе переводит всю строку.
    Поддерживает паузу и продолжение.
    """
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

    try:
        lines = _read_text_file_lines(input_file)
    except Exception as e:
        print(f"Ошибка чтения файла {input_file}: {e}")
        return

    translated_lines = []
    log_entries = []
    words_set = set()

    # Если продолжаем, загружаем уже переведённые строки из output_file
    if start_index > 0 and os.path.exists(output_file):
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                existing_lines = f.readlines()
            translated_lines = existing_lines
        except Exception as e:
            print(f"Ошибка чтения существующего файла {output_file}: {e}")
            translated_lines = []

    # Если уже всё переведено, завершить
    if start_index >= len(lines):
        print(f"Перевод уже завершён для {output_file}")
        if os.path.exists(progress_file):
            os.remove(progress_file)
        return
    _get_translation_cache() 
    for i in range(start_index, len(lines)):
        if paused:
            break
        line = lines[i]
        original = line.strip()
        if not original:
            translated_lines.append(line)  # Пустые строки оставляем как есть
            continue

        # Определяем, нужно ли переводить всю строку или только часть после ';'
        if ';' in original:
            # Формат: ключ ; текст
            parts = original.split(';', 1)
            key_part = parts[0]
            text_to_translate = parts[1].strip()
            if not text_to_translate:
                translated_lines.append(line)
                continue
            # Переводим только текст после ';'
            if words_mode:
                words_set.add(text_to_translate)
            else:
                if translator is None:
                    translated = translate_text(text_to_translate)
                else:
                    translated = translator.translate_many([text_to_translate])[0]
                _translation_cache[text_to_translate] = translated
                new_line = f"{key_part};{translated}\n"
                translated_lines.append(new_line)
                log_msg = f"Line {i+1}: {text_to_translate}\n->\n{translated}\n"
                print(log_msg)
                log_entries.append(log_msg)
        else:
            # Переводим всю строку (как в Hints.txt)
            if words_mode:
                words_set.add(original)
            else:
                if translator is None:
                    translated = translate_text(original)
                else:
                    translated = translator.translate_many([original])[0]
                _translation_cache[original] = translated
                translated_lines.append(translated + '\n')
                log_msg = f"Line {i+1}: {original}\n->\n{translated}\n"
                print(log_msg)
                log_entries.append(log_msg)

        # Сохранение прогресса
        progress = {
            'file': output_file,
            'task_index': i + 1
        }
        with open(progress_file, 'wb') as f:
            pickle.dump(progress, f)

    if words_mode:
        words_file = output_file.replace('.txt', '_words.txt')
        with open(words_file, 'w', encoding='utf-8') as f:
            for word in sorted(words_set):
                f.write(word + "\n")
        print(f"Список слов для словаря сохранён: {words_file}")
        return

    if not paused:
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.writelines(translated_lines)
            print(f"\nФайл сохранён: {output_file}")
            
            # Save log file
            log_file = output_file.replace('.txt', '_log.txt')
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
        except Exception as e:
            print(f"Ошибка сохранения файла {output_file}: {e}")
    else:
        # При паузе сохраняем текущее состояние
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.writelines(translated_lines)
            print(f"Прогресс сохранён в {progress_file}. Запустите скрипт снова для продолжения.")
        except Exception as e:
            print(f"Ошибка сохранения прогресса: {e}")


def translate_file(input_file: str, output_file: str, file_type: str, words_mode=False, translator=None, fix_untranslated=False, fix_newlines=False):
    """
    Универсальная функция для перевода файла в зависимости от типа.
    """
    if file_type == 'xml':
        translate_xml(input_file, output_file, words_mode, translator, fix_untranslated, fix_newlines)
    elif file_type == 'txt':
        if fix_untranslated:
            print("Режим fix_untranslated не поддерживается для TXT файлов")
            return
        if fix_newlines:
            print("Режим fix_newlines не поддерживается для TXT файлов")
            return
        translate_txt_file(input_file, output_file, words_mode, translator)
    else:
        print(f"Неподдерживаемый тип файла: {file_type}")
        return


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


def build_translation_cache_from_files(russian_dir_path: str) -> dict:
    """
    Сканирует уже переведённые файлы в русской папке и собирает кэш переводов.
    Возвращает словарь {english_text: russian_text}
    """
    cache = {}
    russian_dir = Path(russian_dir_path)
    
    if not russian_dir.exists():
        print(f"[WARNING] Русская папка не найдена: {russian_dir_path}")
        return cache
    
    # Найтём все XML файлы в русской папке
    xml_files = list(russian_dir.rglob('*.xml'))
    print(f"[INFO] Сканирование {len(xml_files)} переведённых файлов для кэша...")
    
    for xml_file in xml_files:
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            # Извлекаем все переводы из тегов
            for elem in root.iter():
                elem_tag = elem.tag
                if elem.text and elem.text.strip():
                    # Это простой подход - мы добавляем русский текст в кэш
                    # но нам нужна пара английский->русский
                    # Это будет сделано в batch_translate.py путём сравнения файлов
                    pass
        except Exception as e:
            print(f"[WARNING] Ошибка обработки файла {xml_file}: {e}")
            continue
    
    return cache


def _is_technical_string(text: str) -> bool:
    """
    Проверяет, является ли строка технической информацией (путь к файлу, ID и т.д.).
    Такие строки не должны быть в кэше переводов.
    """
    if not text:
        return False

    text = text.strip()

    # Пути к файлам содержат слэши
    if '/' in text or '\\' in text:
        return True

    # Идентификаторы и служебные имена часто содержат подчеркивание, цифры или
    # выглядят как CamelCase/UPPERCASE. Но обычные имена персонажей/рас (
    # например: Sukantu, Wekkarus, ResearchAll) не должны считаться техническими.
    if ' ' not in text and all(c.isalnum() or c in '._-' for c in text):
        if '_' in text or any(c.isdigit() for c in text):
            return True

        uppercase_count = sum(c.isupper() for c in text)
        if uppercase_count >= 2:
            return True

        if text.isupper() and len(text) > 1:
            return True

    return False


def extract_translatable_texts(root):
    """
    Извлекает все переводимые тексты из XML дерева в том же порядке,
    что и в translate_xml. Возвращает список кортежей (text, tag_description).
    """
    texts = []
    for elem, tag, tag_desc in iter_translatable_elements(root):
        text = elem.text.strip() if elem.text else ''
        if text:
            texts.append((text, tag_desc))
    return texts


def resolve_russian_file_path(english_file: Path, english_dir: Path, russian_dir: Path) -> Path:
    """Возвращает корректный путь к русскому файлу, учитывая переведённые имена Galactopedia."""
    rel_path = english_file.relative_to(english_dir)
    candidate = russian_dir / rel_path

    if candidate.exists():
        return candidate

    if 'Galactopedia' in str(rel_path):
        translated_stem = translate_text(rel_path.stem + rel_path.suffix)
        translated_rel_path = rel_path.with_name(translated_stem)
        translated_candidate = russian_dir / translated_rel_path
        if translated_candidate.exists():
            return translated_candidate

    return candidate


def build_translation_cache_from_paired_files(english_dir_path: str, russian_dir_path: str, exclude_files: set = None) -> dict:
    """
    Сравнивает английские и русские файлы и собирает кэш переводов.
    Пропускает файлы, которые находятся в процессе перевода (указаны в exclude_files).
    Проверяет, что каждый перевод отличается от оригинала (т.е. отсутствуют недопереведённые элементы).
    Возвращает словарь {english_text: russian_text}
    """
    cache = {}
    english_dir = Path(english_dir_path)
    russian_dir = Path(russian_dir_path)
    
    if exclude_files is None:
        exclude_files = set()
    
    if not english_dir.exists():
        print(f"[WARNING] Английская папка не найдена: {english_dir_path}")
        return cache
    
    if not russian_dir.exists():
        print(f"[WARNING] Русская папка не найдена: {russian_dir_path}")
        return cache
    
    # Найдём все XML файлы в английской папке
    xml_files = list(english_dir.rglob('*.xml'))
    print(f"[INFO] Сканирование xml {len(xml_files)} файлов для построения кэша переводов...")
    
    files_processed = 0
    problems_found = []
    technical_strings_skipped = 0
    
    for eng_file in xml_files:
        # Вычислим соответствующий русский файл
        rus_file = resolve_russian_file_path(eng_file, english_dir, russian_dir)
        rel_path = eng_file.relative_to(english_dir)
        
        # Пропускаем файлы в процессе перевода
        if str(rus_file) in exclude_files:
            continue
        
        if not rus_file.exists():
            continue
        
        try:
            # Парсим оба файла
            eng_tree = ET.parse(eng_file)
            eng_root = eng_tree.getroot()
            rus_tree = ET.parse(rus_file)
            rus_root = rus_tree.getroot()
            
            # Добавляем перевод имени файла, если в русской версии оно уже отличается.
            eng_filename = rel_path.name
            rus_filename = rus_file.name
            if eng_filename and rus_filename and eng_filename != rus_filename:
                cache[eng_filename] = rus_filename

            # Извлекаем переводимые тексты из обоих файлов
            eng_texts = extract_translatable_texts(eng_root)
            rus_texts = extract_translatable_texts(rus_root)
            
            # Сопоставляем тексты по порядку
            for (eng_text, tag_desc), (rus_text, _) in zip(eng_texts, rus_texts):
                # Пропускаем технические строки
                if _is_technical_string(eng_text):
                    technical_strings_skipped += 1
                    continue
                
                # Если русский текст равен английскому - это недопереведённый элемент
                if rus_text == eng_text:
                    problem_msg = f"[PROBLEM] Недопереводённый элемент в {rus_file}: <{tag_desc.split('/')[-1]}>{eng_text}</{tag_desc.split('/')[-1]}>"
                    print(problem_msg)
                    problems_found.append(problem_msg)
                    # Не добавляем в кэш!
                else:
                    # Добавляем только правильно переведённые элементы
                    cache[eng_text] = rus_text
            
            files_processed += 1
            if files_processed % 10 == 0:
                print(f"  Обработано {files_processed} файлов, кэш: {len(cache)} записей")
        
        except Exception as e:
            print(f"[WARNING] Ошибка обработки пары файлов {eng_file} / {rus_file}: {e}")
            continue
    
    txt_files = list(english_dir.rglob('*.txt'))
    print(f"[INFO] Сканирование txt {len(txt_files)} файлов для построения кэша переводов...")
    for eng_file in txt_files:
        # Вычислим соответствующий русский файл
        rus_file = resolve_russian_file_path(eng_file, english_dir, russian_dir)
        rel_path = eng_file.relative_to(english_dir)
        
        # Пропускаем файлы в процессе перевода
        if str(rus_file) in exclude_files:
            continue
        
        if not rus_file.exists():
            continue
        
        try:
            # Для *.txt строим кэш по строкам, без XML-парсинга.
            try:
                eng_lines = _read_text_file_lines(eng_file)
            except Exception as e:
                print(f"Ошибка чтения файла {eng_file}: {e}")
                continue

            try:
                rus_lines = _read_text_file_lines(rus_file)
            except Exception as e:
                print(f"Ошибка чтения файла {rus_file}: {e}")
                continue

            line_count = min(len(eng_lines), len(rus_lines))
            if len(eng_lines) != len(rus_lines):
                print(f"[WARNING] Разное число строк в {eng_file} и {rus_file}: {len(eng_lines)} vs {len(rus_lines)}")

            for i in range(line_count):
                eng_line = eng_lines[i].rstrip('\n')
                rus_line = rus_lines[i].rstrip('\n')

                eng_original = eng_line.strip()
                if not eng_original:
                    continue

                rus_original = rus_line.strip()
                if not rus_original:
                    continue

                # Добавляем перевод имени файла, если это файл Galactopedia и имя уже отличается.
                eng_filename = rel_path.name
                rus_filename = rus_file.name
                if eng_filename and rus_filename and eng_filename != rus_filename:
                    cache[eng_filename] = rus_filename

                if ';' in eng_original:
                    eng_parts = eng_original.split(';', 1)
                    eng_text = eng_parts[1].strip()

                    rus_parts = rus_original.split(';', 1)
                    rus_text = rus_parts[1].strip() if len(rus_parts) > 1 else rus_original
                else:
                    eng_text = eng_original
                    rus_text = rus_original

                if rus_text == eng_text:
                    problem_msg = f"[PROBLEM] Недопереводённый элемент в {rus_file}: {eng_text}"
                    print(problem_msg)
                    problems_found.append(problem_msg)
                else:
                    cache[eng_text] = rus_text

            files_processed += 1
            if files_processed % 10 == 0:
                print(f"  Обработано {files_processed} файлов, кэш: {len(cache)} записей")
        
        except Exception as e:
            print(f"[WARNING] Ошибка обработки пары файлов {eng_file} / {rus_file}: {e}")
            continue

    # Выводим итоги проблем
    if problems_found:
        print(f"\n[ERROR] Найдено {len(problems_found)} недопереведённых элементов:")
        for problem in problems_found[:20]:  # Показываем первые 20
            print(f"  {problem}")
        if len(problems_found) > 20:
            print(f"  ... и ещё {len(problems_found) - 20} проблем")
        print("[ERROR] Эти элементы исключены из кэша для пересортировки!")
    
    if technical_strings_skipped > 0:
        print(f"[INFO] Пропущено {technical_strings_skipped} технических строк (пути, ID и т.д.)")
    
    print(f"[INFO] Построено {len(cache)} переводов в кэше из {files_processed} файлов")
    return cache

def translate_xml(input_file: str, output_file: str, words_mode=False, translator=None, fix_untranslated=False, fix_newlines=False):
    # Загрузка прогресса ДО парсинга файла
    progress_file = f"{output_file}.progress.pkl"
    start_index = 0
    source_file = input_file  # По умолчанию парсим исходный файл
    
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'rb') as f:
                progress = pickle.load(f)
            if progress.get('file') == output_file:
                start_index = progress.get('task_index', 0)
                # Если выходной файл существует, загружаем его вместо исходного
                # чтобы получить уже сделанные переводы
                if os.path.exists(output_file):
                    source_file = output_file
                    print(f"Загружаю состояние с частичными переводами из {output_file}")
                print(f"Найден сохранённый прогресс. Продолжаю с задачи {start_index + 1}")
            else:
                print("Прогресс для другого файла, начинаю заново.")
        except Exception as e:
            print(f"Ошибка загрузки прогресса: {e}. Начинаю заново.")

    # В режиме fix_untranslated нужно сопоставлять с английским файлом
    english_texts = []
    if fix_untranslated:
        try:
            eng_tree = ET.parse(input_file)
            eng_root = eng_tree.getroot()
            english_texts = extract_translatable_texts(eng_root)
        except Exception as e:
            print(f"Ошибка загрузки английского файла {input_file}: {e}")
            return

    # В режиме fix_untranslated специальная обработка
    if fix_untranslated and os.path.exists(output_file):
        print(f"Режим доперевода: исправляю недопереведённые элементы в {output_file}")
        
        # Загружаем оба файла
        eng_tree = ET.parse(input_file)
        eng_root = eng_tree.getroot()
        rus_tree = ET.parse(output_file)
        rus_root = rus_tree.getroot()
        
        # Получаем списки текстов
        eng_texts = extract_translatable_texts(eng_root)
        rus_texts = extract_translatable_texts(rus_root)
        
        _get_translation_cache() 

        # Ищем недопереведённые элементы и переводим их
        log_entries = []
        fixed_count = 0
        
        for i, ((eng_text, eng_tag), (rus_text, rus_tag)) in enumerate(zip(eng_texts, rus_texts)):
            if eng_tag == rus_tag and eng_text.strip() and rus_text.strip():
                if rus_text == eng_text:
                    # Найден недопереведённый элемент, переводим
                    translated = translate_text(eng_text)
                    _translation_cache[eng_text] = translated

                    # Теперь нужно найти и заменить соответствующий элемент в русском дереве
                    # Это сложно реализовать точно, поэтому просто пересохраним файл
                    print(f"Перевод: {eng_text} -> {translated}")
                    fixed_count += 1
                else:
                    _translation_cache[eng_text] = rus_text

        print(f"Добавлено {fixed_count} переводов в кэш")
    
    if (fix_newlines):
        tree = ET.parse(output_file)
    else:
        tree = ET.parse(source_file)
    root = tree.getroot()
    log_entries = []
    words_set = set()
    tasks = []

    for elem, tag, tag_desc in iter_translatable_elements(root):
        original = elem.text.strip() if elem.text else ''
        if not original:
            continue
        if words_mode:
            words_set.add(original)
        else:
            tasks.append((elem, tag_desc, original))

    if words_mode:
        words_file = output_file.replace('.xml', '_words.txt')
        with open(words_file, 'w', encoding='utf-8') as f:
            for word in sorted(words_set):
                f.write(word + "\n")
        print(f"Список слов для словаря сохранён: {words_file}")
        return
    
    _get_translation_cache()

    # Process translations one by one to show logs in real time
    for i in range(start_index, len(tasks)):
        if paused:
            break
        elem, tag, original = tasks[i]
        if translator is None:
            translated = translate_text(original)
        else:
            translated = translator.translate_many([original])[0]

        if original.strip() and _translation_cache.get(original, '') == '':
            _translation_cache[original] = translated
        
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
        # При паузе сохраняем текущее состояние XML с уже сделанными переводами
        tree.write(output_file, encoding="utf-8", xml_declaration=True)
        print(f"Прогресс сохранён в {progress_file}. Запустите скрипт снова для продолжения.")

def main():
    parser = argparse.ArgumentParser(
        description="CLI‑утилита для перевода XML или TXT файла."
    )

    parser.add_argument(
        '--type',
        choices=['xml', 'txt'],
        default='xml',
        help='Тип файла для перевода (xml или txt)'
    )
    parser.add_argument("input", help="Путь к исходному файлу")
    parser.add_argument("output", nargs='?', help="Путь к выходному файлу (необязательно для --check)")
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
        '--fix-untranslated',
        action='store_true',
        help='Доперевести только недопереведённые элементы (где русский текст равен английскому)'
    )
    parser.add_argument(
        '--fix-newlines',
        action='store_true',
        help='Постобработка: заменить переносы строк на \\n в переведённых XML файлах (для директории вместо input/output)'
    )

    args = parser.parse_args()

    if args.check:
        if args.words:
            parser.error('--check и --words нельзя использовать одновременно')
        if args.type == 'xml':
            return check_translated_file(args.input)
        elif args.type == 'txt':
            return check_translated_txt_file(args.input)
        else:
            parser.error(f'Неподдерживаемый тип файла для проверки: {args.type}')

    # if args.fix_newlines:
    #     if args.type != 'xml':
    #         parser.error('--fix-newlines работает только с XML файлами')
    #     print(f"Постобработка переносов в директории: {args.input}")
    #     fix_newlines_in_directory(args.input)
    #     return 0

    if args.output is None:
        parser.error('output обязателен, если не задан --check')

    # Настройка обработчика паузы
    keyboard.add_hotkey('pause', pause_handler)

    pool_addresses = []
    if args.pool:
        for chunk in args.pool:
            pool_addresses.extend([addr.strip() for addr in chunk.split(',') if addr.strip()])

    translator = None
    if pool_addresses and not args.fix_newlines:
        translator = RemotePoolTranslator(pool_addresses, timeout=args.pool_timeout)

    if args.fix_newlines:
        translator = FixNewLinesTranslator()

    translate_file(args.input, args.output, args.type, words_mode=args.words, translator=translator, fix_untranslated=args.fix_untranslated if args.type == 'xml' else False, fix_newlines=args.fix_newlines)

if __name__ == "__main__":
    sys.exit(main())