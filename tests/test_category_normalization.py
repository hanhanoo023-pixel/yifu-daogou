from scripts.build_filtered_product_database import normalize_category


def test_category_path_prevents_dress_compound_errors() -> None:
    cases = (
        (["Clothing, Shoes & Jewelry", "Men", "Accessories", "Gloves & Mittens", "Cold Weather Gloves"], "Leather Dress Gloves", "accessory"),
        (["Clothing, Shoes & Jewelry", "Men", "Clothing", "Shirts", "Dress Shirts"], "Slim Fit Dress Shirt", "top"),
        (["Clothing, Shoes & Jewelry", "Women", "Clothing", "Active", "Active Pants"], "Dress Yoga Pants", "pants"),
        (["Clothing, Shoes & Jewelry", "Men", "Clothing", "Pants", "Dress"], "Golf Dress Pants", "pants"),
        (["Clothing, Shoes & Jewelry", "Luggage & Travel Gear", "Luggage", "Garment Bags"], "Travel Dress Bag", "bag"),
        (["Clothing, Shoes & Jewelry", "Costumes & Accessories", "Women", "Wigs"], "Fancy Dress Wig", "accessory"),
        (["Clothing, Shoes & Jewelry", "Women", "Clothing", "Dresses", "Casual"], "Summer Midi Dress", "dress"),
    )
    for categories, title, expected in cases:
        assert normalize_category(categories, title) == expected


def test_parent_product_family_beats_leaf_top_label() -> None:
    assert normalize_category(
        ["Clothing, Shoes & Jewelry", "Women", "Clothing", "Swimsuits", "Bikinis", "Tops"],
        "Triangle Bikini Top",
    ) == "swimwear"
    assert normalize_category(
        ["Clothing, Shoes & Jewelry", "Women", "Clothing", "Lingerie", "Thermal Underwear", "Tops"],
        "Thermal Base Layer Top",
    ) == "underwear"
