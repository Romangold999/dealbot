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
    "сумка", "кейс", "защитный", "защитная", "аксессуар",
}
_BUNDLE = {
    "набор", "комплект", "комплектация", "бандл", "bundle", "2в1", "3в1",
}
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
    return {
        t for t in tokens
        if re.search(r"\d", t) and not re.fullmatch(r"\d{1,4}", t)
    }


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
    if ta & _ACCESSORY or tb & _ACCESSORY:
        return {"comparable": False, "confidence": 0.0, "reason": "accessory"}

    ca, cb = _condition(a), _condition(b)
    if ca == "conflict" or cb == "conflict" or ca != cb:
        return {"comparable": False, "confidence": 0.0, "reason": "condition mismatch"}

    # A bundle is only comparable to another explicitly equivalent bundle.
    ba, bb = bool(ta & _BUNDLE), bool(tb & _BUNDLE)
    if ba != bb:
        return {"comparable": False, "confidence": 0.0, "reason": "bundle mismatch"}

    capa, capb = _capacities(a), _capacities(b)
    if capa and capb and capa != capb:
        return {"comparable": False, "confidence": 0.0, "reason": "capacity mismatch"}

    sizea, sizeb = _sizes(a), _sizes(b)
    if sizea and sizeb and sizea != sizeb:
        return {"comparable": False, "confidence": 0.0, "reason": "size mismatch"}

    ma, mb = _model_tokens(_tokens(a)), _model_tokens(_tokens(b))
    if ma and mb and not (ma & mb):
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
        return values
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
