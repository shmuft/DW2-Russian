import os
import re
import xml.etree.ElementTree as ET

BASE = r"C:\Users\Ivan\projects\DW2-Russian\1.3.4.3"
RUSSIAN_DIR = os.path.join(BASE, "Russian")
ENGLISH_DIR = os.path.join(BASE, "English")
OUTPUT_FILE = os.path.join(RUSSIAN_DIR, "untranslated_report.txt")

# Tags that should never be translated because they're technical identifiers
TECHNICAL_TAGS = {
    "OrbTypeId", "Category", "ImageFilename", "LargeImageFilename", "FullsizeImageFilename",
    "SurfaceDrawType", "AtmosphereDrawType", "StarColorGradient", "SurfaceMaterialFilenames",
    "AtmosphereMaterialFilenames", "AmbientSoundEffectFilenames", "PossibleResources",
    "CommonBonuses", "LocationEffectId", "AsteroidFieldProbability", "AsteroidFieldOrbTypeId",
    "ChildCountMinimum", "ChildCountMaximum", "QualityRangeMinimum", "QualityRangeMaximum",
    "OrbitalDistanceFromSunRatioMinimum", "OrbitalDistanceFromSunRatioMaximum",
    "DiameterMinimum", "DiameterMaximum", "ResourceCountMinimum", "ResourceCountMaximum",
    "EnergyOutputMinimum", "EnergyOutputMaximum", "StarColorVariationFactor",
    "StarBrightnessFactor", "AmbientLightIntensity", "Factor",
    "StarProbability", "OrbTypeFactor", "ResourcePrevalence",
    "ChildTypes", "Type", "Minimum", "Maximum", "AppearanceChance",
    "Descriptions", "BonusRange", "RingsProbability", "RingsPrimaryColor",
    "RingsColorVariationFactor", "GasGradientPrimaryColor", "GasGradientColorVariationFactor",
    "GasGradientSecondaryColor", "GasGradientSecondaryFactor",
    "SeaLevelMinimum", "SeaLevelMaximum", "MountainFactorMinimum", "MountainFactorMaximum",
    "CloudCoverageMinimum", "CloudCoverageMaximum", "HasGasSurface",
    "GasPerturbationMinimum", "GasPerturbationMaximum",
    "GasEmissivePerturbationMinimum", "GasEmissiveCoverageMinimum", "GasEmissiveCoverageMaximum",
    "GasEmissiveColorVariationFactor",
    "LandSpecularIntensityModifier", "MinimumCityLightLevelOffset",
    "ColorBlendNoiseFactor", "LandscapeGainFactor", "MountainGainFactor",
    "LatitudeColorPerturbationFactor", "LatitudeBlendEnd", "LatitudeMaximumPoint", "LatitudeRange",
    "ModelFilenames", "RuinsModelFilename", "RuinsModelScaleFactor",
    "AmbientSoundEffect", "LandscapeImageFilename", "LandscapeOutpostImageFilename",
    "VagueLandscapeImageFilename",
    "AtmosphereDensity", "CloudColor", "CloudDensity", "CloudStormChance",
    "AltitudeGradient1", "AltitudeGradient2", "LatitudeGradient1", "LatitudeGradient2",
    "OceanGradient", "RingProbability",
    "AtmosphereColor", "StarColor", "StarLightColor", "AmbientLightColor",
    "CityLightColor", "CityLightColor2",
    "AtmosphereTintColor", "GasGradientColor2",
    "R", "G", "B", "A",
    "Prevalence", "ResourceId", "AbundanceMinimum", "AbundanceMaximum",
    "Id", "OrbTypeId", "ChildCountMinimum", "ChildCountMaximum",
    "Value", "EnergyOutput", "Bonus", "Amount",
    "ShipHullId", "ComponentId", "TroopId", "ResourceId",
    "ResearchProjectId", "ArtifactId", "RaceId", "GovernmentId",
    "GameEventId", "ColonyEventId", "FleetTemplateId",
    "SpaceItemId", "TourItemId", "ArmyTemplateId",
    "AltName", "AltDesc", "AltTitle1", "AltTitle2",
    "OrbitalDistance", "Diameter", "ResourceCount",
    "AltName", "AltDescription",
    "QualityRangeMedian",
}

def get_tag(elem):
    tag = elem.tag
    if '}' in tag:
        tag = tag.split('}', 1)[1]
    return tag

def has_cyrillic(text):
    return bool(re.search(r'[а-яА-ЯёЁ]', text))

def is_technical_value(text):
    text = text.strip()
    if not text:
        return True
    # pure numbers
    if re.match(r'^[+-]?\d+(\.\d+)?$', text):
        return True
    # file paths
    if '/' in text and not re.search(r'[а-яА-Я]', text):
        return True
    # file paths with extension
    if re.match(r'^[\w./\\-]+\.\w+$', text):
        return True
    # GUIDs
    if re.match(r'^\{?[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}?$', text):
        return True
    # booleans
    if text.lower() in ('true', 'false'):
        return True
    # single letters/words that are clearly identifiers
    if re.match(r'^[A-Z][a-z]+/[A-Z]', text):
        return True
    # Shader/material paths
    if text.startswith('CoreEffects/') or text.startswith('Shaders/') or text.startswith('Materials/'):
        return True
    # Sound paths
    if text.startswith('Sounds/'):
        return True
    # Environment paths
    if text.startswith('Environment/'):
        return True
    # UI paths
    if text.startswith('UI/'):
        return True
    return False

