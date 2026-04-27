# -*- coding: utf-8 -*-
"""
Rotas FastAPI para Prospeccao B2B (Google Maps Places).
Importado por agent-manager.py e registrado como subapp.
"""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request

# Importacoes do agent-manager (compartilhamos db_query e log)
import importlib

# placeholder, sobrescrito na hora do register
def db_query(*a, **kw):
    raise RuntimeError("b2b_routes.db_query nao foi inicializado")

def log(*a, **kw):
    print(*a)


def init(host_db_query, host_log):
    """Chamado pelo agent-manager para injetar db_query e log."""
    global db_query, log
    db_query = host_db_query
    log = host_log


from business_segments import (
    BUSINESS_SEGMENTS, BRAZILIAN_STATES, is_person_profile, label_for_value
)
import places_service as places

router = APIRouter()


# --------------------------- CATEGORIAS ---------------------------

@router.get("/api/prospect-b2b/categories")
async def b2b_categories():
    """Lista hierarquica de categorias para popular o dropdown."""
    segs = []
    for key, seg in BUSINESS_SEGMENTS.items():
        types = sorted(seg["types"], key=lambda t: t["label"])
        segs.append({"key": key, "label": seg["label"], "types": types})
    return {"segments": segs, "states": BRAZILIAN_STATES}


# --------------------------- TARGETS ---------------------------

@router.get("/api/prospect-b2b/targets")
async def b2b_list_targets():
    rows = db_query(
        """SELECT id, business_type, business_label, segment, location_text,
                  origin_lat, origin_lng, radius_meters, max_results, active,
                  created_at, last_run_at
             FROM prospect_b2b_targets
            ORDER BY created_at DESC""",
        fetchall=True,
    ) or []
    return {"targets": rows}


