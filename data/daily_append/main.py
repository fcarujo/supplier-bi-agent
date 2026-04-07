"""
Supplier BI Agent — Daily Append Cloud Function
================================================
Generates ~250 realistic rows per day and appends them to BigQuery.
Incident and return rates match the seed script exactly:
  - ~10-13% incident rate overall
  - ~5-6% return rate overall
  - High-incident categories (Electronics, Toys & Games) slightly elevated
  - SUP003 spike maintained
  - supplier_direct channel has elevated lost_item rate

Deploy command:
    gcloud functions deploy supplier-bi-daily-append \
        --gen2 \
        --runtime python311 \
        --region europe-west2 \
        --source data/daily_append/ \
        --entry-point append_daily \
        --trigger-topic supplier-bi-daily-trigger \
        --memory 512MB \
        --timeout 120s \
        --set-env-vars GCP_PROJECT=supplier-bi-agent-2025,BQ_DATASET=supplier_bi \
        --project supplier-bi-agent-2025

requirements.txt:
    google-cloud-bigquery==3.25.0
    functions-framework==3.8.1
"""

import base64
import json
import os
import random
import uuid
from datetime import date, timedelta

import functions_framework
from google.cloud import bigquery

# ── Configuration ─────────────────────────────────────────────────────────────
GCP_PROJECT  = os.environ.get("GCP_PROJECT", "supplier-bi-agent-2025")
BQ_DATASET   = os.environ.get("BQ_DATASET",  "supplier_bi")
DAILY_ORDERS = 250   # target before natural variance

# ── Suppliers — must match seed_data.py exactly ───────────────────────────────
# (id, tier, region, volume_weight, incident_multiplier)
SUPPLIERS = [
    ("SUP001", "preferred",    "North America", 0.18,  1.2),
    ("SUP002", "preferred",    "Europe",        0.16,  1.0),
    ("SUP003", "preferred",    "Asia Pacific",  0.15,  0.95),  # spike supplier
    ("SUP004", "preferred",    "North America", 0.14,  0.8),
    ("SUP005", "preferred",    "Asia Pacific",  0.12,  1.1),
    ("SUP006", "standard",     "Europe",        0.018, 1.4),
    ("SUP007", "standard",     "North America", 0.017, 1.0),
    ("SUP008", "standard",     "North America", 0.017, 1.3),
    ("SUP009", "standard",     "Europe",        0.016, 1.2),
    ("SUP010", "standard",     "South America", 0.016, 0.7),
    ("SUP011", "standard",     "Middle East",   0.015, 0.6),
    ("SUP012", "standard",     "Europe",        0.015, 1.0),
    ("SUP013", "standard",     "North America", 0.014, 0.9),
    ("SUP014", "standard",     "Asia Pacific",  0.014, 1.3),
    ("SUP015", "standard",     "North America", 0.013, 1.1),
    ("SUP016", "standard",     "Asia Pacific",  0.013, 0.9),
    ("SUP017", "standard",     "Europe",        0.012, 0.85),
    ("SUP018", "probationary", "South America", 0.011, 1.6),
    ("SUP019", "probationary", "North America", 0.010, 1.5),
    ("SUP020", "probationary", "Asia Pacific",  0.009, 1.7),
]

_total_vol       = sum(s[3] for s in SUPPLIERS)
SUPPLIER_IDS     = [s[0] for s in SUPPLIERS]
SUPPLIER_WEIGHTS = [s[3] / _total_vol for s in SUPPLIERS]
SUPPLIER_MAP     = {s[0]: s for s in SUPPLIERS}

SPIKE_SUPPLIER   = "SUP003"
SPIKE_START_DATE = date.today() - timedelta(days=90)
SPIKE_MULTIPLIER = 1.8

BASE_INCIDENT_RATE = 0.10
BASE_RETURN_RATE   = 0.05

HIGH_INCIDENT_CATEGORIES = {"Electronics", "Toys & Games"}

