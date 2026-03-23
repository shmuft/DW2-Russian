import argparse
import xml.etree.ElementTree as ET
import requests

def translate_text(text: str) -> str:
    """
    Перевод текста через LM Studio (локальный LLM).
    Требуется запущенный LM Studio с включённым локальным сервером.
    """

#    prompt = (
#        "Translate the following text from English to Russian. "
#        "Keep the style suitable for a sci-fi strategy game. "
#        "Do not add anything extra, only the translation.\n\n"
#        f"Text: {text}"
#    )
    prompt = (
        f"Text: {text}"
    )

    response = requests.post(
        "http://localhost:1234/v1/chat/completions",
        json={
            "model": "qwen/qwen3.5-35b-a3b",  # название модели в LM Studio
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
    )

    result = response.json()
    return result["choices"][0]["message"]["content"].strip()

def translate_xml(input_file: str, output_file: str, tags_to_translate: list):
    tree = ET.parse(input_file)
    root = tree.getroot()

    for tag in tags_to_translate:
        for elem in root.iter(tag):
            if elem.text and elem.text.strip():
                original = elem.text.strip()
                translated = translate_text(original)
                elem.text = translated
                print(f"{tag}: {original} → {translated}")

    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    print(f"\nФайл сохранён: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="CLI‑утилита для перевода указанных тегов в XML‑файле."
    )

    parser.add_argument("input", help="Путь к исходному XML‑файлу")
    parser.add_argument("output", help="Путь к выходному XML‑файлу")
    parser.add_argument(
        "--tags",
        nargs="+",
        required=True,
        help="Список тегов, которые нужно перевести (например: --tags name description)",
    )

    args = parser.parse_args()

    translate_xml(args.input, args.output, args.tags)


if __name__ == "__main__":
    main()