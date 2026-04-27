"""
Set Firebase custom claims on all user accounts.

Claims:
  admin     → { role: "admin" }
  business  → { role: "business" }
  suppliers → { role: "supplier", supplierID: "SUPXXX" }

Usage:
  python scripts/set_firebase_claims.py
"""

import firebase_admin
from firebase_admin import credentials, auth
from pathlib import Path

# Initialise Firebase Admin SDK
cred = credentials.Certificate(
    Path(__file__).parent.parent / "firebase-service-account.json"
)
firebase_admin.initialize_app(cred)

USERS = [
    {
        "email":  "admin@agentic-intel.de",
        "claims": {"role": "admin"},
    },
    {
        "email":  "business@agentic-intel.de",
        "claims": {"role": "business"},
    },
    {
        "email":  "demo@agentic-intel.de",
        "claims": {"role": "demo"},
    },
    {
        "email":  "sup001@agentic-intel.de",
        "claims": {"role": "supplier", "supplierID": "SUP001"},
    },
    {
        "email":  "sup002@agentic-intel.de",
        "claims": {"role": "supplier", "supplierID": "SUP002"},
    },
    {
        "email":  "sup003@agentic-intel.de",
        "claims": {"role": "supplier", "supplierID": "SUP003"},
    },
]

for user in USERS:
    try:
        firebase_user = auth.get_user_by_email(user["email"])
        auth.set_custom_user_claims(firebase_user.uid, user["claims"])
        print(f"✓  {user['email']} → {user['claims']}")
    except Exception as e:
        print(f"✗  {user['email']} → ERROR: {e}")

print("\nDone. Custom claims set on all accounts.")
