"""
Supplier BI Agent — Seed Data Generator
========================================
Generates ~500k orders, ~50k incidents, ~25k returns, 20 suppliers
and loads them into BigQuery (or saves as CSVs for manual upload).

Usage:
    # Generate CSVs only (for inspection or manual upload)
    python seed_data.py --output csv

    # Generate and load directly into BigQuery
    python seed_data.py --output bigquery --project YOUR_GCP_PROJECT --dataset supplier_bi

Dependencies:
    pip install google-cloud-bigquery pandas numpy
"""

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Try importing optional dependencies ──────────────────────────────────────
try:
    import pandas as pd
    import numpy as np
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    print("pandas/numpy not found — falling back to pure Python CSV generation.")

try:
    from google.cloud import bigquery
    HAS_BQ = True
except ImportError:
    HAS_BQ = False


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

RANDOM_SEED = 42
random.seed(RANDOM_SEED)
if HAS_PANDAS:
    np.random.seed(RANDOM_SEED)

TARGET_ORDERS      = 500_000
INCIDENT_RATE      = 0.10   # 10% of orders
RETURN_RATE        = 0.05   # 5% of orders (buyers remorse)
HISTORY_DAYS       = 365    # 12 months of history
START_DATE         = date.today() - timedelta(days=HISTORY_DAYS)

OUTPUT_DIR = Path("./seed_output")

# ── Suppliers ─────────────────────────────────────────────────────────────────
# 5 high-volume suppliers (75% of revenue+volume, 60% of incidents)
# 15 standard suppliers share the rest

SUPPLIERS = [
    # id, name, region, tier, category_speciality, volume_weight, incident_weight_multiplier
    ("SUP001", "Apex Manufacturing Co.",      "North America", "preferred",     "Electronics",        0.18, 1.2),
    ("SUP002", "Horizon Global Goods",        "Europe",        "preferred",     "Home & Garden",      0.16, 1.0),
    ("SUP003", "Summit Supply Chain",         "Asia Pacific",  "preferred",     "Clothing & Apparel", 0.15, 0.95),  # spike supplier
    ("SUP004", "CoreTech Industries",         "North America", "preferred",     "Sports & Outdoors",  0.14, 0.8),
    ("SUP005", "Pacific Rim Traders",         "Asia Pacific",  "preferred",     "Toys & Games",       0.12, 1.1),
    # Standard suppliers — 15 of them sharing remaining 25% volume
    ("SUP006", "Alpine Goods Ltd.",           "Europe",        "standard",      "Home & Garden",      0.018, 2.1),
    ("SUP007", "Zenith Products Inc.",        "North America", "standard",      "Electronics",        0.017, 1.0),
    ("SUP008", "Blue Ridge Supplies",         "North America", "standard",      "Clothing & Apparel", 0.017, 1.3),
    ("SUP009", "Meridian Trading Co.",        "Europe",        "standard",      "Sports & Outdoors",  0.016, 1.2),
    ("SUP010", "TerraFirm Wholesale",         "South America", "standard",      "Home & Garden",      0.016, 0.7),
    ("SUP011", "Crescent Moon Goods",         "Middle East",   "standard",      "Toys & Games",       0.015, 0.6),
    ("SUP012", "Nordic Supply Group",         "Europe",        "standard",      "Beauty & Health",    0.015, 1.0),
    ("SUP013", "Cascade Distributors",        "North America", "standard",      "Beauty & Health",    0.014, 0.9),
    ("SUP014", "Equinox Trade Partners",      "Asia Pacific",  "standard",      "Clothing & Apparel", 0.014, 1.3),
    ("SUP015", "Ironwood Manufacturing",      "North America", "standard",      "Sports & Outdoors",  0.013, 1.1),
    ("SUP016", "Coral Sea Imports",           "Asia Pacific",  "standard",      "Toys & Games",       0.013, 0.9),
    ("SUP017", "Stonebridge Commerce",        "Europe",        "standard",      "Electronics",        0.012, 0.85),
    ("SUP018", "Vega Global Supplies",        "South America", "probationary",  "Home & Garden",      0.011, 2.8),
    ("SUP019", "Polaris Procurement",         "North America", "probationary",  "Beauty & Health",    0.010, 2.6),
    ("SUP020", "Eastern Gate Traders",        "Asia Pacific",  "probationary",  "Clothing & Apparel", 0.009, 3.1),
]

# Normalize volume weights to sum to 1
_total_weight = sum(s[5] for s in SUPPLIERS)
SUPPLIER_VOLUME_WEIGHTS = [s[5] / _total_weight for s in SUPPLIERS]
SUPPLIER_IDS = [s[0] for s in SUPPLIERS]

# Supplier lookup dict
SUPPLIER_MAP = {s[0]: s for s in SUPPLIERS}

# SUP003 gets a spike starting 3 months ago — incident_weight multiplied
SPIKE_SUPPLIER    = "SUP003"
SPIKE_START_DATE  = date.today() - timedelta(days=90)
SPIKE_MULTIPLIER  = 3.5  # incident rate multiplies for this supplier post-spike

# ── Products ──────────────────────────────────────────────────────────────────

