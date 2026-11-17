import unittest

from detector import compare_products, evaluate_peer_deal


class ProductMatchingTests(unittest.TestCase):
    def test_same_model_and_capacity_match(self):
        result = compare_products(
            {"name": "Samsung Galaxy S24 Ultra 256GB"},
            {"name": "Samsung Galaxy S24 Ultra 256 GB"},
        )
        self.assertTrue(result["comparable"])

    def test_different_brand_does_not_match(self):
        result = compare_products(
            {"name": "Samsung Galaxy S24 Ultra"},
            {"name": "Xiaomi Galaxy S24 Ultra"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "brand mismatch")

    def test_different_generation_does_not_match(self):
        result = compare_products(
            {"name": "Samsung Galaxy S24 Ultra 256GB"},
            {"name": "Samsung Galaxy S23 Ultra 256GB"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "model mismatch")

    def test_different_capacity_does_not_match(self):
        result = compare_products(
            {"name": "Apple iPhone 15 Pro 128GB"},
            {"name": "Apple iPhone 15 Pro 256GB"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "capacity mismatch")

    def test_new_and_used_are_not_compared(self):
        result = compare_products(
            {"name": "Sony WH-1000XM5 новые"},
            {"name": "Sony WH-1000XM5 б/у"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "condition mismatch")

    def test_accessory_is_rejected(self):
        result = compare_products(
            {"name": "Apple iPhone 15 Pro"},
            {"name": "Чехол для Apple iPhone 15 Pro"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "accessory")

    def test_bundle_mismatch_is_rejected(self):
        result = compare_products(
            {"name": "Sony PlayStation 5"},
            {"name": "Набор Sony PlayStation 5 два контроллера"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "bundle mismatch")

    def test_color_aliases_match_but_different_colors_do_not(self):
        same = compare_products(
            {"name": "Apple iPhone 15 Pro 256GB черный"},
            {"name": "Apple iPhone 15 Pro 256GB black"},
        )
        different = compare_products(
            {"name": "Apple iPhone 15 Pro 256GB черный"},
            {"name": "Apple iPhone 15 Pro 256GB белый"},
        )
        self.assertTrue(same["comparable"])
        self.assertFalse(different["comparable"])
        self.assertEqual(different["reason"], "color mismatch")

    def test_explicit_clothing_size_must_match(self):
        result = compare_products(
            {"name": "Кроссовки Nike Air Max 270 размер 42"},
            {"name": "Кроссовки Nike Air Max 270 размер 43"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "variant size mismatch")

    def test_bundle_quantity_must_match(self):
        result = compare_products(
            {"name": "Набор LEGO Creator 3 шт"},
            {"name": "Набор LEGO Creator 5 шт"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "bundle quantity mismatch")

    def test_peer_deal_requires_two_comparable_offers(self):
        product = {"name": "Samsung Galaxy Buds2 Pro", "link": "a", "price_value": 5000}
        one_peer = [{"name": "Samsung Galaxy Buds2 Pro", "link": "b", "price_value": 10000}]
        self.assertIsNone(evaluate_peer_deal(product, one_peer))

    def test_peer_deal_scores_only_comparable_offers(self):
        product = {"name": "Samsung Galaxy Buds2 Pro", "link": "a", "price_value": 5000}
        offers = [
            product,
            {"name": "Samsung Galaxy Buds2 Pro", "link": "b", "price_value": 10000},
            {"name": "Samsung Galaxy Buds2 Pro", "link": "c", "price_value": 10200},
            {"name": "Чехол для Samsung Galaxy Buds2 Pro", "link": "d", "price_value": 500},
        ]
        result = evaluate_peer_deal(product, offers)
        self.assertIsNotNone(result)
        self.assertEqual(result["peer_count"], 2)
        self.assertGreaterEqual(result["score"], 78)


if __name__ == "__main__":
    unittest.main()
