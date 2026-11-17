"""Strict product matching and deal scoring.

Displayed marketplace discounts are deliberately not treated as proof of a deal.
"""
import re
from difflib import SequenceMatcher
from statistics import median

_STOP = {
    "и", "в", "на", "для", "с", "по", "от", "до", "из", "под", "the", "a",
    "шт", "штук", "новый", "новая", "новое", "оригинал", "официальный",
    "купить", "доставка", "товар", "набор", "комплект",
}
_ACCESSORY = {
    "чехол", "стекло", "пленка", "плёнка", "кабель", "зарядка", "адаптер",
    "держатель", "крепление", "насадка", "фильтр", "картридж", "ремешок",
    "сумка", "кейс", "case", "защитный", "защитная", "аксессуар",
}
_BUNDLE = {
    "набор", "комплект", "комплектация", "бандл", "bundle", "2в1", "3в1",
}
_BRANDS = {
    "apple", "samsung", "xiaomi", "redmi", "poco", "huawei", "honor", "oppo",
    "vivo", "realme", "oneplus", "google", "sony", "lg", "bosch", "dyson",
    "philips", "hp", "dell", "lenovo", "asus", "acer", "msi", "gigabyte",
    "intel", "amd", "nvidia", "canon", "nikon", "fujifilm", "jbl", "bose",
    "marshall", "sennheiser", "anker", "baseus", "tefal", "braun", "rowenta",
    "electrolux", "haier", "hisense", "tcl", "beko", "indesit", "whirlpool",
    "kitfort", "redmond", "polaris", "yamaha", "pioneer", "karcher", "makita",
    "dewalt", "metabo", "hikoki", "milwaukee", "stanley", "grohe", "ikea",
    "lego", "logitech", "razer", "steelseries", "corsair", "hyperx",
    "nintendo", "playstation", "xbox", "microsoft", "amazon", "garmin",
    "fitbit", "polar", "suunto", "meizu", "tecno", "infinix", "motorola",
    "nokia", "nothing", "zte", "tenda", "keenetic", "mikrotik",
}
_COLOR_ALIASES = {
    "черный": "black", "черная": "black", "черное": "black", "black": "black",
    "белый": "white", "белая": "white", "белое": "white", "white": "white",
    "синий": "blue", "синяя": "blue", "голубой": "blue", "blue": "blue",
    "красный": "red", "красная": "red", "red": "red",
    "зеленый": "green", "зеленая": "green", "green": "green",
    "розовый": "pink", "розовая": "pink", "pink": "pink",
    "серый": "gray", "серая": "gray", "gray": "gray", "grey": "gray",
    "серебристый": "silver", "серебристая": "silver", "silver": "silver",
    "золотой": "gold", "золотая": "gold", "gold": "gold",
    "фиолетовый": "purple", "фиолетовая": "purple", "purple": "purple",
    "желтый": "yellow", "желтая": "yellow", "yellow": "yellow",
    "бежевый": "beige", "бежевая": "beige", "beige": "beige",
    "коричневый": "brown", "коричневая": "brown", "brown": "brown",
}
_VARIANT_SIZE = re.compile(r"(?<![a-zа-я0-9])(?:размер|size|eu)\s*[:\-]?\s*(xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|\d{2})(?![a-zа-я0-9])", re.I)
_BUNDLE_COUNT = re.compile(r"(?:набор|комплект|упаковк\w*)\s*(?:из|по)?\s*(\d+)", re.I)
_CONDITION = {
    "used": {"б/у", "бу", "уцененный", "уценённый", "восстановленный", "refurbished"},
    "new": {"новый", "новая", "новое", "новые", "new"},
}
_CAPACITY = re.compile(r"(?<![a-zа-я0-9])(\d+(?:[.,]\d+)?)\s*(tb|тб|gb|гб|mb|мб|г\s*\.?\s*б)(?![a-zа-я])", re.I)
_SIZE = re.compile(r"(?<![a-zа-я0-9])(\d+(?:[.,]\d+)?)\s*(дюйм\w*|inch|in)(?![a-zа-я])", re.I)