CATEGORIES = [
    "Electronics",
    "Home & Garden",
    "Clothing & Apparel",
    "Sports & Outdoors",
    "Toys & Games",
    "Beauty & Health",
    "Kitchen & Dining",
]

# Categories with elevated incident rates (3× average)
HIGH_INCIDENT_CATEGORIES = {"Electronics", "Toys & Games"}

# SKUs per category — 300 total across 7 categories (~43 per category)
def _generate_skus():
    skus = {}
    prefixes = {
        "Electronics":       "ELC",
        "Home & Garden":     "HMG",
        "Clothing & Apparel":"CLT",
        "Sports & Outdoors": "SPT",
        "Toys & Games":      "TOY",
        "Beauty & Health":   "BTY",
        "Kitchen & Dining":  "KIT",
    }
    price_tiers = ["budget", "mid", "premium"]
    # budget 40%, mid 40%, premium 20%
    tier_weights = [0.40, 0.40, 0.20]

    # Price ranges per tier per category (gross revenue range)
    price_ranges = {
        "budget":   (9.99,  49.99),
        "mid":      (50.00, 149.99),
        "premium":  (150.0, 599.99),
    }

    for cat in CATEGORIES:
        skus[cat] = []
        for i in range(1, 44):  # 43 SKUs per category = 301 total
            tier = random.choices(price_tiers, weights=tier_weights)[0]
            lo, hi = price_ranges[tier]
            base_price = round(random.uniform(lo, hi), 2)
            skus[cat].append({
                "sku":   f"{prefixes[cat]}-{i:04d}",
                "tier":  tier,
                "price": base_price,
            })
    return skus

SKUS = _generate_skus()

# ── Fulfilment channels ───────────────────────────────────────────────────────
# supplier_direct has 4× lost item rate
FULFILMENT_CHANNELS = ["warehouse", "supplier_direct", "third_party_logistics"]
FULFILMENT_WEIGHTS  = [0.60, 0.25, 0.15]

# ── Incident types ────────────────────────────────────────────────────────────
INCIDENT_TYPES = [
    "damage_defect",
    "missing_parts",
    "lost_item",
    "misinformation",
    "mis_shipped",
]

# Base distribution — modified by fulfilment channel
INCIDENT_TYPE_BASE_WEIGHTS = {
    "damage_defect":  0.35,
    "missing_parts":  0.20,
    "lost_item":      0.15,
    "misinformation": 0.15,
    "mis_shipped":    0.15,
}

# supplier_direct dramatically raises lost_item
INCIDENT_TYPE_WEIGHTS_DIRECT = {
    "damage_defect":  0.25,
    "missing_parts":  0.15,
    "lost_item":      0.40,  # 4× signal
    "misinformation": 0.10,
    "mis_shipped":    0.10,
}

# ── Incident resolutions ──────────────────────────────────────────────────────
RESOLUTIONS = ["full_refund", "full_replacement", "replacement_part", "discount_to_keep"]
RESOLUTION_WEIGHTS = [0.30, 0.25, 0.25, 0.20]

def resolution_cost(resolution, gross_revenue):
    if resolution == "full_refund":
        return round(gross_revenue, 2)
    elif resolution == "full_replacement":
        return round(gross_revenue, 2)
    elif resolution == "replacement_part":
        return round(gross_revenue * 0.10, 2)
    elif resolution == "discount_to_keep":
        pct = random.uniform(0.05, 0.30)
        return round(gross_revenue * pct, 2)
    return 0.0

# ── Return reasons ────────────────────────────────────────────────────────────
RETURN_REASONS = ["didnt_like", "doesnt_need", "not_up_to_expectations"]
RETURN_REASON_WEIGHTS = [0.30, 0.25, 0.45]

# not_up_to_expectations sub-reasons (for comment generation)
EXPECTATIONS_SUB = ["quality", "material", "size", "colour", "functionality", "other"]

# ── Resolution statuses ───────────────────────────────────────────────────────
RESOLUTION_STATUSES = ["open", "in_progress", "resolved", "escalated"]

def pick_resolution_status(order_date, incident_date):
    days_since = (date.today() - incident_date).days
    if days_since < 3:
        return random.choices(["open", "in_progress"], weights=[0.7, 0.3])[0]
    elif days_since < 14:
        return random.choices(["open", "in_progress", "resolved"], weights=[0.2, 0.4, 0.4])[0]
    else:
        return random.choices(["in_progress", "resolved", "escalated"], weights=[0.1, 0.8, 0.1])[0]

# ── Comment / review text pools ───────────────────────────────────────────────

