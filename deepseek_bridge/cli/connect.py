"""CLI Generator untuk Base URL & API Key, serta auto-inject ke 9router."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from ..core import config
from ..state.apikeys import get_store, ApiKey


DEFAULT_9ROUTER_URL = "http://localhost:20128"
DEFAULT_9ROUTER_PASS = "123456"


def get_published_models() -> List[str]:
    """Ambil daftar model yang dipublikasikan dari session/models.json atau config."""
    try:
        models_file = Path(getattr(config, "MODELS_FILE", config.ROOT / "session" / "models.json"))
        if models_file.exists():
            data = json.loads(models_file.read_text(encoding="utf-8"))
            pub = data.get("published", [])
            if pub:
                return pub
    except Exception:
        pass
    return list(getattr(config, "PUBLISHED_MODELS", ["dsk-auto"]))


def inject_to_9router(
    base_url: str,
    api_key: str,
    router_url: str = DEFAULT_9ROUTER_URL,
    router_pass: str = DEFAULT_9ROUTER_PASS,
    enabled_models: Optional[List[str]] = None,
) -> Dict[str, any]:
    """Otomatis mendaftarkan/memperbarui provider node dan connection di 9router."""
    res = {
        "connected": False,
        "success": False,
        "node_id": None,
        "node_name": None,
        "conn_id": None,
        "conn_name": None,
        "error": None,
    }

    s = requests.Session()
    # 1. Login ke 9router
    try:
        login_resp = s.post(
            f"{router_url.rstrip('/')}/api/auth/login",
            json={"password": router_pass},
            timeout=5,
        )
        if login_resp.status_code != 200:
            res["error"] = f"Login 9router gagal (HTTP {login_resp.status_code}): {login_resp.text[:100]}"
            return res
        res["connected"] = True
    except requests.exceptions.RequestException as exc:
        res["error"] = f"Tidak dapat terhubung ke 9router di {router_url} ({exc})"
        return res

    # 2. Periksa / buat Provider Node
    node_id = None
    node_name = "deepseek-bridge"
    prefix = "dsk"
    try:
        r_nodes = s.get(f"{router_url.rstrip('/')}/api/provider-nodes", timeout=8)
        if r_nodes.status_code == 200:
            data = r_nodes.json()
            nodes_list = data if isinstance(data, list) else data.get("nodes", [])
            for n in nodes_list:
                if (
                    str(n.get("name", "")).lower() == "deepseek-bridge"
                    or str(n.get("prefix", "")).lower() == "dsk"
                    or "8787" in str(n.get("baseUrl", ""))
                ):
                    node_id = n.get("id")
                    node_name = n.get("name") or "deepseek-bridge"
                    prefix = n.get("prefix") or "dsk"
                    # Update baseUrl jika berbeda
                    if n.get("baseUrl") != base_url:
                        update_body = {
                            "name": node_name,
                            "prefix": prefix,
                            "type": n.get("type", "openai-compatible"),
                            "apiType": n.get("apiType", "chat"),
                            "baseUrl": base_url,
                        }
                        s.put(
                            f"{router_url.rstrip('/')}/api/provider-nodes/{node_id}",
                            json=update_body,
                            timeout=8,
                        )
                    break
    except Exception as exc:
        res["error"] = f"Gagal membaca node 9router: {exc}"
        return res

    # Jika node belum ada, buat baru
    if not node_id:
        try:
            create_node_body = {
                "name": "deepseek-bridge",
                "prefix": "dsk",
                "type": "openai-compatible",
                "apiType": "chat",
                "baseUrl": base_url,
            }
            rn = s.post(
                f"{router_url.rstrip('/')}/api/provider-nodes",
                json=create_node_body,
                timeout=10,
            )
            if rn.status_code in (200, 201):
                j = rn.json()
                node_id = j.get("id") or (j.get("node") or {}).get("id")
                node_name = "deepseek-bridge"
            else:
                res["error"] = f"Gagal membuat node di 9router (HTTP {rn.status_code}): {rn.text[:150]}"
                return res
        except Exception as exc:
            res["error"] = f"Gagal membuat node di 9router: {exc}"
            return res

    res["node_id"] = node_id
    res["node_name"] = node_name

    # 3. Periksa / buat Connection untuk node ini
    conn_id = None
    conn_name = "Xenonite"
    try:
        r_conns = s.get(f"{router_url.rstrip('/')}/api/providers", timeout=8)
        if r_conns.status_code == 200:
            data = r_conns.json()
            conns_list = data.get("connections", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for c in conns_list:
                if c.get("provider") == node_id:
                    conn_id = c.get("id")
                    conn_name = c.get("name") or conn_name
                    psd = dict(c.get("providerSpecificData") or {})
                    psd["baseUrl"] = base_url
                    if enabled_models:
                        psd["enabledModels"] = enabled_models
                    
                    update_conn_body = {
                        "provider": node_id,
                        "authType": c.get("authType", "apikey"),
                        "name": conn_name,
                        "apiKey": api_key,
                        "priority": c.get("priority", 1),
                        "isActive": True,
                        "providerSpecificData": psd,
                    }
                    rc = s.put(
                        f"{router_url.rstrip('/')}/api/providers/{conn_id}",
                        json=update_conn_body,
                        timeout=10,
                    )
                    if rc.status_code in (200, 201):
                        res["conn_id"] = conn_id
                        res["conn_name"] = conn_name
                        res["success"] = True
                        return res
                    else:
                        res["error"] = f"Gagal update connection di 9router: {rc.text[:150]}"
                        return res
    except Exception as exc:
        res["error"] = f"Gagal membaca connection 9router: {exc}"
        return res

    # Jika connection belum ada, buat baru
    if not conn_id:
        try:
            create_conn_body = {
                "provider": node_id,
                "authType": "apikey",
                "name": "deepseek-bridge-local",
                "apiKey": api_key,
                "priority": 1,
                "isActive": True,
                "providerSpecificData": {
                    "prefix": prefix,
                    "apiType": "chat",
                    "baseUrl": base_url,
                    "nodeName": node_name,
                    "enabledModels": enabled_models or ["dsk-auto"],
                },
            }
            rc = s.post(
                f"{router_url.rstrip('/')}/api/providers",
                json=create_conn_body,
                timeout=10,
            )
            if rc.status_code in (200, 201):
                j = rc.json()
                res["conn_id"] = j.get("id") or (j.get("connection") or {}).get("id")
                res["conn_name"] = "deepseek-bridge-local"
                res["success"] = True
                return res
            else:
                res["error"] = f"Gagal membuat connection di 9router: {rc.text[:150]}"
                return res
        except Exception as exc:
            res["error"] = f"Gagal membuat connection di 9router: {exc}"
            return res

    return res


def cmd_connect_generate(args=None) -> int:
    """Ambil/buat API Key Bridge Xenonite + Base URL, lalu auto-inject ke 9router."""
    store = get_store()
    name = getattr(args, "name", None)
    if not name:
        name = "xenonite"
    name = name.strip()

    force_new = getattr(args, "rotate", False) or getattr(args, "force_new", False)

    # Cari key yang sudah ada dengan nama tersebut
    key: Optional[ApiKey] = None
    if not force_new:
        for existing in store.all():
            if existing.name.lower() == name.lower() and existing.enabled:
                key = existing
                break

    # Jika belum ada atau diminta rotate, buat baru
    if key is None:
        # Bersihkan key lama berlabel sama
        for existing in store.all():
            if existing.name.lower() == name.lower():
                store.delete(existing.key)

        rpm = getattr(args, "rpm", 60)
        gap = getattr(args, "gap", 1.0)
        conc = getattr(args, "conc", 3)
        budget = getattr(args, "budget", 0)
        accounts = (
            [a.strip() for a in args.accounts.split(",") if a.strip()]
            if getattr(args, "accounts", None)
            else None
        )
        models = (
            [m.strip() for m in args.models.split(",") if m.strip()]
            if getattr(args, "models", None)
            else None
        )
        note = (
            getattr(args, "note", "")
            or f"Key tunggal {name}: provider bridge untuk 9router."
        )

        key = store.create(
            name=name,
            rpm=rpm,
            min_gap_s=gap,
            max_concurrent=conc,
            daily_budget=budget,
            accounts=accounts,
            models=models,
            note=note,
        )

    # Base URL lokal DeepSeek Bridge
    port = getattr(config, "PORT", 8787)
    base_url = f"http://127.0.0.1:{port}/v1"

    # Published models
    pub_models = get_published_models()

    # 9router auto-injection (hanya inject ke provider connection di 9router, BUKAN root keys 9router)
    no_inject = getattr(args, "no_inject", False)
    router_url = getattr(args, "router_url", DEFAULT_9ROUTER_URL) or DEFAULT_9ROUTER_URL
    router_pass = getattr(args, "router_pass", DEFAULT_9ROUTER_PASS) or DEFAULT_9ROUTER_PASS

    inject_res = None
    if not no_inject:
        inject_res = inject_to_9router(
            base_url=base_url,
            api_key=key.key,
            router_url=router_url,
            router_pass=router_pass,
            enabled_models=pub_models,
        )

    # JSON output mode jika diminta
    if getattr(args, "json", False):
        output = {
            "base_url": base_url,
            "api_key": key.key,
            "name": key.name,
            "rpm": key.rpm,
            "min_gap_s": key.min_gap_s,
            "max_concurrent": key.max_concurrent,
            "published_models": pub_models,
            "9router_injected": inject_res.get("success", False) if inject_res else False,
            "9router_details": inject_res,
        }
        print(json.dumps(output, indent=2))
        return 0

    # Display yang rapi & siap pakai
    print("\n" + "=" * 68)
    print("        DEEPSEEK BRIDGE - CONNECTION & API KEY GENERATOR")
    print("=" * 68)
    print(f"  Base URL        : {base_url}")
    print(f"  API Key Token   : {key.key}")
    print(f"  Key Label / Name: {key.name}")
    print(f"  Rate Limits     : {key.rpm} RPM | Min Gap {key.min_gap_s}s | Max Conc {key.max_concurrent}")
    print(f"  Published Model : {', '.join(pub_models)}")
    print("-" * 68)

    if no_inject:
        print("  Status 9router  : [DILEWATI] Flag --no-inject digunakan.")
    elif inject_res and inject_res.get("success"):
        cname = inject_res.get("conn_name", "Connection")
        nname = inject_res.get("node_name", "Node")
        print(f"  Status 9router  : [BERHASIL DI-INJECT KE 9ROUTER]")
        print(f"    - Provider Node  : {nname} (ID: {inject_res.get('node_id')})")
        print(f"    - Connection     : {cname} (ID: {inject_res.get('conn_id')})")
        print(f"    - Base URL & Key : Telah disinkronkan & siap digunakan.")
    else:
        err = (inject_res.get("error") if inject_res else "Tidak aktif")
        print(f"  Status 9router  : [TIDAK TERHUBUNG SECARA OTOMATIS]")
        print(f"    Penyebab: {err}")
        print(f"    (Anda dapat memasukkan Base URL & API Key di atas secara manual ke 9router)")

    print("=" * 68)
    print("\nContoh format konfigurasi langsung:\n")
    print("1. Di 9router (Manual jika diperlukan):")
    print(f"   Provider Type : OpenAI-compatible")
    print(f"   Base URL      : {base_url}")
    print(f"   API Key       : {key.key}\n")

    print("2. Di Hermes / Environment Variables / .env:")
    print(f"   OPENAI_BASE_URL={base_url}")
    print(f"   OPENAI_API_KEY={key.key}\n")

    print("3. Uji koneksi via cURL terminal:")
    print(f'   curl -X GET "{base_url}/models" -H "Authorization: Bearer {key.key}"\n')
    print("=" * 68 + "\n")

    return 0