# ── Categories and SKUs ───────────────────────────────────────────────────────
CATEGORIES = [
    "Electronics", "Home & Garden", "Clothing & Apparel",
    "Sports & Outdoors", "Toys & Games", "Beauty & Health", "Kitchen & Dining",
]
CATEGORY_PREFIXES = {
    "Electronics":       "ELC",
    "Home & Garden":     "HMG",
    "Clothing & Apparel":"CLT",
    "Sports & Outdoors": "SPT",
    "Toys & Games":      "TOY",
    "Beauty & Health":   "BTY",
    "Kitchen & Dining":  "KIT",
}
PRICE_RANGES = {
    "budget":  (9.99,  49.99),
    "mid":     (50.00, 149.99),
    "premium": (150.0, 599.99),
}

# ── Fulfilment ────────────────────────────────────────────────────────────────
FULFILMENT_CHANNELS  = ["warehouse", "supplier_direct", "third_party_logistics"]
FULFILMENT_WEIGHTS   = [0.60, 0.25, 0.15]

# ── Incident types ────────────────────────────────────────────────────────────
INCIDENT_TYPES       = ["damage_defect", "missing_parts", "lost_item", "misinformation", "mis_shipped"]
INCIDENT_WEIGHTS     = [0.35, 0.20, 0.15, 0.15, 0.15]
INCIDENT_WEIGHTS_DIR = [0.25, 0.15, 0.40, 0.10, 0.10]   # supplier_direct elevated lost_item

RESOLUTIONS          = ["full_refund", "full_replacement", "replacement_part", "discount_to_keep"]
RESOLUTION_WEIGHTS   = [0.30, 0.25, 0.25, 0.20]

RETURN_REASONS       = ["didnt_like", "doesnt_need", "not_up_to_expectations"]
RETURN_REASON_WEIGHTS= [0.30, 0.25, 0.45]

RESOLUTION_STATUSES  = ["open", "in_progress", "resolved", "escalated"]

# ── Comment pools ─────────────────────────────────────────────────────────────
INCIDENT_COMMENTS = [
    "The item arrived with visible damage — the casing was cracked on one side.",
    "Product failed within hours of first use, apparent manufacturing defect.",
    "Package was marked as delivered but was not found at my address.",
    "The dimensions listed were incorrect — the item is significantly smaller than described.",
    "Received the wrong variant — my order confirmation shows a different model.",
    "Several key components were missing from the box despite sealed packaging.",
    "The item stopped functioning after minimal use, possible internal fault.",
    "Tracking shows delivered but the item was not at my address or with neighbours.",
    "The material does not match the description on the product listing.",
    "Received a completely different product from what I ordered.",
    "Significant cosmetic damage on arrival despite apparently intact outer packaging.",
    "Assembly kit was missing three of the required parts — item cannot be assembled.",
]
RETURN_COMMENTS = [
    "Changed my mind after receiving — not what I was expecting.",
    "Bought this by mistake and no longer need it.",
    "Quality is below what I expected at this price point.",
    "The item does not fit as described in the listing.",
    "Colour is very different from what was shown in the product photos.",
    "No longer have a use for this item, returning unused.",
    "Personal preference — the style does not suit my needs.",
    "The material feels cheaper than the listing suggested.",
]
REVIEWS = [
    "Very disappointed — would not buy from this supplier again.",
    "Had issues from the start but the problem was eventually resolved.",
    "Average quality, does the job but nothing special.",
    "Good product once the issue was sorted out.",
    "Happy with my purchase despite a small problem on arrival.",
    "Not exactly what I expected but acceptable for the price.",
    "Excellent product, very happy with the quality.",
]


# ── Rate calculation — mirrors seed_data.py exactly ──────────────────────────

def effective_incident_rate(supplier_id, category, today):
    """Calculate incident rate using same dampened multiplier logic as seed."""
    sup            = SUPPLIER_MAP[supplier_id]
    raw_multiplier = sup[4]
    # Dampen multipliers toward 1.0 (same as seed)
    inc_multiplier = 1.0 + (raw_multiplier - 1.0) * 0.25

    # Spike supplier post-spike
    if supplier_id == SPIKE_SUPPLIER and today >= SPIKE_START_DATE:
        inc_multiplier *= SPIKE_MULTIPLIER

    base = BASE_INCIDENT_RATE
    # High-incident category boost
    if category in HIGH_INCIDENT_CATEGORIES:
        base *= 1.25

    return min(base * inc_multiplier, 0.25)