INCIDENT_COMMENTS = {
    "damage_defect": [
        "Item arrived with a cracked casing — the corner was visibly broken and the internal components were exposed.",
        "The product had a manufacturing defect: the stitching came apart immediately on first use.",
        "Received a damaged unit. The screen had a large scratch running diagonally across it.",
        "Item was defective out of the box — the power button did not respond and the unit would not turn on.",
        "Significant cosmetic damage on arrival: dents on two sides and paint chipping along the edges.",
        "The product failed within 24 hours. There was a burning smell followed by complete shutdown.",
        "Defective hinge mechanism — the lid snapped off on the first opening.",
        "Arrived with water damage marks. Packaging was dry but the item itself showed moisture stains inside.",
        "The motor made a loud grinding noise from the first use and stopped working after 10 minutes.",
        "Broken clasp on delivery — the locking mechanism was bent and could not close properly.",
        "Two of the four legs were cracked. The item was unusable and potentially unsafe.",
        "The display was shattered despite adequate packaging. Glass fragments were loose inside the box.",
    ],
    "missing_parts": [
        "The product arrived without the power adapter. The box was sealed but the adapter slot was empty.",
        "Assembly instructions referenced 12 screws but only 8 were included in the hardware bag.",
        "The remote control was not included despite being listed as part of the package.",
        "Missing the user manual and warranty card. Only the product itself was in the box.",
        "One of the two handles was absent. The item cannot be used safely without both.",
        "The charging cable was not included. The product requires a proprietary cable not sold separately.",
        "Arrived without the mounting bracket. Cannot be installed as intended.",
        "The accessory kit shown in the product photos was missing — only the base unit was in the box.",
        "Battery cover was absent. The device cannot hold batteries without it.",
        "Missing three of the five interchangeable nozzle attachments described on the product page.",
    ],
    "lost_item": [
        "Package was marked as delivered but was not found at my address or with neighbours.",
        "Tracking shows delivered to a location that is not my address. Item has not arrived.",
        "Parcel appears to have been lost in transit — tracking has not updated in 12 days.",
        "Received an empty box. The product was either not packed or removed during shipping.",
        "Delivered to the wrong address according to the photo proof provided by the courier.",
        "Item lost during last-mile delivery. Courier confirmed they cannot locate the parcel.",
        "Package was returned to sender without any notification. Never received.",
        "Tracking shows the item was loaded onto a vehicle for delivery 3 weeks ago and has not moved since.",
    ],
    "misinformation": [
        "The product dimensions listed were incorrect — the item is 40% smaller than advertised.",
        "The material described as premium leather is clearly synthetic. Very misleading.",
        "Listed as compatible with my device model but does not fit or function with it.",
        "The colour shown in the product images is significantly different from what was received.",
        "Described as waterproof to 30 metres but failed completely when submerged in a shallow sink.",
        "The product capacity was misrepresented — holds half of what the listing states.",
        "Weight listed as 500g but the product weighs over 1.2kg, making it impractical for its stated use.",
        "Described as suitable for ages 3 and up but contains small parts that are a clear choking hazard.",
        "The product claims to be compatible with a specific standard but failed the basic compatibility test.",
        "Energy consumption listed as 40W but the actual unit draws over 110W according to my meter.",
    ],
    "mis_shipped": [
        "Received a completely different product — ordered a blue version and received a red one of a different model.",
        "Wrong size was shipped. Ordered large, received medium. The label on the package says medium.",
        "Received someone else's order. The packing slip inside has a different name and address.",
        "Ordered a set of four but received a single item. The packing slip shows the correct order.",
        "Got a different variant than ordered — the SKU on the box does not match my order confirmation.",
        "Received last year's model instead of the current version that was ordered and paid for.",
        "Two identical items arrived instead of the two different items ordered separately.",
        "Received a product in a different category entirely — ordered kitchenware, received a garden tool.",
    ],
}

CUSTOMER_REVIEWS = {
    "low": [  # 1-2 stars
        "Absolutely terrible experience. Would not recommend this seller to anyone.",
        "Complete waste of money. The product did not work as described and support was unhelpful.",
        "Very disappointed. Quality is nothing like what was shown in the photos.",
        "Do not buy from this supplier. Item arrived damaged and resolution took weeks.",
        "Worst purchase I've made this year. The product failed immediately and the description was misleading.",
        "One star is generous. Defective product, slow response, frustrating experience.",
        "This product is a scam. Nothing like advertised and impossible to return.",
        "Terrible quality. Fell apart within days. Avoid.",
    ],
    "mid": [  # 3 stars
        "Okay product but had some issues with delivery. Eventually sorted out.",
        "Average quality for the price. Nothing special but does the job after the initial problem was fixed.",
        "Had a minor issue that was resolved after contacting support. Product itself is acceptable.",
        "Mixed feelings. The product is decent but the experience around the issue was frustrating.",
        "Not what I expected but manageable. Support could be faster.",
        "Three stars because it works, but barely. Had to deal with a problem from day one.",
    ],
    "high": [  # 4-5 stars
        "Great product once the initial issue was resolved. Seller was helpful and responsive.",
        "Minor hiccup on delivery but the product itself is excellent. Very happy overall.",
        "Excellent quality. The small issue I raised was resolved quickly and professionally.",
        "Really happy with this purchase. Five stars for the product and the resolution service.",
        "Top quality. Had a small concern but it was addressed swiftly. Would buy again.",
        "Fantastic product. The resolution process was smooth and the outcome was fair.",
        "Very satisfied. Despite the initial problem, the overall experience was positive.",
    ],
}