def _tokens(value):
    return re.findall(r"[a-zа-яё0-9]+", str(value or "").lower().replace("ё", "е"))


def _condition(name):
    tokens = set(_tokens(str(name or "").replace("б/у", "бу")))
    found = {kind for kind, words in _CONDITION.items() if tokens & words}
    if len(found) > 1:
        return "conflict"
    return next(iter(found), "unknown")


def _capacities(name):
    text = str(name or "").lower().replace(",", ".")
    return {
        (m.group(1), re.sub(r"\s+", "", m.group(2).lower()))
        for m in _CAPACITY.finditer(text)
    }


def _sizes(name):
    text = str(name or "").lower().replace(",", ".")
    return {
        (m.group(1), m.group(2).lower().replace("дюйм", "in"))
        for m in _SIZE.finditer(text)
    }


def _model_tokens(tokens):
    text = " ".join(tokens)
    capacity_numbers = {m.group(1) for m in _CAPACITY.finditer(text)}
    size_numbers = {m.group(1) for m in _SIZE.finditer(text)}
    result = set()
    for token in tokens:
        if re.fullmatch(r"\d+(?:gb|гб|tb|тб|mb|мб)", token):
            continue
        if re.search(r"[a-zа-я]", token) and re.search(r"\d", token):
            result.add(token)
        elif token.isdigit() and len(token) >= 2 and token not in capacity_numbers and token not in size_numbers:
            result.add(token)
    return result


def title_similarity(left: str, right: str) -> float:
    a = " ".join(t for t in _tokens(left) if t not in _STOP)
    b = " ".join(t for t in _tokens(right) if t not in _STOP)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    sequence = SequenceMatcher(None, a, b).ratio()
    return 0.65 * jaccard + 0.35 * sequence


def compare_products(left: dict, right: dict) -> dict:
    """Return an explainable compatibility decision; never guess unknown variants."""
    a, b = str(left.get("name") or ""), str(right.get("name") or "")
    if not a or not b:
        return {"comparable": False, "confidence": 0.0, "reason": "missing title"}

    ta, tb = set(_tokens(a)), set(_tokens(b))
    brands_a, brands_b = ta & _BRANDS, tb & _BRANDS
    if brands_a and brands_b and not (brands_a & brands_b):
        return {"comparable": False, "confidence": 0.0, "reason": "brand mismatch"}
    if ta & _ACCESSORY or tb & _ACCESSORY:
        return {"comparable": False, "confidence": 0.0, "reason": "accessory"}

    ca, cb = _condition(a), _condition(b)
    if ca == "conflict" or cb == "conflict" or ca != cb:
        return {"comparable": False, "confidence": 0.0, "reason": "condition mismatch"}

    # Bundle composition, color and explicit clothing/shoe sizes are variants.
    ba, bb = bool(ta & _BUNDLE), bool(tb & _BUNDLE)
    if ba != bb:
        return {"comparable": False, "confidence": 0.0, "reason": "bundle mismatch"}
    count_a = _BUNDLE_COUNT.search(a)
    count_b = _BUNDLE_COUNT.search(b)
    if bool(count_a) != bool(count_b) or (count_a and count_b and count_a.group(1) != count_b.group(1)):
        return {"comparable": False, "confidence": 0.0, "reason": "bundle quantity mismatch"}

    colors_a = {_COLOR_ALIASES[t] for t in ta if t in _COLOR_ALIASES}
    colors_b = {_COLOR_ALIASES[t] for t in tb if t in _COLOR_ALIASES}
    if bool(colors_a) != bool(colors_b) or (colors_a and colors_b and colors_a != colors_b):
        return {"comparable": False, "confidence": 0.0, "reason": "color mismatch"}

    size_a = {m.group(1).lower() for m in _VARIANT_SIZE.finditer(a)}
    size_b = {m.group(1).lower() for m in _VARIANT_SIZE.finditer(b)}
    if bool(size_a) != bool(size_b) or (size_a and size_b and size_a != size_b):
        return {"comparable": False, "confidence": 0.0, "reason": "variant size mismatch"}

    capa, capb = _capacities(a), _capacities(b)
    if bool(capa) != bool(capb) or (capa and capb and capa != capb):
        return {"comparable": False, "confidence": 0.0, "reason": "capacity mismatch"}

    sizea, sizeb = _sizes(a), _sizes(b)
    if bool(sizea) != bool(sizeb) or (sizea and sizeb and sizea != sizeb):
        return {"comparable": False, "confidence": 0.0, "reason": "size mismatch"}

    ma, mb = _model_tokens(_tokens(a)), _model_tokens(_tokens(b))
    if bool(ma) != bool(mb) or (ma and mb and not (ma & mb)):
        return {"comparable": False, "confidence": 0.0, "reason": "model mismatch"}

    similarity = title_similarity(a, b)
    if similarity < 0.58:
        return {"comparable": False, "confidence": similarity, "reason": "weak title match"}

    # Matching model identifiers and explicit capacities increase confidence.
    confidence = min(0.99, similarity + (0.12 if ma & mb else 0.0))
    if capa and capb and capa == capb:
        confidence = min(0.99, confidence + 0.05)
    return {
        "comparable": confidence >= 0.78,
        "confidence": round(confidence, 3),
        "reason": "strict title/model/variant match" if confidence >= 0.78 else "insufficient identity confidence",
    }


