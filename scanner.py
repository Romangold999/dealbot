import re
import logging
import sys
import time
from pathlib import Path
from urllib.parse import quote

VENDOR = Path(__file__).parent / "vendor" / "Marketplace-Parser"
sys.path.insert(0, str(VENDOR))

from parsers import ozon  # noqa: E402

log = logging.getLogger("dealbot.scanner")


# Wide automatic searches across the main product departments. Marketplace search
# pages return samples; this is a broader seed list, not a full catalog export.
AUTO_SEARCH_GROUPS = {
    "Р­Р»РµРєС‚СЂРѕРЅРёРєР°": (
        "СЃРјР°СЂС‚С„РѕРЅС‹", "РїР»Р°РЅС€РµС‚С‹", "РЅРѕСѓС‚Р±СѓРєРё", "РєРѕРјРїСЊСЋС‚РµСЂРЅС‹Рµ РјРѕРЅРёС‚РѕСЂС‹", "С‚РµР»РµРІРёР·РѕСЂС‹",
        "РЅР°СѓС€РЅРёРєРё", "СЃРјР°СЂС‚-С‡Р°СЃС‹", "С„РѕС‚РѕР°РїРїР°СЂР°С‚С‹", "РєРѕРјРїСЊСЋС‚РµСЂРЅС‹Рµ РєРѕРјРїР»РµРєС‚СѓСЋС‰РёРµ",
        "РєРѕРјРїСЊСЋС‚РµСЂРЅР°СЏ РїРµСЂРёС„РµСЂРёСЏ", "РёРіСЂРѕРІС‹Рµ РїСЂРёСЃС‚Р°РІРєРё", "СЂРѕСѓС‚РµСЂС‹", "СЌР»РµРєС‚СЂРѕС‚РѕРІР°СЂС‹", "СЌР»РµРєС‚СЂРѕРЅРёРєР°",
    ),
    "Р‘С‹С‚РѕРІР°СЏ С‚РµС…РЅРёРєР°": (
        "Р±С‹С‚РѕРІР°СЏ С‚РµС…РЅРёРєР°", "РєСЂСѓРїРЅР°СЏ Р±С‹С‚РѕРІР°СЏ С‚РµС…РЅРёРєР°", "С…РѕР»РѕРґРёР»СЊРЅРёРєРё", "СЃС‚РёСЂР°Р»СЊРЅС‹Рµ РјР°С€РёРЅС‹",
        "РїРѕСЃСѓРґРѕРјРѕРµС‡РЅС‹Рµ РјР°С€РёРЅС‹", "РїС‹Р»РµСЃРѕСЃС‹", "СЂРѕР±РѕС‚С‹-РїС‹Р»РµСЃРѕСЃС‹", "РєРѕС„РµРјР°С€РёРЅС‹",
        "РјРёРєСЂРѕРІРѕР»РЅРѕРІС‹Рµ РїРµС‡Рё", "РєРѕРЅРґРёС†РёРѕРЅРµСЂС‹", "С‚РµС…РЅРёРєР° РґР»СЏ РєСѓС…РЅРё", "С„РµРЅС‹ Рё СЃС‚Р°Р№Р»РµСЂС‹",
    ),
    "РћРґРµР¶РґР° Рё РѕР±СѓРІСЊ": (
        "РѕРґРµР¶РґР° Р¶РµРЅСЃРєР°СЏ", "РѕРґРµР¶РґР° РјСѓР¶СЃРєР°СЏ", "РѕРґРµР¶РґР° РґРµС‚СЃРєР°СЏ", "РїР»Р°С‚СЊСЏ", "РєСѓСЂС‚РєРё",
        "РґР¶РёРЅСЃС‹", "С„СѓС‚Р±РѕР»РєРё", "СЃРІРёС‚РµСЂР°", "РЅРёР¶РЅРµРµ Р±РµР»СЊРµ", "СЃРїРѕСЂС‚РёРІРЅР°СЏ РѕРґРµР¶РґР°",
        "РѕР±СѓРІСЊ Р¶РµРЅСЃРєР°СЏ", "РѕР±СѓРІСЊ РјСѓР¶СЃРєР°СЏ", "РєСЂРѕСЃСЃРѕРІРєРё", "Р±РѕС‚РёРЅРєРё", "СЃСѓРјРєРё", "Р°РєСЃРµСЃСЃСѓР°СЂС‹",
    ),
    "Р”РѕРј Рё РјРµР±РµР»СЊ": (
        "С‚РѕРІР°СЂС‹ РґР»СЏ РґРѕРјР°", "РјРµР±РµР»СЊ", "РїРѕСЃСѓРґР°", "РїРѕСЃС‚РµР»СЊРЅРѕРµ Р±РµР»СЊРµ", "РґРѕРјР°С€РЅРёР№ С‚РµРєСЃС‚РёР»СЊ",
        "СЃРІРµС‚РёР»СЊРЅРёРєРё", "РґРµРєРѕСЂ РґР»СЏ РґРѕРјР°", "С‚РѕРІР°СЂС‹ РґР»СЏ СѓР±РѕСЂРєРё", "С‚РѕРІР°СЂС‹ РґР»СЏ С…СЂР°РЅРµРЅРёСЏ", "РјР°С‚СЂР°СЃС‹",
    ),
    "РљСЂР°СЃРѕС‚Р°": (
        "РєРѕСЃРјРµС‚РёРєР°", "РїР°СЂС„СЋРјРµСЂРёСЏ", "СѓС…РѕРґ Р·Р° РєРѕР¶РµР№", "СѓС…РѕРґ Р·Р° РІРѕР»РѕСЃР°РјРё", "РјР°РЅРёРєСЋСЂ",
        "СЌР»РµРєС‚СЂРёС‡РµСЃРєРёРµ Р·СѓР±РЅС‹Рµ С‰РµС‚РєРё", "Р±СЂРёС‚РІС‹ Рё С‚СЂРёРјРјРµСЂС‹",
    ),
    "Р”РµС‚СЃРєРёРµ С‚РѕРІР°СЂС‹": (
        "РґРµС‚СЃРєРёРµ С‚РѕРІР°СЂС‹", "РґРµС‚СЃРєРёРµ РёРіСЂСѓС€РєРё", "РґРµС‚СЃРєР°СЏ РѕРґРµР¶РґР°", "РєРѕР»СЏСЃРєРё", "Р°РІС‚РѕРєСЂРµСЃР»Р°",
        "С‚РѕРІР°СЂС‹ РґР»СЏ РЅРѕРІРѕСЂРѕР¶РґРµРЅРЅС‹С…",
    ),
    "РЎРїРѕСЂС‚ Рё РѕС‚РґС‹С…": (
        "С‚РѕРІР°СЂС‹ РґР»СЏ СЃРїРѕСЂС‚Р°", "С‚СЂРµРЅР°Р¶РµСЂС‹", "РІРµР»РѕСЃРёРїРµРґС‹", "С‚РѕРІР°СЂС‹ РґР»СЏ С‚СѓСЂРёР·РјР°", "РєРµРјРїРёРЅРі",
        "СЂС‹Р±Р°Р»РєР°", "СЃР°РјРѕРєР°С‚С‹", "С‚РѕРІР°СЂС‹ РґР»СЏ РїР»Р°РІР°РЅРёСЏ",
    ),
    "РђРІС‚Рѕ Рё РёРЅСЃС‚СЂСѓРјРµРЅС‚С‹": (
        "Р°РІС‚РѕС‚РѕРІР°СЂС‹", "Р°РІС‚РѕР°РєСЃРµСЃСЃСѓР°СЂС‹", "С€РёРЅС‹", "РёРЅСЃС‚СЂСѓРјРµРЅС‚С‹", "СЌР»РµРєС‚СЂРѕРёРЅСЃС‚СЂСѓРјРµРЅС‚С‹",
        "С‚РѕРІР°СЂС‹ РґР»СЏ СЂРµРјРѕРЅС‚Р°", "Р°РІС‚РѕСЌР»РµРєС‚СЂРѕРЅРёРєР°",
    ),
    "Р—РґРѕСЂРѕРІСЊРµ": (
        "С‚РѕРІР°СЂС‹ РґР»СЏ Р·РґРѕСЂРѕРІСЊСЏ", "РјР°СЃСЃР°Р¶РµСЂС‹", "РјРµРґРёС†РёРЅСЃРєРёРµ РїСЂРёР±РѕСЂС‹", "РѕСЂС‚РѕРїРµРґРёС‡РµСЃРєРёРµ С‚РѕРІР°СЂС‹",
    ),
    "Р—РѕРѕС‚РѕРІР°СЂС‹": (
        "С‚РѕРІР°СЂС‹ РґР»СЏ Р¶РёРІРѕС‚РЅС‹С…", "РєРѕСЂРјР° РґР»СЏ Р¶РёРІРѕС‚РЅС‹С…", "С‚РѕРІР°СЂС‹ РґР»СЏ РєРѕС€РµРє", "С‚РѕРІР°СЂС‹ РґР»СЏ СЃРѕР±Р°Рє",
    ),
    "РљРЅРёРіРё Рё РѕС„РёСЃ": (
        "РєРЅРёРіРё", "РєР°РЅС†С‚РѕРІР°СЂС‹", "РїСЂРёРЅС‚РµСЂС‹", "РѕС„РёСЃРЅР°СЏ С‚РµС…РЅРёРєР°", "РЅР°СЃС‚РѕР»СЊРЅС‹Рµ РёРіСЂС‹",
    ),
    "РЎР°Рґ Рё РґР°С‡Р°": (
        "С‚РѕРІР°СЂС‹ РґР»СЏ СЃР°РґР°", "СЃР°РґРѕРІР°СЏ С‚РµС…РЅРёРєР°", "С‚РѕРІР°СЂС‹ РґР»СЏ РґР°С‡Рё", "РіСЂРёР»СЊ Рё РјР°РЅРіР°Р»С‹",
    ),
    "РџСЂРѕРґСѓРєС‚С‹ Рё РЅР°РїРёС‚РєРё": (
        "РїСЂРѕРґСѓРєС‚С‹ РїРёС‚Р°РЅРёСЏ", "РєРѕС„Рµ", "С‡Р°Р№", "СЃР»Р°РґРѕСЃС‚Рё", "С€РѕРєРѕР»Р°Рґ Рё РєРѕРЅС„РµС‚С‹", "Р±Р°РєР°Р»РµСЏ", "РЅР°РїРёС‚РєРё",
    ),
    "РЎС‚СЂРѕРёС‚РµР»СЊСЃС‚РІРѕ Рё СЂРµРјРѕРЅС‚": (
        "СЃР°РЅС‚РµС…РЅРёРєР°", "СЌР»РµРєС‚СЂРёРєР° РґР»СЏ РґРѕРјР°", "СЃС‚СЂРѕРёС‚РµР»СЊРЅС‹Рµ РјР°С‚РµСЂРёР°Р»С‹", "РєСЂР°СЃРєРё Рё Р»Р°РєРё",
        "РІРѕРґРѕРЅР°РіСЂРµРІР°С‚РµР»Рё", "РґРІРµСЂРЅР°СЏ С„СѓСЂРЅРёС‚СѓСЂР°", "Р·Р°РјРєРё",
    ),
}
AUTO_SEARCHES = tuple(query for queries in AUTO_SEARCH_GROUPS.values() for query in queries)
QUERY_GROUP = {query: group for group, queries in AUTO_SEARCH_GROUPS.items() for query in queries}