def collect_texts(elem, path=""):
    """Collect all leaf elements (with text) and their paths."""
    tag = get_tag(elem)
    current_path = f"{path}/{tag}" if path else tag
    
    texts = []
    children = list(elem)
    
    text = (elem.text or "").strip()
    if text and not children:
        texts.append((current_path, tag, text))
    
    for child in children:
        texts.extend(collect_texts(child, current_path))
    
    return texts

def collect_texts_by_index(elem, path="", index_map=None):
    """Collect leaf elements with positional indices for repeated elements."""
    if index_map is None:
        index_map = {}
    
    tag = get_tag(elem)
    
    # Track counts of this tag at current level prefix
    level_key = path
    tag_key = f"{level_key}/{tag}"
    
    if tag_key not in index_map:
        index_map[tag_key] = 0
    index_map[tag_key] += 1
    idx = index_map[tag_key]
    
    current_path = f"{path}/{tag}" if path else tag
    
    texts = []
    children = list(elem)
    
    text = (elem.text or "").strip()
    if text and not children:
        texts.append((current_path, tag, text))
    
    for child in children:
        texts.extend(collect_texts_by_index(child, current_path, index_map))
    
    return texts

def compare_in_order(en_root, ru_root):
    """Compare elements in order to handle repeated elements correctly."""
    results = []
    
    def compare_elements(en_elem, ru_elem, path=""):
        tag = get_tag(en_elem)
        current_path = f"{path}/{tag}" if path else tag
        
        en_text = (en_elem.text or "").strip()
        ru_text = (ru_elem.text or "").strip()
        
        # If both have text and no children, compare
        if en_text and ru_text and not list(en_elem) and not list(ru_elem):
            if en_text != ru_text and not is_technical_value(en_text) and not is_technical_value(ru_text):
                # Different content - check if Russian has Cyrillic (translated)
                if has_cyrillic(ru_text):
                    pass  # translated correctly
                elif en_text == ru_text:
                    pass  # same text, not translated - but this case won't match the != condition
            elif en_text == ru_text and not is_technical_value(en_text):
                # Same non-technical text - UNTRANSLATED
                if has_cyrillic(en_text):
                    pass  # English text has Cyrillic? impossible
                elif en_text:
                    results.append((current_path, en_text))
        
        # Compare children in order
        en_children = list(en_elem)
        ru_children = list(ru_elem)
        
        min_len = min(len(en_children), len(ru_children))
        for i in range(min_len):
            compare_elements(en_children[i], ru_children[i], current_path)
    
    compare_elements(en_root, ru_root)
    return results

def main():
    results = {}
    total_untranslated = 0
    processed = 0
    
    # Walk through Russian directory
    for root_dir, dirs, files in os.walk(RUSSIAN_DIR):
        for filename in files:
            if not filename.endswith('.xml'):
                continue
            
            ru_file_path = os.path.join(root_dir, filename)
            rel_path = os.path.relpath(ru_file_path, RUSSIAN_DIR)
            en_file_path = os.path.join(ENGLISH_DIR, rel_path)
            
            if not os.path.exists(en_file_path):
                print(f"  SKIP (no English): {filename}")
                continue
            
            processed += 1
            file_results = []
            
            try:
                en_tree = ET.parse(en_file_path)
                ru_tree = ET.parse(ru_file_path)
                en_root = en_tree.getroot()
                ru_root = ru_tree.getroot()
            except Exception as e:
                print(f"  ERROR parsing {filename}: {e}")
                continue
            
            # First approach: compare in order (respects XML element order)
            untranslated = compare_in_order(en_root, ru_root)
            
            # Also try: for each leaf element in EN, find matching element in RU
            # (more robust for slightly different structures)
            en_texts = collect_texts(en_root)
            ru_texts = {path: text for path, tag, text in collect_texts(ru_root)}
            
            for path, tag, en_text in en_texts:
                # Skip technical tags
                if tag in TECHNICAL_TAGS:
                    continue
                # Skip technical values
                if is_technical_value(en_text):
                    continue
                # Skip text that already has Cyrillic (translated)
                if has_cyrillic(en_text):
                    continue
                
                ru_text = ru_texts.get(path)
                if ru_text is None:
                    # Tag missing in Russian - could be untranslated or structure differs
                    continue
                
                # If Russian text matches English text exactly, it's untranslated
                if ru_text == en_text:
                    file_results.append((path, en_text))
                elif not has_cyrillic(ru_text) and not is_technical_value(ru_text) and len(ru_text) > 2:
                    # Russian text is still in English but different from ours (maybe partial translation)
                    # Only report if the text looks like English words (has spaces)
                    if ' ' in ru_text or len(ru_text) > 5:
                        file_results.append((path, ru_text))
            
            # Deduplicate by path
            seen = set()
            unique_results = []
            for path, text in file_results:
                if path not in seen:
                    seen.add(path)
                    unique_results.append((path, text))
            
            if unique_results:
                results[filename] = unique_results
                total_untranslated += len(unique_results)
                print(f"  {filename}: {len(unique_results)} untranslated tags")
            else:
                print(f"  {filename}: OK (no untranslated tags)")
    
    # Write output
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for filename, tags in results.items():
            for path, text in tags:
                f.write(f"Имя файла: {filename}\n")
                f.write(f"Путь до недопереведённого тега: {path}\n\n")
    
    print(f"\n{'='*60}")
    print(f"Processed: {processed} files")
    print(f"Files with untranslated tags: {len(results)}")
    print(f"Total untranslated tags: {total_untranslated}")
    print(f"Report saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