RETURN_COMMENTS = {
    "didnt_like": [
        "The product just isn't what I was hoping for. It feels cheaper than expected.",
        "Not to my taste after seeing it in person. The photos made it look better than it is.",
        "Changed my mind after receiving it. It doesn't suit my home the way I imagined.",
        "Personal preference — the style just doesn't work for me after all.",
        "Decided I don't like the design as much as I thought. Returning for a refund.",
        "The colour doesn't match my existing items the way I hoped it would.",
    ],
    "doesnt_need": [
        "I ordered this as a gift but the recipient already has one. No longer needed.",
        "Bought this as a backup but realised I don't need a backup. Returning unused.",
        "Circumstances changed and I no longer have a use for this item.",
        "Purchased by mistake — I already had one of these I'd forgotten about.",
        "No longer needed. Situation changed before the item arrived.",
        "Bought it impulsively but on reflection I have no real use for it.",
    ],
    "not_up_to_expectations": {
        "quality": [
            "The build quality is much lower than the price point suggests. Feels very cheap.",
            "Quality is not what I expected based on the product description and reviews.",
            "The materials used are clearly lower grade than implied by the listing.",
            "Very flimsy construction. Does not feel like it will last.",
            "Quality control seems poor — there are rough edges and uneven finishes throughout.",
        ],
        "material": [
            "The material is not as described. It feels synthetic rather than the natural material listed.",
            "The fabric is much thinner than expected and feels like it will wear quickly.",
            "Not the material quality I expected at this price. Feels rough and uncomfortable.",
            "The leather finish is clearly artificial despite being marketed as genuine.",
            "Material is coarser than described and has an unpleasant texture.",
        ],
        "size": [
            "The sizing is very inconsistent with the size guide provided. Does not fit as expected.",
            "Much smaller in person than the dimensions suggested. Doesn't work for my space.",
            "Runs very small. Ordered my usual size and it is at least one size too small.",
            "Larger than I anticipated despite reading the dimensions. Doesn't fit where intended.",
            "The size chart was misleading. This does not match standard sizing in any way.",
        ],
        "colour": [
            "The colour is very different from what was shown in the product images.",
            "Much darker in person than the photos suggested. Not what I wanted.",
            "The shade is completely off from the listing. Looks different under any lighting.",
            "More faded and washed out than depicted. The photos were clearly enhanced.",
            "The colour clashes with everything rather than complementing as shown.",
        ],
        "functionality": [
            "The product does not perform the function it was designed for adequately.",
            "Far less effective than described. Does the job poorly rather than well.",
            "The features listed do not work as described in practice.",
            "Performance is below what any reasonable interpretation of the listing would suggest.",
            "Simply does not do what it says on the box. Fundamentally not fit for purpose.",
        ],
        "other": [
            "Generally not up to the standard I would expect for the price paid.",
            "Several aspects of the product fell short of the expectations set by the listing.",
            "Overall quality and performance were below what the product claimed to deliver.",
            "Did not meet my expectations in multiple areas. Returning for a full refund.",
            "The product in several ways failed to live up to its description.",
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def short_id(prefix=""):
    return prefix + uuid.uuid4().hex[:12].upper()

def random_date_between(start, end):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))

def anon_customer_id():
    """Anonymised but consistent customer ID."""
    return "CUST" + uuid.uuid4().hex[:10].upper()

def pick_incident_type(fulfilment_channel):
    if fulfilment_channel == "supplier_direct":
        weights = list(INCIDENT_TYPE_WEIGHTS_DIRECT.values())
    else:
        weights = list(INCIDENT_TYPE_BASE_WEIGHTS.values())
    return random.choices(INCIDENT_TYPES, weights=weights)[0]

def pick_incident_comment(incident_type):
    pool = INCIDENT_COMMENTS.get(incident_type, ["Issue reported by customer."])
    return random.choice(pool)

def pick_review(rating):
    if rating <= 2:
        return random.choice(CUSTOMER_REVIEWS["low"])
    elif rating == 3:
        return random.choice(CUSTOMER_REVIEWS["mid"])
    else:
        return random.choice(CUSTOMER_REVIEWS["high"])

def pick_return_comment(reason):
    if reason == "not_up_to_expectations":
        sub = random.choice(EXPECTATIONS_SUB)
        pool = RETURN_COMMENTS["not_up_to_expectations"][sub]
    else:
        pool = RETURN_COMMENTS[reason]
    return random.choice(pool)

def compute_incident_rate(supplier_id, order_date, base_rate, fulfilment_channel):
    supplier = SUPPLIER_MAP[supplier_id]
    # Dampen supplier multipliers — compress them toward 1.0
    # so they create relative differences without exploding the overall rate
    raw_multiplier = supplier[6]
    inc_multiplier = 1.0 + (raw_multiplier - 1.0) * 0.25
    if supplier_id == SPIKE_SUPPLIER and order_date >= SPIKE_START_DATE:
        inc_multiplier *= 1.8  # spike is real but contained
    rate = base_rate * inc_multiplier
    return min(rate, 0.25)  # hard cap at 25% for any single combination

def weighted_incident_rate_for_order(supplier_id, category, order_date, fulfilment_channel):
    base = INCIDENT_RATE
    # High-incident categories get 3× rate
    if category in HIGH_INCIDENT_CATEGORIES:
        base *= 1.25
    return compute_incident_rate(supplier_id, order_date, base, fulfilment_channel)


# ══════════════════════════════════════════════════════════════════════════════
# SUPPLIERS TABLE
# ══════════════════════════════════════════════════════════════════════════════

def generate_suppliers():
    rows = []
    for sup in SUPPLIERS:
        sid, name, region, tier, cat_spec, _, _ = sup[:7]
        # Onboarding date: preferred older, probationary recent
        if tier == "preferred":
            days_back = random.randint(730, 1825)   # 2-5 years
        elif tier == "standard":
            days_back = random.randint(365, 1095)   # 1-3 years
        else:
            days_back = random.randint(30, 365)     # 1 month - 1 year
        onboarding = date.today() - timedelta(days=days_back)
        rows.append({
            "supplierID":          sid,
            "supplierName":        name,
            "supplierRegion":      region,
            "supplierTier":        tier,
            "categorySpeciality":  cat_spec,
            "onboardingDate":      onboarding.isoformat(),
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# ORDERS, INCIDENTS, RETURNS GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_batch(n_orders, start_dt, end_dt, id_offset=0, progress=True):
    """
    Generate n_orders orders plus their linked incidents and returns.
    Returns (orders, incidents, returns) as lists of dicts.
    """
    orders    = []
    incidents = []
    returns   = []

    print(f"  Generating {n_orders:,} orders ({start_dt} → {end_dt})...")

    for i in range(n_orders):
        if progress and i > 0 and i % 50_000 == 0:
            print(f"    {i:,} / {n_orders:,} orders generated...")

        # ── Order core ────────────────────────────────────────────────────────
        order_id      = f"ORD{(id_offset + i):08d}"
        supplier_id   = random.choices(SUPPLIER_IDS, weights=SUPPLIER_VOLUME_WEIGHTS)[0]
        category      = random.choice(CATEGORIES)
        sku_entry     = random.choice(SKUS[category])
        sku           = sku_entry["sku"]
        price_tier    = sku_entry["tier"]
        base_price    = sku_entry["price"]
        unit_qty      = random.choices([1, 2, 3, 4, 5], weights=[0.60, 0.22, 0.10, 0.05, 0.03])[0]
        gross_revenue = round(base_price * unit_qty, 2)
        margin_pct    = random.uniform(0.22, 0.38)   # avg 30%
        net_revenue   = round(gross_revenue * margin_pct, 2)
        product_cost  = round(gross_revenue - net_revenue, 2)
        fulfilment    = random.choices(FULFILMENT_CHANNELS, weights=FULFILMENT_WEIGHTS)[0]
        customer_id   = anon_customer_id()
        order_date    = random_date_between(start_dt, end_dt)

        # Budget tier has 2× return rate — handled via flag below
        # Determine incident and return flags
        eff_inc_rate  = weighted_incident_rate_for_order(supplier_id, category, order_date, fulfilment)
        # Budget price tier raises return rate
        eff_ret_rate  = RETURN_RATE * (2.0 if price_tier == "budget" else 1.0)

        has_incident  = random.random() < eff_inc_rate
        has_return    = (not has_incident) and (random.random() < eff_ret_rate)

        orders.append({
            "orderID":           order_id,
            "supplierID":        supplier_id,
            "customerID":        customer_id,
            "productSKU":        sku,
            "productCategory":   category,
            "orderDate":         order_date.isoformat(),
            "unitQuantity":      unit_qty,
            "grossRevenue":      gross_revenue,
            "netRevenue":        net_revenue,
            "productCost":       product_cost,
            "fulfilmentChannel": fulfilment,
            "supplierTier":      SUPPLIER_MAP[supplier_id][3],
            "supplierRegion":    SUPPLIER_MAP[supplier_id][2],
            "productPriceTier":  price_tier,
            "hasIncident":       has_incident,
            "hasReturn":         has_return,
        })

        # ── Incident ──────────────────────────────────────────────────────────
        if has_incident:
            days_to_incident  = random.randint(0, 45)
            incident_date     = min(order_date + timedelta(days=days_to_incident), date.today())
            inc_type          = pick_incident_type(fulfilment)
            resolution        = random.choices(RESOLUTIONS, weights=RESOLUTION_WEIGHTS)[0]
            res_cost          = resolution_cost(resolution, gross_revenue)
            res_status        = pick_resolution_status(order_date, incident_date)
            if res_status == "resolved":
                days_to_resolve  = random.randint(1, 21)
                resolution_date  = (incident_date + timedelta(days=days_to_resolve)).isoformat()
            else:
                resolution_date  = None
            # Rating: incidents get lower ratings
            rating            = random.choices([1, 2, 3, 4, 5], weights=[0.35, 0.30, 0.20, 0.10, 0.05])[0]
            comment           = pick_incident_comment(inc_type)
            review            = pick_review(rating)

            incidents.append({
                "incidentID":                    f"INC{(id_offset + i):08d}",
                "orderID":                       order_id,
                "supplierID":                    supplier_id,
                "productSKU":                    sku,
                "productCategory":               category,
                "incidentDate":                  incident_date.isoformat(),
                "resolutionDate":                resolution_date,
                "resolutionStatus":              res_status,
                "daysBetweenPurchaseAndIncident": (incident_date - order_date).days,
                "incidentType":                  inc_type,
                "incidentResolution":            resolution,
                "resolutionCost":                res_cost,
                "incidentCustomerComment":       comment,
                "productRating":                 rating,
                "customerReview":                review,
            })

        # ── Return ────────────────────────────────────────────────────────────
        if has_return:
            days_to_return    = random.randint(1, 30)
            return_date       = min(order_date + timedelta(days=days_to_return), date.today())
            reason            = random.choices(RETURN_REASONS, weights=RETURN_REASON_WEIGHTS)[0]
            res_status        = pick_resolution_status(order_date, return_date)
            if res_status == "resolved":
                days_to_resolve  = random.randint(1, 14)
                resolution_date  = (return_date + timedelta(days=days_to_resolve)).isoformat()
            else:
                resolution_date  = None
            # Ratings: returns get medium-low ratings
            rating            = random.choices([1, 2, 3, 4, 5], weights=[0.15, 0.25, 0.35, 0.20, 0.05])[0]
            comment           = pick_return_comment(reason)
            review            = pick_review(rating)

            returns.append({
                "returnID":                     f"RET{(id_offset + i):08d}",
                "orderID":                      order_id,
                "supplierID":                   supplier_id,
                "productSKU":                   sku,
                "productCategory":              category,
                "returnDate":                   return_date.isoformat(),
                "resolutionDate":               resolution_date,
                "resolutionStatus":             res_status,
                "daysBetweenPurchaseAndReturn":  (return_date - order_date).days,
                "buyersRemorseReason":           reason,
                "buyersRemorseComment":          comment,
                "productRating":                rating,
                "customerReview":               review,
            })

    return orders, incidents, returns


# ══════════════════════════════════════════════════════════════════════════════
# CSV WRITING
# ══════════════════════════════════════════════════════════════════════════════

def write_csv(rows, filepath, fieldnames):
    if not rows:
        print(f"  No rows to write for {filepath.name}")
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  ✓ Wrote {len(rows):,} rows → {filepath}")

ORDERS_FIELDS = [
    "orderID","supplierID","customerID","productSKU","productCategory",
    "orderDate","unitQuantity","grossRevenue","netRevenue","productCost",
    "fulfilmentChannel","supplierTier","supplierRegion","productPriceTier",
    "hasIncident","hasReturn",
]
INCIDENTS_FIELDS = [
    "incidentID","orderID","supplierID","productSKU","productCategory",
    "incidentDate","resolutionDate","resolutionStatus",
    "daysBetweenPurchaseAndIncident","incidentType","incidentResolution",
    "resolutionCost","incidentCustomerComment","productRating","customerReview",
]
RETURNS_FIELDS = [
    "returnID","orderID","supplierID","productSKU","productCategory",
    "returnDate","resolutionDate","resolutionStatus",
    "daysBetweenPurchaseAndReturn","buyersRemorseReason",
    "buyersRemorseComment","productRating","customerReview",
]
SUPPLIERS_FIELDS = [
    "supplierID","supplierName","supplierRegion","supplierTier",
    "categorySpeciality","onboardingDate",
]


# ══════════════════════════════════════════════════════════════════════════════
# BIGQUERY LOADING
# ══════════════════════════════════════════════════════════════════════════════

BQ_SCHEMAS = {
    "orders": [
        bigquery.SchemaField("orderID",           "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierID",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("customerID",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productSKU",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productCategory",    "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("orderDate",          "DATE",    mode="REQUIRED"),
        bigquery.SchemaField("unitQuantity",       "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("grossRevenue",       "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("netRevenue",         "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("productCost",        "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("fulfilmentChannel",  "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierTier",       "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierRegion",     "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productPriceTier",   "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("hasIncident",        "BOOLEAN", mode="REQUIRED"),
        bigquery.SchemaField("hasReturn",          "BOOLEAN", mode="REQUIRED"),
    ],
    "incidents": [
        bigquery.SchemaField("incidentID",                      "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("orderID",                         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierID",                      "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productSKU",                      "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productCategory",                 "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("incidentDate",                    "DATE",    mode="REQUIRED"),
        bigquery.SchemaField("resolutionDate",                  "DATE",    mode="NULLABLE"),
        bigquery.SchemaField("resolutionStatus",                "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("daysBetweenPurchaseAndIncident",  "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("incidentType",                    "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("incidentResolution",              "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("resolutionCost",                  "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("incidentCustomerComment",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productRating",                   "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("customerReview",                  "STRING",  mode="REQUIRED"),
    ],
    "returns": [
        bigquery.SchemaField("returnID",                       "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("orderID",                        "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierID",                     "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productSKU",                     "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productCategory",                "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("returnDate",                     "DATE",    mode="REQUIRED"),
        bigquery.SchemaField("resolutionDate",                 "DATE",    mode="NULLABLE"),
        bigquery.SchemaField("resolutionStatus",               "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("daysBetweenPurchaseAndReturn",   "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("buyersRemorseReason",            "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("buyersRemorseComment",           "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productRating",                  "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("customerReview",                 "STRING",  mode="REQUIRED"),
    ],
    "suppliers": [
        bigquery.SchemaField("supplierID",         "STRING", mode="REQUIRED"),
        bigquery.SchemaField("supplierName",       "STRING", mode="REQUIRED"),
        bigquery.SchemaField("supplierRegion",     "STRING", mode="REQUIRED"),
        bigquery.SchemaField("supplierTier",       "STRING", mode="REQUIRED"),
        bigquery.SchemaField("categorySpeciality", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("onboardingDate",     "DATE",   mode="REQUIRED"),
    ],
}

def load_to_bigquery(project, dataset, table_name, rows, schema):
    if not HAS_BQ:
        print("  google-cloud-bigquery not installed. Skipping BQ load.")
        return
    if not rows:
        return

    client      = bigquery.Client(project=project)
    table_ref   = f"{project}.{dataset}.{table_name}"

    job_config  = bigquery.LoadJobConfig(
        schema          = schema,
        write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format   = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    # Convert to JSON-serialisable format
    cleaned = []
    for row in rows:
        r = {}
        for k, v in row.items():
            if isinstance(v, bool):
                r[k] = v
            elif v is None:
                r[k] = None
            else:
                r[k] = v
        cleaned.append(r)

    job = client.load_table_from_json(cleaned, table_ref, job_config=job_config)
    job.result()
    print(f"  ✓ Loaded {len(rows):,} rows → {table_ref}")


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION REPORT
# ══════════════════════════════════════════════════════════════════════════════

def print_validation(orders, incidents, returns, suppliers):
    print("\n" + "═" * 60)
    print("VALIDATION REPORT")
    print("═" * 60)
    print(f"  Orders:    {len(orders):>8,}")
    print(f"  Incidents: {len(incidents):>8,}  ({len(incidents)/len(orders)*100:.1f}% of orders)")
    print(f"  Returns:   {len(returns):>8,}  ({len(returns)/len(orders)*100:.1f}% of orders)")
    print(f"  Suppliers: {len(suppliers):>8,}")

    # Volume distribution
    print("\n  Supplier volume distribution (top 5):")
    from collections import Counter
    sup_counts = Counter(o["supplierID"] for o in orders)
    top5 = sup_counts.most_common(5)
    total_orders = len(orders)
    for sid, cnt in top5:
        name = SUPPLIER_MAP[sid][1]
        print(f"    {sid} ({name[:25]:<25}): {cnt:>7,}  ({cnt/total_orders*100:.1f}%)")

    top5_total = sum(cnt for _, cnt in sup_counts.most_common(5))
    print(f"  Top 5 combined: {top5_total/total_orders*100:.1f}% of orders")

    # Incident distribution by supplier
    print("\n  Incident distribution (top 5 suppliers):")
    inc_sup = Counter(i["supplierID"] for i in incidents)
    top5_inc = inc_sup.most_common(5)
    total_inc = len(incidents)
    for sid, cnt in top5_inc:
        name = SUPPLIER_MAP[sid][1]
        print(f"    {sid} ({name[:25]:<25}): {cnt:>6,}  ({cnt/total_inc*100:.1f}%)")

    # Category incident rate
    print("\n  Incident rate by category:")
    cat_orders   = Counter(o["productCategory"] for o in orders)
    cat_incidents = Counter(i["productCategory"] for i in incidents)
    for cat in sorted(CATEGORIES):
        n_ord = cat_orders[cat]
        n_inc = cat_incidents[cat]
        rate  = n_inc / n_ord * 100 if n_ord else 0
        flag  = " ← elevated" if cat in HIGH_INCIDENT_CATEGORIES else ""
        print(f"    {cat:<22}: {rate:.1f}%{flag}")

    # Spike supplier check
    print(f"\n  Spike supplier ({SPIKE_SUPPLIER}) incident rate:")
    sp_orders_pre  = sum(1 for o in orders if o["supplierID"] == SPIKE_SUPPLIER
                        and date.fromisoformat(o["orderDate"]) < SPIKE_START_DATE)
    sp_orders_post = sum(1 for o in orders if o["supplierID"] == SPIKE_SUPPLIER
                        and date.fromisoformat(o["orderDate"]) >= SPIKE_START_DATE)
    sp_inc_pre  = sum(1 for i in incidents if i["supplierID"] == SPIKE_SUPPLIER
                     and date.fromisoformat(i["incidentDate"]) < SPIKE_START_DATE)
    sp_inc_post = sum(1 for i in incidents if i["supplierID"] == SPIKE_SUPPLIER
                     and date.fromisoformat(i["incidentDate"]) >= SPIKE_START_DATE)
    if sp_orders_pre:
        print(f"    Pre-spike rate:  {sp_inc_pre/sp_orders_pre*100:.1f}%")
    if sp_orders_post:
        print(f"    Post-spike rate: {sp_inc_post/sp_orders_post*100:.1f}%  ← should be ~{SPIKE_MULTIPLIER}× higher")

    # Fulfilment channel lost item check
    print("\n  Lost item rate by fulfilment channel:")
    ch_orders = Counter(o["fulfilmentChannel"] for o in orders)
    ch_lost   = Counter(
        i["fulfilmentChannel"] if "fulfilmentChannel" in i else
        next((o["fulfilmentChannel"] for o in orders if o["orderID"] == i["orderID"]), "unknown")
        for i in incidents if i["incidentType"] == "lost_item"
    )
    # Simpler: just check overall incident type by channel
    ch_inc_type = {}
    order_channel = {o["orderID"]: o["fulfilmentChannel"] for o in orders}
    for inc in incidents:
        ch = order_channel.get(inc["orderID"], "unknown")
        if ch not in ch_inc_type:
            ch_inc_type[ch] = Counter()
        ch_inc_type[ch][inc["incidentType"]] += 1

    for ch in FULFILMENT_CHANNELS:
        n_ord = ch_orders[ch]
        n_lost = ch_inc_type.get(ch, Counter()).get("lost_item", 0)
        rate = n_lost / n_ord * 100 if n_ord else 0
        flag = " ← should be elevated" if ch == "supplier_direct" else ""
        print(f"    {ch:<25}: lost_item rate {rate:.2f}%{flag}")

    # Revenue totals
    total_gross = sum(o["grossRevenue"] for o in orders)
    total_res_cost = sum(i["resolutionCost"] for i in incidents)
    print(f"\n  Total gross revenue:   ${total_gross:>14,.2f}")
    print(f"  Total resolution cost: ${total_res_cost:>14,.2f}")
    print(f"  Resolution cost ratio: {total_res_cost/total_gross*100:.2f}% of revenue")
    print("═" * 60 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Supplier BI Agent — Seed Data Generator")
    parser.add_argument("--output",  choices=["csv", "bigquery", "both"], default="csv",
                        help="Output mode (default: csv)")
    parser.add_argument("--project", default=None, help="GCP project ID (required for bigquery mode)")
    parser.add_argument("--dataset", default="supplier_bi", help="BigQuery dataset name")
    parser.add_argument("--orders",  type=int, default=TARGET_ORDERS,
                        help=f"Number of orders to generate (default: {TARGET_ORDERS:,})")
    parser.add_argument("--validate", action="store_true", default=True,
                        help="Print validation report after generation")
    args = parser.parse_args()

    if args.output in ("bigquery", "both") and not args.project:
        print("ERROR: --project is required when using bigquery output mode.")
        sys.exit(1)

    if args.output in ("bigquery", "both") and not HAS_BQ:
        print("ERROR: google-cloud-bigquery is not installed.")
        print("  Run: pip install google-cloud-bigquery")
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 60)
    print("Supplier BI Agent — Seed Data Generator")
    print("=" * 60)
    print(f"  Target orders: {args.orders:,}")
    print(f"  Date range:    {START_DATE} → {date.today()}")
    print(f"  Output mode:   {args.output}")
    print()

    # Generate suppliers
    print("Generating suppliers table...")
    suppliers = generate_suppliers()
    print(f"  ✓ {len(suppliers)} suppliers")

    # Generate orders + incidents + returns in one batch
    print("\nGenerating orders, incidents, returns...")
    end_dt = date.today() - timedelta(days=1)  # up to yesterday
    orders, incidents, returns = generate_batch(
        args.orders, START_DATE, end_dt, id_offset=0
    )

    print(f"\n  ✓ Orders:    {len(orders):,}")
    print(f"  ✓ Incidents: {len(incidents):,}")
    print(f"  ✓ Returns:   {len(returns):,}")

    # Write outputs
    if args.output in ("csv", "both"):
        print("\nWriting CSV files...")
        write_csv(suppliers, OUTPUT_DIR / "suppliers.csv",  SUPPLIERS_FIELDS)
        write_csv(orders,    OUTPUT_DIR / "orders.csv",     ORDERS_FIELDS)
        write_csv(incidents, OUTPUT_DIR / "incidents.csv",  INCIDENTS_FIELDS)
        write_csv(returns,   OUTPUT_DIR / "returns.csv",    RETURNS_FIELDS)
        print(f"\n  All CSVs written to: {OUTPUT_DIR.resolve()}/")

    if args.output in ("bigquery", "both"):
        print("\nLoading to BigQuery...")
        if not HAS_BQ:
            print("  google-cloud-bigquery not installed — skipping.")
        else:
            load_to_bigquery(args.project, args.dataset, "suppliers", suppliers, BQ_SCHEMAS["suppliers"])
            load_to_bigquery(args.project, args.dataset, "orders",    orders,    BQ_SCHEMAS["orders"])
            load_to_bigquery(args.project, args.dataset, "incidents", incidents, BQ_SCHEMAS["incidents"])
            load_to_bigquery(args.project, args.dataset, "returns",   returns,   BQ_SCHEMAS["returns"])

    # Validation
    if args.validate:
        print_validation(orders, incidents, returns, suppliers)

    print("Done.")


if __name__ == "__main__":
    main()
