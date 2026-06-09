import xml.etree.ElementTree as ET

# Governments.xml - CabinetTitle
en_gov = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\English\DW2\Governments.xml')
ru_gov = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\Russian\DW2\Governments.xml')

en_govs = en_gov.findall('.//CabinetTitle')
ru_govs = ru_gov.findall('.//CabinetTitle')

print('=== Governments.xml - CabinetTitle ===')
count = 0
for en, ru in zip(en_govs, ru_govs):
    en_t = en.text.strip() if en.text else ''
    ru_t = ru.text.strip() if ru.text else ''
    if en_t == ru_t:
        print(f'  UNTRANSLATED: "{en_t}"')
        count += 1
print(f'  Total: {count}')

# TourItems.xml - Title
en_tour = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\English\DW2\TourItems.xml')
ru_tour = ET.parse(r'C:\Users\Ivan\projects\DW2-Russian\1.3.4.3\Russian\DW2\TourItems.xml')

en_titles = en_tour.findall('.//Title')
ru_titles = ru_tour.findall('.//Title')

print()
print('=== TourItems.xml - Title ===')
count = 0
for en, ru in zip(en_titles, ru_titles):
    en_t = en.text.strip() if en.text else ''
    ru_t = ru.text.strip() if ru.text else ''
    if en_t == ru_t:
        print(f'  UNTRANSLATED: "{en_t}"')
        count += 1
print(f'  Total: {count}')
