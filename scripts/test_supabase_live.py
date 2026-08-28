#!/usr/bin/env python3
"""Live API + Supabase integration checks (run against .env config)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import reset_store_client
from backend.integrations.supabase_store_client import SupabaseStoreClient
from backend.main import app
from backend.services import store_source


USER_ID = "cmqm2iwuy0000kvsqj1nqf090"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def main() -> None:
    get_settings.cache_clear()
    reset_store_client()
    settings = get_settings()
    init_db()

    print("=== Config ===")
    print(f"  store_provider={settings.resolved_store_provider}")
    print(f"  supabase_url={settings.effective_supabase_url}")
    print(f"  demo_user_id={settings.demo_user_id}")
    print(f"  completed_status={settings.supabase_order_completed_status}")

    # --- Health via TestClient ---
    print("\n=== GET /health ===")
    client = TestClient(app)
    health = client.get("/health")
    if health.status_code != 200:
        fail(f"/health status {health.status_code}: {health.text[:200]}")
    body = health.json()
    print(json.dumps(body, indent=2))

    if body.get("catalog_source") != "supabase":
        fail(f"catalog_source expected supabase, got {body.get('catalog_source')!r}")
    ok("catalog_source=supabase")

    if body.get("store_provider") != "supabase":
        fail(f"store_provider expected supabase, got {body.get('store_provider')!r}")
    ok("store_provider=supabase")

    if not body.get("supabase_configured"):
        fail("supabase_configured is false")
    ok("supabase_configured=true")

    if body.get("demo_user_id") != USER_ID:
        fail(f"demo_user_id mismatch: {body.get('demo_user_id')}")
    ok(f"demo_user_id={USER_ID}")

    # --- Meta ---
    print("\n=== GET /api/meta ===")
    meta = client.get("/api/meta").json()
    print(json.dumps(meta, indent=2))
    if meta.get("catalog_source") != "supabase":
        fail("meta catalog_source not supabase")
    ok("meta catalog_source=supabase")

    # --- Direct Supabase ---
    print("\n=== Direct Supabase (products) ===")
    store = SupabaseStoreClient()
    if not store.configured:
        fail("SupabaseStoreClient not configured")
    try:
        products = store.list_products()
    except Exception as exc:
        fail(f"Supabase list_products: {exc}")
    print(f"  products returned: {len(products)}")
    if products:
        sample = products[0]
        print(f"  sample: id={sample.id!r} name={sample.name!r} price_inr={sample.price_inr}")
        ok(f"list_products ({len(products)} rows)")
    else:
        print("  WARN: zero products (table empty or RLS blocking)")

    print("\n=== Direct Supabase (usual order) ===")
    try:
        usual = store.get_usual_order(USER_ID)
    except Exception as exc:
        fail(f"Supabase get_usual_order: {exc}")
    print(json.dumps(usual.model_dump(), indent=2))
    if usual.order_id:
        ok(f"usual order found: {usual.order_id} total_inr={usual.total_inr}")
    else:
        print("  WARN: no DELIVERED order for user (check status filter or user_id)")

    # --- API routes (through store_source) ===
    print("\n=== GET /api/products ===")
    r = client.get("/api/products")
    if r.status_code != 200:
        fail(f"/api/products status {r.status_code}: {r.text[:300]}")
    pdata = r.json()
    print(f"  count={pdata.get('count')}")
    in_stock = [p for p in pdata.get("products", []) if p.get("stock", 0) > 0]
    print(f"  in_stock={len(in_stock)}")
    if pdata.get("count", 0) > 0:
        sample = pdata["products"][0]
        print(
            f"  sample stock: {sample.get('name')} stock={sample.get('stock')}"
        )
    if pdata.get("count", 0) == 0 and len(products) == 0:
        print("  WARN: API products empty (same as direct Supabase)")
    elif pdata.get("count", 0) == 0 and len(products) > 0:
        fail("API products empty but direct Supabase had rows")
    else:
        ok(f"/api/products count={pdata.get('count')}")

    print("\n=== GET /api/users/{id}/usual ===")
    r = client.get(f"/api/users/{USER_ID}/usual")
    if r.status_code != 200:
        fail(f"/api/users/usual status {r.status_code}: {r.text[:300]}")
    udata = r.json()
    print(json.dumps(udata, indent=2))
    if usual.order_id and not udata.get("order_id"):
        fail("API usual empty but direct Supabase had order")
    if udata.get("order_id"):
        ok(f"/api/users/usual order_id={udata.get('order_id')} total={udata.get('total_inr')}")
    else:
        print("  WARN: API usual order empty")

    # --- Optional hero flow smoke ---
    if pdata.get("count", 0) > 0:
        print("\n=== POST /api/proposals (usual-or-bestsellers + budget 800) ===")
        prop = client.post(
            "/api/proposals",
            json={
                "user_id": USER_ID,
                "use_usual": True,
                "stated_budget_inr": 800,
                "with_growth": True,
            },
        )
        if prop.status_code != 200:
            fail(f"create proposal {prop.status_code}: {prop.text[:300]}")
        proposal = prop.json()
        print(json.dumps(
            {
                "id": proposal.get("id"),
                "status": proposal.get("status"),
                "total_inr": proposal.get("total_inr"),
                "items": len(proposal.get("items", [])),
                "proposal_source": proposal.get("proposal_source"),
                "source_reason": proposal.get("source_reason"),
            },
            indent=2,
        ))
        expected_source = "completed_order_history" if usual.order_id else "bestsellers"
        if proposal.get("proposal_source") != expected_source:
            fail(
                f"proposal source expected {expected_source}, "
                f"got {proposal.get('proposal_source')}"
            )
        ok(
            f"proposal created source={proposal.get('proposal_source')} "
            f"id={proposal.get('id')} total={proposal.get('total_inr')}"
        )

    print("\n=== SUMMARY ===")
    print(json.dumps({"status": "supabase_live_tests_passed"}, indent=2))


if __name__ == "__main__":
    main()