def _number(value):
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d[\d\s]*", str(value or ""))
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return float(digits) if digits else None


def _wb_items(query: str, limit: int):
    referer = f"https://www.wildberries.ru/catalog/0/search.aspx?search={quote(query)}&sort=benefit"
    records = wildberries.search_wb(query, limit, referer=referer, extra={"sort": "benefit"})
    found = []
    for record in records:
        sizes = record.get("sizes") or []
        prices = sizes[0].get("price", {}) if sizes else {}
        price = _number(prices.get("product"))
        old_price = _number(prices.get("basic"))
        article = record.get("id")
        name = (record.get("name") or "").strip()
        if price and article and name:
            found.append({
                "marketplace": "Wildberries",
                "name": name,
                "link": f"https://www.wildberries.ru/catalog/{article}/detail.aspx",
                "price_value": price / 100,
                "old_price_value": old_price / 100 if old_price and old_price > price else None,
            })
    return found


def _ozon_items(session, query: str, limit: int):
    records = ozon.search(session, "/search/", query, limit, extra={"sorting": "discount"})
    found = []
    for record in records:
        price = _number(record.get("price"))
        old_price = _number(record.get("oldPrice"))
        link = record.get("url")
        if link and not link.startswith("http"):
            link = "https://www.ozon.ru" + link
        name = (record.get("name") or "").strip()
        if price and link and name:
            found.append({
                "marketplace": "Ozon",
                "name": name,
                "link": link,
                "price_value": price,
                "old_price_value": old_price if old_price and old_price > price else None,
                "image_url": record.get("image"),
            })
    return found


def scan_queries(queries=AUTO_SEARCHES, limit: int = 12):
    """Fetch lightweight search results with one browser session per marketplace."""
    results = {query: [] for query in queries}
    errors = []
    log.info("Starting fast scan: %d category themes", len(queries))

    session = None
    try:
        session = ozon.OzonSession()
        for query in queries:
            try:
                found = _ozon_items(session, query, limit)
                results[query].extend(found)
                photos = sum(bool(item.get("image_url")) for item in found)
                log.info("Ozon category '%s': %d offers, %d photos", query, len(found), photos)
            except Exception as exc:
                errors.append(f"Ozon {query}: {type(exc).__name__}: {exc}")
                log.warning("Ozon category '%s' failed: %s", query, type(exc).__name__)
            time.sleep(0.35)
    except Exception as exc:
        errors.append(f"Ozon session: {type(exc).__name__}: {exc}")
    finally:
        if session:
            session.shutdown()

    log.info("Fast scan complete: %d offers, %d errors", sum(map(len, results.values())), len(errors))
    return results, errors


def scan_query(query: str, limit: int = 12):
    """Compatibility wrapper for scanning one theme."""
    results, errors = scan_queries((query,), limit)
    return results[query], errors