def _robust_prices(values):
    values = [float(v) for v in values if v and float(v) > 0]
    if len(values) < 3:
        return values
    center = median(values)
    deviations = [abs(v - center) for v in values]
    mad = median(deviations)
    if mad <= 0:
        return [v for v in values if v == center]
    # Robustly remove extreme price listings without assuming a normal distribution.
    return [v for v in values if abs(v - center) <= 3.5 * 1.4826 * mad]


def evaluate_peer_deal(product: dict, products: list[dict]) -> dict | None:
    peers = []
    confidences = []
    for peer in products:
        if peer.get("link") == product.get("link"):
            continue
        if not peer.get("price_value") or peer["price_value"] <= 0:
            continue
        match = compare_products(product, peer)
        if match["comparable"]:
            peers.append(float(peer["price_value"]))
            confidences.append(match["confidence"])
    if len(peers) < 2:
        return None
    clean = _robust_prices(peers)
    if len(clean) < 2:
        return None
    reference = median(clean)
    price = float(product.get("price_value") or 0)
    if price <= 0 or reference <= price:
        return None
    discount = 1 - price / reference
    confidence = sum(confidences) / len(confidences)
    peer_factor = min(1.0, len(clean) / 5)
    anomaly_factor = min(1.0, discount / 0.35)
    savings_factor = min(1.0, (reference - price) / 10000)
    score = 100 * (0.55 * anomaly_factor + 0.25 * confidence + 0.15 * peer_factor + 0.05 * savings_factor)
    return {
        "score": round(score),
        "reference_price": round(reference),
        "discount": round(discount, 4),
        "peer_count": len(clean),
        "confidence": round(confidence, 3),
        "reason": f"{round(discount * 100)}% ниже медианы {len(clean)} сопоставимых предложений (ориентир {reference:,.0f} ₽)".replace(",", " "),
    }


def history_anomaly(price: float, previous: list[float], threshold: float = 0.30) -> str | None:
    if len(previous) < 3:
        return None
    reference = median(float(p) for p in previous if p and p > 0)
    if reference <= 0:
        return None
    drop = 1 - price / reference
    if drop >= threshold:
        return f"на {round(drop * 100)}% ниже собственной медианы цены за историю наблюдений"
    return None
