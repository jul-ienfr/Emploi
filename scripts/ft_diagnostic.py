"""Diagnostic live France Travail via Managed Browser (outil manuel).

Usage::

    python scripts/ft_diagnostic.py

Nécessite le Managed Browser (Camoufox) accessible sur ``MANAGED_BROWSER_URL``
(par défaut http://127.0.0.1:9377) avec une session France Travail ouverte.
Ce n'est PAS un test pytest : chaque test_* avale ses exceptions et retourne
un booléen pour afficher un bilan final lisible par un humain.

Notes:
- Le test 6 (recherche complète) écrit dans la base SQLite par défaut
  (~/.local/share/emploi/emploi.sqlite). Pour un diagnostic sans écriture,
  définir ``EMPLOI_DB`` vers une base jetable.
- Le test 7 nécessite les variables d'environnement FT_CLIENT_ID /
  FT_CLIENT_SECRET (API REST France Travail, optionnelle).
"""

from __future__ import annotations

import os
import time
import traceback

from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserError
from emploi.config import get_default_profile


def test_managed_browser_status():
    """Vérifier que le Managed Browser répond."""
    print("=== Test 1: Status Managed Browser ===")
    try:
        client = ManagedBrowserClient()
        result = client.status()
        payload = result.payload if isinstance(result.payload, dict) else {}
        print(f"  ✅ Serveur répond (ok={payload.get('ok')})")
        print(f"  ✅ Profile: {payload.get('profile', 'unknown')}")
        print(f"  ✅ Browser: {payload.get('engine', 'unknown')} alive={payload.get('alive', '?')}")
        return True
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


def test_ft_search_url():
    """Vérifier que l'URL de recherche FT est correcte."""
    print("\n=== Test 2: URL de recherche France Travail ===")
    try:
        from emploi.france_travail.flows import build_search_url

        url = build_search_url("python développeur", "Bogève 74250", radius=30)
        print(f"  ✅ URL: {url}")
        return True
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


def test_ft_open_page():
    """Essayer d'ouvrir la page de recherche FT dans le browser."""
    print("\n=== Test 3: Ouverture page France Travail ===")
    try:
        from emploi.france_travail.flows import build_search_url

        url = build_search_url("python", "Bogève 74250", radius=30)
        client = ManagedBrowserClient()
        profile = get_default_profile()
        print(f"  Profile: {profile}")
        print(f"  URL: {url[:80]}...")
        result = client.lifecycle_open(url, profile=profile)
        print(f"  ✅ Page ouverte: {result.payload.get('status', 'unknown')}")
        return True
    except ManagedBrowserError as e:
        print(f"  ❌ ManagedBrowserError: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


def test_ft_snapshot():
    """Prendre un snapshot de la page FT."""
    print("\n=== Test 4: Snapshot France Travail ===")
    try:
        client = ManagedBrowserClient()
        profile = get_default_profile()
        result = client.snapshot(profile=profile)
        payload = result.payload
        print("  ✅ Snapshot pris")
        print(f"  Keys: {list(payload.keys()) if isinstance(payload, dict) else 'not dict'}")
        if isinstance(payload, dict):
            for k in ["text", "html", "content", "snapshot"]:
                if k in payload:
                    val = str(payload[k])
                    print(f"  {k}: {val[:200]}...")
        return True
    except ManagedBrowserError as e:
        print(f"  ❌ ManagedBrowserError: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


def test_ft_console_eval():
    """Extraire les offres via console_eval."""
    print("\n=== Test 5: Extraction offres via console_eval ===")
    try:
        client = ManagedBrowserClient()
        profile = get_default_profile()
        expression = """
        Array.from(document.querySelectorAll('li.result')).map(li => {
          const link = li.querySelector('a[href*="/offres/recherche/detail/"]');
          const title = li.querySelector('.media-heading-title')?.innerText || '';
          return {title: title, href: link?.href || '', text: li.innerText || ''};
        })
        """.strip()
        print("  ✅ Console eval exécuté")
        from emploi.france_travail.flows import _eval_value

        value = []
        for attempt in range(5):
            result = client.console_eval(expression, profile=profile)
            value = _eval_value(result.payload, default=[])
            if isinstance(value, list) and value:
                break
            if attempt < 4:
                time.sleep(2.0)
        if not isinstance(value, list):
            value = []
        if isinstance(value, list):
            print(f"  📊 {len(value)} offres extraites")
            for o in value[:3]:
                print(f"    - {o.get('title', 'N/A')[:60]}")
        else:
            print(f"  ⚠️ Pas de liste d'offres: {type(value)}")
        return True
    except ManagedBrowserError as e:
        print(f"  ❌ ManagedBrowserError: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


def test_ft_search_offers():
    """Lancer une recherche FT complète."""
    print("\n=== Test 6: Recherche FT complète ===")
    try:
        from emploi.db import connect, init_db
        from emploi.france_travail.flows import search_offers

        with connect() as conn:
            init_db(conn)
            profile = get_default_profile()
            results = search_offers(conn, query="python", profile=profile, radius=30, location="Bogève 74250")
            print(f"  ✅ {len(results)} offre(s) trouvée(s)")
            for r in results[:5]:
                print(f"    - {r.title} | id={r.offer_id} | score={r.score}")
        return True
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


def test_ft_api_client():
    """Tester le client API REST France Travail."""
    print("\n=== Test 7: Client API REST France Travail ===")
    try:
        from emploi.france_travail.api_client import FranceTravailAPIClient

        client_id = os.environ.get("FT_CLIENT_ID", "")
        client_secret = os.environ.get("FT_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            print("  ⚠️ FT_CLIENT_ID et FT_CLIENT_SECRET non configurés")
            print("  → L'API REST n'est pas disponible, utiliser le browser à la place")
            return False
        client = FranceTravailAPIClient(client_id, client_secret)
        results = client.search_offers("python", location="74038")
        print(f"  ✅ {len(results)} offre(s) via API REST")
        return True
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


if __name__ == "__main__":
    results = []
    results.append(("Status MB", test_managed_browser_status()))
    results.append(("URL FT", test_ft_search_url()))
    results.append(("Ouverture page", test_ft_open_page()))
    results.append(("Snapshot", test_ft_snapshot()))
    results.append(("Console eval", test_ft_console_eval()))
    results.append(("Recherche complète", test_ft_search_offers()))
    results.append(("API REST", test_ft_api_client()))

    print("\n" + "=" * 60)
    print("RÉSUMÉ DU DIAGNOSTIC")
    print("=" * 60)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