@router.post("/api/prospect-b2b/targets")
async def b2b_add_target(request: Request):
    data = await request.json()
    business_type = (data.get("business_type") or "").strip()
    location_text = (data.get("location_text") or "").strip()
    if not business_type or not location_text:
        return {"ok": False, "error": "business_type e location_text obrigatorios"}
    seg, label = label_for_value(business_type)
    radius = int(data.get("radius_meters") or 5000)
    max_results = int(data.get("max_results") or 60)

    # Tenta geocode logo no insert pra economizar depois
    origin_lat = data.get("origin_lat")
    origin_lng = data.get("origin_lng")
    if not origin_lat or not origin_lng:
        try:
            origin = places.geocode(location_text)
            origin_lat = origin["lat"]
            origin_lng = origin["lng"]
        except Exception as e:
            log(f"[b2b] geocode falhou: {e}")

    row = db_query(
        """INSERT INTO prospect_b2b_targets
              (business_type, business_label, segment, location_text,
               origin_lat, origin_lng, radius_meters, max_results)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (business_type, label, seg, location_text,
         origin_lat, origin_lng, radius, max_results),
        fetchone=True, commit=True
    )
    return {"ok": True, "id": row["id"], "segment": seg, "business_label": label,
            "origin_lat": origin_lat, "origin_lng": origin_lng}


@router.delete("/api/prospect-b2b/targets/{target_id}")
async def b2b_delete_target(target_id: int):
    db_query("DELETE FROM prospect_b2b_targets WHERE id = %s", (target_id,), commit=True)
    return {"ok": True}


# --------------------------- COMPANIES ---------------------------

@router.get("/api/prospect-b2b/companies")
async def b2b_list_companies(
    request: Request,
    status: str = "",
    target_id: int = 0,
    limit: int = 200,
    offset: int = 0,
    q: str = ""
):
    where = []
    params = []
    if status:
        where.append("status = %s")
        params.append(status)
    if target_id:
        where.append("target_id = %s")
        params.append(target_id)
    if q:
        where.append("(name ILIKE %s OR formatted_address ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    rows = db_query(
        f"""SELECT id, place_id, target_id, name, formatted_address, phone,
                   whatsapp, website, instagram, rating, user_ratings_total,
                   types, business_label, segment, lat, lng, is_person_profile,
                   status, discovered_at, updated_at
              FROM prospect_b2b_companies
              {where_sql}
              ORDER BY discovered_at DESC
              LIMIT %s OFFSET %s""",
        tuple(params + [limit, offset]),
        fetchall=True,
    ) or []
    total = (db_query(
        f"SELECT COUNT(*) AS c FROM prospect_b2b_companies {where_sql}",
        tuple(params), fetchone=True
    ) or {"c": 0})["c"]
    return {"companies": rows, "total": total}


@router.get("/api/prospect-b2b/companies/{company_id}")
async def b2b_company_detail(company_id: int):
    row = db_query(
        "SELECT * FROM prospect_b2b_companies WHERE id = %s",
        (company_id,), fetchone=True,
    )
    if not row:
        return {"ok": False, "error": "company not found"}
    msgs = db_query(
        "SELECT * FROM prospect_b2b_messages WHERE company_id = %s ORDER BY sent_at DESC",
        (company_id,), fetchall=True,
    ) or []
    return {"ok": True, "company": row, "messages": msgs}


@router.patch("/api/prospect-b2b/companies/{company_id}")
async def b2b_company_update(company_id: int, request: Request):
    body = await request.json()
    fields = []
    vals = []
    for k in ("status", "notes", "whatsapp", "instagram"):
        if k in body:
            fields.append(f"{k} = %s")
            vals.append(body[k])
    if not fields:
        return {"ok": False, "error": "nada para atualizar"}
    vals.append(company_id)
    db_query(
        f"UPDATE prospect_b2b_companies SET {', '.join(fields)} WHERE id = %s",
        tuple(vals), commit=True
    )
    return {"ok": True}


@router.post("/api/prospect-b2b/companies/{company_id}/approve")
async def b2b_company_approve(company_id: int):
    db_query(
        "UPDATE prospect_b2b_companies SET status = 'approved_to_send' WHERE id = %s",
        (company_id,), commit=True,
    )
    return {"ok": True}


@router.post("/api/prospect-b2b/companies/bulk-approve")
async def b2b_company_bulk_approve(request: Request):
    body = await request.json()
    ids = body.get("ids") or []
    if not ids:
        return {"ok": False, "error": "ids vazio"}
    db_query(
        """UPDATE prospect_b2b_companies
              SET status = 'approved_to_send'
            WHERE id = ANY(%s) AND status IN ('discovered','qualified')""",
        (list(ids),), commit=True,
    )
    return {"ok": True, "count": len(ids)}


# --------------------------- RUNS ---------------------------

@router.get("/api/prospect-b2b/runs")
async def b2b_list_runs(limit: int = 50):
    rows = db_query(
        """SELECT r.*, t.business_label, t.location_text
             FROM prospect_b2b_runs r
        LEFT JOIN prospect_b2b_targets t ON t.id = r.target_id
            ORDER BY r.started_at DESC LIMIT %s""",
        (limit,), fetchall=True,
    ) or []
    return {"runs": rows}


# --------------------------- DISCOVERY ---------------------------

def _persist_company(row: dict, target_id: Optional[int]) -> str:
    """Insere ou atualiza company. Retorna 'inserted' | 'updated' | 'skipped'."""
    place_id = row.get("place_id")
    if not place_id:
        return "skipped"
    existing = db_query(
        "SELECT id FROM prospect_b2b_companies WHERE place_id = %s",
        (place_id,), fetchone=True,
    )
    is_person = is_person_profile(row.get("name") or "")
    seg, label = (None, None)
    if target_id:
        target = db_query(
            "SELECT segment, business_label FROM prospect_b2b_targets WHERE id = %s",
            (target_id,), fetchone=True,
        )
        if target:
            seg = target.get("segment")
            label = target.get("business_label")

    instagram = None
    if row.get("website"):
        try:
            instagram = places.extract_instagram_from_website(row["website"])
        except Exception:
            instagram = None

    whatsapp = places.normalize_phone_to_whatsapp(row.get("phone") or "")

    if existing:
        db_query(
            """UPDATE prospect_b2b_companies
                  SET name = %s, formatted_address = %s, phone = %s,
                      whatsapp = COALESCE(%s, whatsapp),
                      website = %s,
                      instagram = COALESCE(%s, instagram),
                      rating = %s, user_ratings_total = %s,
                      opening_hours = %s, types = %s,
                      lat = %s, lng = %s,
                      is_person_profile = %s,
                      raw = %s
                WHERE id = %s""",
            (
                row.get("name"), row.get("formatted_address"), row.get("phone"),
                whatsapp, row.get("website"), instagram,
                row.get("rating"), row.get("user_ratings_total"),
                json.dumps(row.get("opening_hours")) if row.get("opening_hours") else None,
                row.get("types") or [],
                row.get("lat"), row.get("lng"),
                is_person, json.dumps(row.get("raw") or {}),
                existing["id"],
            ),
            commit=True,
        )
        return "updated"
    else:
        db_query(
            """INSERT INTO prospect_b2b_companies
                   (place_id, target_id, name, formatted_address, phone, whatsapp,
                    website, instagram, rating, user_ratings_total, opening_hours,
                    types, business_label, segment, lat, lng,
                    is_person_profile, raw, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, 'discovered')""",
            (
                place_id, target_id, row.get("name"),
                row.get("formatted_address"), row.get("phone"), whatsapp,
                row.get("website"), instagram,
                row.get("rating"), row.get("user_ratings_total"),
                json.dumps(row.get("opening_hours")) if row.get("opening_hours") else None,
                row.get("types") or [], label, seg,
                row.get("lat"), row.get("lng"), is_person,
                json.dumps(row.get("raw") or {}),
            ),
            commit=True,
        )
        return "inserted"


def run_discovery_for_target(target_id: int, trigger: str = "manual") -> dict:
    """Executa discovery para um target especifico. Retorna stats."""
    target = db_query(
        "SELECT * FROM prospect_b2b_targets WHERE id = %s",
        (target_id,), fetchone=True,
    )
    if not target:
        return {"ok": False, "error": "target not found"}

    run = db_query(
        """INSERT INTO prospect_b2b_runs (target_id, trigger, status)
           VALUES (%s, %s, 'running') RETURNING id""",
        (target_id, trigger), fetchone=True, commit=True,
    )
    run_id = run["id"]
    log(f"[b2b] run {run_id} iniciado target={target_id} type={target['business_type']}")

    new_count = 0
    duplicates = 0
    cross_channel = 0
    api_calls = 0
    cost = 0.0
    error_msg = None

    try:
        result = places.search_businesses(
            business_type=target["business_type"],
            location_input=target["location_text"],
            radius_meters=int(target["radius_meters"]),
            max_results=int(target["max_results"]),
            include_details=True,
        )
        api_calls = result["api_calls"]
        cost = result["estimated_cost_usd"]

        for c in result["companies"]:
            verdict = _persist_company(c, target_id)
            if verdict == "inserted":
                new_count += 1
            elif verdict == "updated":
                duplicates += 1

        cross_count_row = db_query(
            """SELECT COUNT(*) AS c FROM prospect_b2b_companies
                WHERE target_id = %s AND instagram IS NOT NULL""",
            (target_id,), fetchone=True,
        )
        cross_channel = (cross_count_row or {"c": 0})["c"]

        db_query(
            """UPDATE prospect_b2b_runs
                  SET status = 'completed',
                      discovered_count = %s,
                      new_count = %s,
                      duplicates_count = %s,
                      cross_channel_count = %s,
                      api_calls_count = %s,
                      estimated_cost_usd = %s,
                      finished_at = now()
                WHERE id = %s""",
            (
                len(result["companies"]), new_count, duplicates,
                cross_channel, api_calls, cost, run_id,
            ),
            commit=True,
        )
        db_query(
            "UPDATE prospect_b2b_targets SET last_run_at = now() WHERE id = %s",
            (target_id,), commit=True,
        )
    except Exception as e:
        error_msg = str(e)
        log(f"[b2b] run {run_id} ERRO: {error_msg}")
        db_query(
            """UPDATE prospect_b2b_runs SET status = 'failed', error = %s,
                                            finished_at = now() WHERE id = %s""",
            (error_msg[:1000], run_id), commit=True,
        )

    return {
        "ok": error_msg is None,
        "run_id": run_id,
        "discovered": (
            len(result["companies"]) if not error_msg and "result" in dir() else 0
        ),
        "new": new_count,
        "duplicates": duplicates,
        "cross_channel_total": cross_channel,
        "api_calls": api_calls,
        "estimated_cost_usd": cost,
        "error": error_msg,
    }


@router.post("/api/prospect-b2b/run-now")
async def b2b_run_now(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    target_id = body.get("target_id")
    trigger = body.get("trigger") or "manual"

    if not target_id:
        # rodar para todos os targets ativos sequencialmente
        targets = db_query(
            "SELECT id FROM prospect_b2b_targets WHERE active = true ORDER BY created_at ASC",
            fetchall=True,
        ) or []
        if not targets:
            return {"ok": False, "error": "nenhum target ativo"}
        results = []
        for t in targets:
            r = run_discovery_for_target(t["id"], trigger)
            results.append(r)
        return {"ok": True, "runs": results}
    else:
        r = run_discovery_for_target(int(target_id), trigger)
        return r


# --------------------------- STATS ---------------------------

@router.get("/api/prospect-b2b/stats")
async def b2b_stats():
    targets = (db_query(
        "SELECT COUNT(*) AS c FROM prospect_b2b_targets WHERE active = true",
        fetchone=True
    ) or {"c": 0})["c"]
    by_status = db_query(
        """SELECT status, COUNT(*) AS c FROM prospect_b2b_companies GROUP BY status""",
        fetchall=True
    ) or []
    status_map = {r["status"]: r["c"] for r in by_status}
    total_companies = sum(status_map.values())
    qualified = status_map.get("qualified", 0)
    approved = status_map.get("approved_to_send", 0)
    sent = status_map.get("dm_sent", 0)
    replied = status_map.get("replied", 0)
    discovered = status_map.get("discovered", 0)
    with_whatsapp = (db_query(
        "SELECT COUNT(*) AS c FROM prospect_b2b_companies WHERE whatsapp IS NOT NULL",
        fetchone=True
    ) or {"c": 0})["c"]
    with_instagram = (db_query(
        "SELECT COUNT(*) AS c FROM prospect_b2b_companies WHERE instagram IS NOT NULL",
        fetchone=True
    ) or {"c": 0})["c"]
    last_run = db_query(
        "SELECT * FROM prospect_b2b_runs ORDER BY started_at DESC LIMIT 1",
        fetchone=True,
    )
    return {
        "targets_active": targets,
        "total_companies": total_companies,
        "discovered": discovered,
        "qualified": qualified,
        "approved": approved,
        "sent": sent,
        "replied": replied,
        "with_whatsapp": with_whatsapp,
        "with_instagram": with_instagram,
        "last_run": last_run,
    }
