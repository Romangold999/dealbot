import re
from difflib import SequenceMatcher
from statistics import median


def title_similarity(left: str, right: str) -> float:
    def normalize(value: str) -> str:
        value = value.lower().replace("ё", "е")
        return " ".join(re.findall(r"[a-zа-я0-9]+", value))

    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    # Exact model numbers matter: do not match different generations or capacities.
    nums_a = set(re.findall(r"[a-z]*\d+[a-z0-9]*", a))
    nums_b = set(re.findall(r"[a-z]*\d+[a-z0-9]*", b))
    if nums_a and nums_b and not nums_a.intersection(nums_b):
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def peer_price_anomaly(product: dict, products: list[dict], threshold: float) -> str | None:
    peers = [
        p["price_value"] for p in products
        if p["link"] != product["link"]
        and p["price_value"] > 0
        and title_similarity(product["name"], p["name"]) >= 0.67
    ]
    if len(peers) < 2:
        return None
    reference = median(peers)
    drop = 1 - product["price_value"] / reference
    if drop >= threshold:
        return f"на {round(drop * 100)}% ниже похожих предложений (ориентир {reference:,.0f} ₽)".replace(",", " ")
    return None


def has_close_price_peers(product: dict, products: list[dict], min_peers: int = 2, tolerance: float = 0.10) -> bool:
    """True when comparable products make the listed discount unconvincing."""
    peers = [
        p["price_value"] for p in products
        if p["link"] != product["link"]
        and p["price_value"] > 0
        and title_similarity(product["name"], p["name"]) >= 0.67
    ]
    if len(peers) < min_peers:
        return False
    reference = median(peers)
    return 1 - product["price_value"] / reference < tolerance


def history_anomaly(price: float, previous: list[float], threshold: float) -> str | None:
    if len(previous) < 3:
        return None
    reference = median(previous)
    drop = 1 - price / reference
    if drop >= threshold:
        return f"на {round(drop * 100)}% ниже медианы своей цены за 30 дней"
    return None


def listed_discount_anomaly(price: float, old_price: float | None, threshold: float) -> str | None:
    """Use the marketplace's displayed prior price as a discovery signal."""
    if not old_price or old_price <= price:
        return None
    drop = 1 - price / old_price
    if drop >= threshold:
        return f"площадка заявляет скидку {round(drop * 100)}% от прежней цены"
    return None