def effective_return_rate(price_tier):
    """Budget tier has 2× return rate."""
    return BASE_RETURN_RATE * (2.0 if price_tier == "budget" else 1.0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def random_sku(category):
    prefix = CATEGORY_PREFIXES[category]
    n = random.randint(1, 43)
    return f"{prefix}-{n:04d}"

def random_price_tier():
    return random.choices(["budget", "mid", "premium"], weights=[0.40, 0.40, 0.20])[0]

def resolution_cost(resolution, gross):
    if resolution in ("full_refund", "full_replacement"):
        return round(gross, 2)
    elif resolution == "replacement_part":
        return round(gross * 0.10, 2)
    else:
        return round(gross * random.uniform(0.05, 0.30), 2)

def pick_resolution_status(days_since=0):
    if days_since < 3:
        return random.choices(["open", "in_progress"], weights=[0.7, 0.3])[0]
    elif days_since < 14:
        return random.choices(["open", "in_progress", "resolved"], weights=[0.2, 0.4, 0.4])[0]
    else:
        return random.choices(["in_progress", "resolved", "escalated"], weights=[0.1, 0.8, 0.1])[0]

def today_str():
    return date.today().isoformat()

def natural_variance(target, pct=0.15):
    lo = max(1, int(target * (1 - pct)))
    hi = int(target * (1 + pct))
    return random.randint(lo, hi)


# ── Row generators ────────────────────────────────────────────────────────────

def generate_orders(n, today):
    rows = []
    for _ in range(n):
        supplier_id = random.choices(SUPPLIER_IDS, weights=SUPPLIER_WEIGHTS)[0]
        sup         = SUPPLIER_MAP[supplier_id]
        category    = random.choice(CATEGORIES)
        sku         = random_sku(category)
        price_tier  = random_price_tier()
        lo, hi      = PRICE_RANGES[price_tier]
        base_price  = round(random.uniform(lo, hi), 2)
        unit_qty    = random.choices([1,2,3,4,5], weights=[0.60,0.22,0.10,0.05,0.03])[0]
        gross       = round(base_price * unit_qty, 2)
        margin      = random.uniform(0.22, 0.38)
        net         = round(gross * margin, 2)
        cost        = round(gross - net, 2)
        fulfilment  = random.choices(FULFILMENT_CHANNELS, weights=FULFILMENT_WEIGHTS)[0]

        inc_rate    = effective_incident_rate(supplier_id, category, today)
        ret_rate    = effective_return_rate(price_tier)

        has_incident = random.random() < inc_rate
        has_return   = (not has_incident) and (random.random() < ret_rate)

        rows.append({
            "orderID":           f"ORD{uuid.uuid4().hex[:10].upper()}",
            "supplierID":        supplier_id,
            "customerID":        f"CUST{uuid.uuid4().hex[:10].upper()}",
            "productSKU":        sku,
            "productCategory":   category,
            "orderDate":         today_str(),
            "unitQuantity":      unit_qty,
            "grossRevenue":      gross,
            "netRevenue":        net,
            "productCost":       cost,
            "fulfilmentChannel": fulfilment,
            "supplierTier":      sup[1],
            "supplierRegion":    sup[2],
            "productPriceTier":  price_tier,
            "hasIncident":       has_incident,
            "hasReturn":         has_return,
        })
    return rows


def generate_incidents(today, order_rows):
    """Incidents derived directly from orders with hasIncident=True."""
    rows = []
    for o in order_rows:
        if not o["hasIncident"]:
            continue

        fulfilment = o["fulfilmentChannel"]
        if fulfilment == "supplier_direct":
            inc_type = random.choices(INCIDENT_TYPES, weights=INCIDENT_WEIGHTS_DIR)[0]
        else:
            inc_type = random.choices(INCIDENT_TYPES, weights=INCIDENT_WEIGHTS)[0]

        resolution = random.choices(RESOLUTIONS, weights=RESOLUTION_WEIGHTS)[0]
        res_cost   = resolution_cost(resolution, o["grossRevenue"])
        res_status = pick_resolution_status(0)
        rating     = random.choices([1,2,3,4,5], weights=[0.35,0.30,0.20,0.10,0.05])[0]

        rows.append({
            "incidentID":                     f"INC{uuid.uuid4().hex[:10].upper()}",
            "orderID":                        o["orderID"],
            "supplierID":                     o["supplierID"],
            "productSKU":                     o["productSKU"],
            "productCategory":                o["productCategory"],
            "incidentDate":                   today_str(),
            "resolutionDate":                 None,
            "resolutionStatus":               res_status,
            "daysBetweenPurchaseAndIncident": random.randint(0, 30),
            "incidentType":                   inc_type,
            "incidentResolution":             resolution,
            "resolutionCost":                 res_cost,
            "incidentCustomerComment":        random.choice(INCIDENT_COMMENTS),
            "productRating":                  rating,
            "customerReview":                 random.choice(REVIEWS),
        })
    return rows


def generate_returns(today, order_rows):
    """Returns derived directly from orders with hasReturn=True."""
    rows = []
    for o in order_rows:
        if not o["hasReturn"]:
            continue

        reason     = random.choices(RETURN_REASONS, weights=RETURN_REASON_WEIGHTS)[0]
        res_status = pick_resolution_status(0)
        rating     = random.choices([1,2,3,4,5], weights=[0.15,0.25,0.35,0.20,0.05])[0]

        rows.append({
            "returnID":                      f"RET{uuid.uuid4().hex[:10].upper()}",
            "orderID":                       o["orderID"],
            "supplierID":                    o["supplierID"],
            "productSKU":                    o["productSKU"],
            "productCategory":               o["productCategory"],
            "returnDate":                    today_str(),
            "resolutionDate":                None,
            "resolutionStatus":              res_status,
            "daysBetweenPurchaseAndReturn":  random.randint(1, 30),
            "buyersRemorseReason":           reason,
            "buyersRemorseComment":          random.choice(RETURN_COMMENTS),
            "productRating":                 rating,
            "customerReview":                random.choice(REVIEWS),
        })
    return rows


# ── BigQuery append ───────────────────────────────────────────────────────────

def append_to_bq(client, table_id, rows):
    if not rows:
        return
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors for {table_id}: {errors}")
    print(f"  ✓ Appended {len(rows):,} rows → {table_id}")


# ── Entry point ───────────────────────────────────────────────────────────────

@functions_framework.cloud_event
def append_daily(cloud_event):
    if cloud_event.data and "message" in cloud_event.data:
        msg_data = cloud_event.data["message"].get("data", "")
        if msg_data:
            decoded = base64.b64decode(msg_data).decode("utf-8")
            print(f"Trigger message: {decoded}")

    today = date.today()
    print(f"Daily append job running for: {today}")

    client   = bigquery.Client(project=GCP_PROJECT)
    n_orders = natural_variance(DAILY_ORDERS)
    print(f"  Generating {n_orders} orders...")

    orders    = generate_orders(n_orders, today)
    incidents = generate_incidents(today, orders)
    returns   = generate_returns(today, orders)

    append_to_bq(client, f"{GCP_PROJECT}.{BQ_DATASET}.orders",    orders)
    append_to_bq(client, f"{GCP_PROJECT}.{BQ_DATASET}.incidents", incidents)
    append_to_bq(client, f"{GCP_PROJECT}.{BQ_DATASET}.returns",   returns)

    inc_rate = round(len(incidents) / len(orders) * 100, 1) if orders else 0
    ret_rate = round(len(returns)   / len(orders) * 100, 1) if orders else 0

    summary = {
        "date":          today.isoformat(),
        "orders":        len(orders),
        "incidents":     len(incidents),
        "returns":       len(returns),
        "incident_rate": inc_rate,
        "return_rate":   ret_rate,
        "total_rows":    len(orders) + len(incidents) + len(returns),
    }
    print(f"Daily append complete: {json.dumps(summary)}")
    return summary