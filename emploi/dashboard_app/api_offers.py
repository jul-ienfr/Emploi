"""Dashboard API — routes api offers bp (blueprint ``api_offers_bp``).

Extrait de ``dashboard.py`` (tranche 2 du découpage du monolithe).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime

from flask import Blueprint, jsonify, request

from emploi.dashboard_app.common import _ensure_history_table, _get_db, _log_change

api_offers_bp = Blueprint("api_offers_bp", __name__)


@api_offers_bp.route("/api/offers")
def api_offers():
    conn = _get_db()
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offers = conn.execute(
            "SELECT * FROM offers WHERE is_active = 1 ORDER BY score DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return jsonify([dict(row) for row in offers])
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/history")
def api_offer_history(offer_id):
    conn = _get_db()
    try:
        _ensure_history_table(conn)
        rows = conn.execute(
            "SELECT * FROM offer_history WHERE offer_id = ? ORDER BY created_at DESC",
            (offer_id,),
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/undo", methods=["POST"])
def api_offer_undo(offer_id):
    conn = _get_db()
    try:
        _ensure_history_table(conn)
        last = conn.execute(
            "SELECT * FROM offer_history WHERE offer_id = ? ORDER BY id DESC LIMIT 1",
            (offer_id,),
        ).fetchone()
        if last is None:
            return jsonify({"error": "Nothing to undo"}), 400
        # Restore old value
        conn.execute(
            f"UPDATE offers SET {last['field']} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (last["old_value"], offer_id),
        )
        # Log the undo
        _log_change(conn, offer_id, last["field"], last["new_value"], last["old_value"])
        conn.commit()
        return jsonify({"ok": True, "undone": dict(last)})
    finally:
        conn.close()


@api_offers_bp.route("/api/offers/cleanup", methods=["POST"])
def api_cleanup_stale():
    stale_days = int(os.environ.get("EMPLOI_DASHBOARD_STALE_DAYS", "30"))
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE offers SET is_active = 0, status = 'archived', "
            f"updated_at = CURRENT_TIMESTAMP "
            f"WHERE is_active = 1 AND created_at < datetime('now', '-{stale_days} days')"
        )
        conn.commit()
        return jsonify({"ok": True, "stale_days": stale_days})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/compensation", methods=["GET", "PUT"])
def api_compensation(offer_id):
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS offer_compensation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER UNIQUE NOT NULL,
                salary_brut REAL DEFAULT 0,
                bonus REAL DEFAULT 0,
                benefits_json TEXT DEFAULT '{}',
                total_annual REAL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        if request.method == "GET":
            row = conn.execute("SELECT * FROM offer_compensation WHERE offer_id = ?", (offer_id,)).fetchone()
            return jsonify(dict(row) if row else {"offer_id": offer_id, "total_annual": 0})
        else:
            data = request.get_json(force=True)
            salary = float(data.get("salary_brut", 0))
            bonus = float(data.get("bonus", 0))
            benefits = float(data.get("benefits", 0))
            total = salary + bonus + benefits
            conn.execute(
                """INSERT INTO offer_compensation (offer_id, salary_brut, bonus, benefits_json, total_annual)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(offer_id) DO UPDATE SET
                    salary_brut=excluded.salary_brut, bonus=excluded.bonus,
                    benefits_json=excluded.benefits_json, total_annual=excluded.total_annual,
                    updated_at=CURRENT_TIMESTAMP""",
                (offer_id, salary, bonus, json.dumps(data.get("benefits", {})), total),
            )
            conn.commit()
            return jsonify({"ok": True, "total_annual": total})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/share")
def api_share_offer(offer_id):
    token = hashlib.sha1(f"{offer_id}-share".encode()).hexdigest()[:12]
    url = f"/share/{token}"
    return jsonify({"ok": True, "url": url, "token": token})


@api_offers_bp.route("/api/offers/duplicates")
def api_duplicates():
    conn = _get_db()
    try:
        # Find offers with similar titles from different sources
        rows = conn.execute(
            """SELECT a.id as id_a, a.title as title_a, a.company as company_a,
                      b.id as id_b, b.title as title_b, b.company as company_b
            FROM offers a JOIN offers b ON a.id < b.id
            WHERE a.is_active = 1 AND b.is_active = 1
            AND a.company = b.company AND a.title != b.title
            AND (a.location = b.location OR a.location = '' OR b.location = '')
            LIMIT 50"""
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/credibility")
def api_credibility(offer_id):
    conn = _get_db()
    try:
        offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if not offer:
            return jsonify({"error": "Not found"}), 404
        score = 50
        reasons = []
        if offer["company"]:
            score += 10
            reasons.append("Entreprise renseignée (+10)")
        if offer["description"] and len(offer["description"]) > 100:
            score += 10
            reasons.append("Description détaillée (+10)")
        if offer["salary"]:
            score += 10
            reasons.append("Salaire indiqué (+10)")
        if offer["url"] and offer["url"].startswith("http"):
            score += 5
            reasons.append("URL valide (+5)")
        if not offer["company"]:
            score -= 15
            reasons.append("Pas d'entreprise (-15)")
        if offer["description"] and len(offer["description"]) < 50:
            score -= 10
            reasons.append("Description trop courte (-10)")
        score = max(0, min(100, score))
        return jsonify({"score": score, "reasons": reasons})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/benefits", methods=["PUT"])
def api_set_benefits(offer_id):
    data = request.get_json(force=True)
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS offer_benefits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER UNIQUE NOT NULL,
                benefits_json TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        conn.execute(
            """INSERT INTO offer_benefits (offer_id, benefits_json)
            VALUES (?, ?) ON CONFLICT(offer_id) DO UPDATE SET
            benefits_json=excluded.benefits_json, updated_at=CURRENT_TIMESTAMP""",
            (offer_id, json.dumps(data.get("benefits", {}))),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/visa", methods=["PUT"])
def api_set_visa_info(offer_id):
    data = request.get_json(force=True)
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS offer_visa (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER UNIQUE NOT NULL,
                visa_sponsorship INTEGER DEFAULT 0,
                relocation_assistance INTEGER DEFAULT 0,
                languages TEXT DEFAULT '',
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        conn.execute(
            """INSERT INTO offer_visa (offer_id, visa_sponsorship, relocation_assistance, languages)
            VALUES (?, ?, ?, ?) ON CONFLICT(offer_id) DO UPDATE SET
            visa_sponsorship=excluded.visa_sponsorship,
            relocation_assistance=excluded.relocation_assistance,
            languages=excluded.languages""",
            (offer_id, data.get("visa_sponsorship", 0), data.get("relocation", 0), data.get("languages", "")),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/translate", methods=["POST"])
def api_translate(offer_id):
    data = request.get_json(force=True)
    text = data.get("text", "")
    target_lang = data.get("lang", "fr")
    # Stub: return original text (real translation needs API key)
    return jsonify({"ok": True, "translated": text, "lang": target_lang})


@api_offers_bp.route("/api/offer/<int:offer_id>/voice-notes", methods=["GET", "POST", "DELETE"])
def api_voice_notes(offer_id):
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS voice_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                audio_data TEXT DEFAULT '',
                transcript TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        if request.method == "GET":
            rows = conn.execute(
                "SELECT id, transcript, created_at FROM voice_notes WHERE offer_id = ? ORDER BY created_at DESC",
                (offer_id,),
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        elif request.method == "DELETE":
            note_id = request.args.get("id", "").strip()
            if note_id:
                conn.execute("DELETE FROM voice_notes WHERE id = ? AND offer_id = ?", (int(note_id), offer_id))
                conn.commit()
            return jsonify({"ok": True})
        else:
            data = request.get_json(force=True)
            conn.execute(
                "INSERT INTO voice_notes (offer_id, audio_data, transcript) VALUES (?, ?, ?)",
                (offer_id, data.get("audio", ""), data.get("transcript", "")),
            )
            conn.commit()
            return jsonify({"ok": True})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/bookmark", methods=["POST"])
def api_toggle_bookmark(offer_id):
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS offer_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        existing = conn.execute("SELECT id FROM offer_bookmarks WHERE offer_id = ?", (offer_id,)).fetchone()
        if existing:
            conn.execute("DELETE FROM offer_bookmarks WHERE offer_id = ?", (offer_id,))
            conn.commit()
            return jsonify({"ok": True, "bookmarked": False})
        else:
            conn.execute("INSERT INTO offer_bookmarks (offer_id) VALUES (?)", (offer_id,))
            conn.commit()
            return jsonify({"ok": True, "bookmarked": True})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/tags", methods=["POST"])
def api_set_tags(offer_id):
    from flask import request as req

    data = req.get_json(force=True)
    tags = data.get("tags", [])
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS offer_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id),
                UNIQUE(offer_id, tag)
            )"""
        )
        conn.execute("DELETE FROM offer_tags WHERE offer_id = ?", (offer_id,))
        for tag in tags:
            tag = str(tag).strip().lower()
            if tag:
                conn.execute(
                    "INSERT INTO offer_tags (offer_id, tag) VALUES (?, ?)",
                    (offer_id, tag),
                )
        conn.commit()
        return jsonify({"ok": True, "tags": tags})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/tags")
def api_get_tags(offer_id):
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT tag FROM offer_tags WHERE offer_id = ? ORDER BY tag",
            (offer_id,),
        ).fetchall()
        return jsonify([row["tag"] for row in rows])
    finally:
        conn.close()


@api_offers_bp.route("/api/offers/batch/status", methods=["POST"])
def api_batch_status():
    from flask import request as req

    data = req.get_json(force=True)
    ids = data.get("ids", [])
    status = data.get("status", "").strip()
    if not ids or not status:
        return jsonify({"error": "ids and status required"}), 400
    conn = _get_db()
    try:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE offers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            [status] + ids,
        )
        conn.commit()
        return jsonify({"ok": True, "updated": len(ids)})
    finally:
        conn.close()


@api_offers_bp.route("/api/offers/batch/archive", methods=["POST"])
def api_batch_archive():
    from flask import request as req

    data = req.get_json(force=True)
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "ids required"}), 400
    conn = _get_db()
    try:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE offers SET status = 'archived', is_active = 0, "
            f"updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return jsonify({"ok": True, "archived": len(ids)})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/status", methods=["POST"])
def api_update_offer_status(offer_id):
    from flask import request as req

    data = req.get_json(force=True)
    new_status = data.get("status", "").strip()
    if not new_status:
        return jsonify({"error": "status required"}), 400
    conn = _get_db()
    try:
        old = conn.execute("SELECT status FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if old:
            _log_change(conn, offer_id, "status", str(old["status"]), new_status)
        conn.execute(
            "UPDATE offers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, offer_id),
        )
        conn.commit()
        return jsonify({"ok": True, "status": new_status})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/note", methods=["POST"])
def api_add_offer_note(offer_id):
    from flask import request as req

    data = req.get_json(force=True)
    note_text = data.get("note", "").strip()
    if not note_text:
        return jsonify({"error": "note required"}), 400
    conn = _get_db()
    try:
        # Ensure table exists
        conn.execute(
            """CREATE TABLE IF NOT EXISTS offer_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        conn.execute(
            "INSERT INTO offer_notes (offer_id, note) VALUES (?, ?)",
            (offer_id, note_text),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/cover-letter", methods=["POST"])
def api_cover_letter(offer_id):
    conn = _get_db()
    try:
        offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if offer is None:
            return jsonify({"error": "Offer not found"}), 404

        data = request.get_json(force=True) if request.data else {}
        sender_name = data.get("sender_name", "[Votre nom]")
        sender_email = data.get("sender_email", "[votre.email@example.com]")

        cover_letter = (
            f"Objet : Candidature au poste de {offer['title']}\n\n"
            f"{sender_name}\n"
            f"{sender_email}\n\n"
            f"{datetime.now().strftime('%d/%m/%Y')}\n\n"
            f"{offer['company']}\n"
            f"{offer['location']}\n\n"
            f"Madame, Monsieur,\n\n"
            f"Je me permets de vous adresser ma candidature pour le poste de "
            f"{offer['title']} au sein de {offer['company']}, situé à {offer['location']}.\n\n"
            f"[Décrivez votre parcours et vos compétences pertinentes ici]\n\n"
            f"[Mettez en avant vos réalisations clés et votre motivation pour ce poste]\n\n"
            f"Je serais ravi(e) de pouvoir échanger avec vous lors d'un entretien afin de "
            f"vous exposer plus en détail mes motivations.\n\n"
            f"Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.\n\n"
            f"{sender_name}"
        )

        return jsonify({"ok": True, "cover_letter": cover_letter})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/contract/analyze", methods=["POST"])
def api_contract_analyze(offer_id):
    conn = _get_db()
    try:
        offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if offer is None:
            return jsonify({"error": "Offer not found"}), 404

        data = request.get_json(force=True)
        contract_text = data.get("text", "").strip()
        if not contract_text:
            return jsonify({"error": "text required"}), 400

        import re

        clauses = {}

        # Trial period
        trial_match = re.search(
            r"(?:p[ée]riode|essai)\s+d['’]essai\s*[:\s]*(\d+)\s*(mois|jours?|semaines?)",
            contract_text,
            re.IGNORECASE,
        )
        if trial_match:
            clauses["trial_period"] = f"{trial_match.group(1)} {trial_match.group(2)}"
        else:
            trial_match2 = re.search(
                r"essai\s*(?:de\s*)?(\d+)\s*(mois|jours?|semaines?)",
                contract_text,
                re.IGNORECASE,
            )
            if trial_match2:
                clauses["trial_period"] = f"{trial_match2.group(1)} {trial_match2.group(2)}"

        # Salary
        salary_match = re.search(
            r"(?:salaire|r[ée]mun[ée]ration)\s*[:\s]*(\d[\d\s,.]*)\s*(?:euros?|€|EUR|brut|net)",
            contract_text,
            re.IGNORECASE,
        )
        if salary_match:
            clauses["salary"] = salary_match.group(0).strip()

        # Non-compete
        noncompete_match = re.search(
            r"(?:clause|engagement)\s+(?:de\s+)?non[\s-]*concurrence\s*(?:pendant\s+)?(\d+)\s*(mois|ann[ée]es?)?",
            contract_text,
            re.IGNORECASE,
        )
        if noncompete_match:
            clauses["non_compete"] = noncompete_match.group(0).strip()

        if "non-concurrence" in contract_text.lower() and "non_compete" not in clauses:
            clauses["non_compete"] = "Clause de non-concurrence présente"

        return jsonify({"ok": True, "clauses": clauses})
    finally:
        conn.close()


@api_offers_bp.route("/api/apply/<int:offer_id>/steps")
def api_apply_steps(offer_id):
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS apply_wizard (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                step INTEGER NOT NULL,
                completed INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id),
                UNIQUE(offer_id, step)
            )"""
        )

        offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if offer is None:
            return jsonify({"error": "Offer not found"}), 404

        steps = [
            {"step": 1, "label": "Analyser l’offre", "description": "Lire et comprendre les exigences du poste"},
            {"step": 2, "label": "Préparer le CV", "description": "Adapter le CV au poste visé"},
            {"step": 3, "label": "Rédiger la lettre", "description": "Rédiger la lettre de motivation"},
            {"step": 4, "label": "Vérifier le dossier", "description": "Relire et corriger les documents"},
            {"step": 5, "label": "Postuler", "description": "Envoyer la candidature"},
        ]

        completed_rows = conn.execute(
            "SELECT step, completed, notes FROM apply_wizard WHERE offer_id = ?",
            (offer_id,),
        ).fetchall()
        completed_map = {
            row["step"]: {"completed": bool(row["completed"]), "notes": row["notes"]} for row in completed_rows
        }

        for step in steps:
            if step["step"] in completed_map:
                step["completed"] = completed_map[step["step"]]["completed"]
                step["notes"] = completed_map[step["step"]]["notes"]
            else:
                step["completed"] = False
                step["notes"] = ""

        return jsonify({"ok": True, "offer_id": offer_id, "steps": steps})
    finally:
        conn.close()


@api_offers_bp.route("/api/apply/<int:offer_id>/step/<int:n>", methods=["POST"])
def api_apply_step_complete(offer_id, n):
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS apply_wizard (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                step INTEGER NOT NULL,
                completed INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id),
                UNIQUE(offer_id, step)
            )"""
        )

        offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if offer is None:
            return jsonify({"error": "Offer not found"}), 404

        if n < 1 or n > 5:
            return jsonify({"error": "Step must be between 1 and 5"}), 400

        data = request.get_json(force=True) if request.data else {}
        notes = data.get("notes", "")
        completed = data.get("completed", True)

        conn.execute(
            "INSERT INTO apply_wizard (offer_id, step, completed, notes) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(offer_id, step) DO UPDATE SET completed = ?, notes = ?",
            (offer_id, n, int(completed), notes, int(completed), notes),
        )
        conn.commit()
        return jsonify(
            {
                "ok": True,
                "offer_id": offer_id,
                "step": n,
                "completed": completed,
                "notes": notes,
            }
        )
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/interview", methods=["GET"])
def api_get_interview_prep(offer_id):
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS interview_prep (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER UNIQUE NOT NULL,
                notes TEXT DEFAULT '',
                checklist_json TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        row = conn.execute("SELECT * FROM interview_prep WHERE offer_id = ?", (offer_id,)).fetchone()
        if row is None:
            return jsonify({"offer_id": offer_id, "notes": "", "checklist": []})
        return jsonify(
            {
                "offer_id": offer_id,
                "notes": row["notes"],
                "checklist": json.loads(row["checklist_json"] or "[]"),
            }
        )
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/interview", methods=["PUT"])
def api_save_interview_prep(offer_id):
    from flask import request as req

    data = req.get_json(force=True)
    notes = data.get("notes", "")
    checklist = data.get(
        "checklist",
        [
            {"text": "Relire l'annonce", "done": False},
            {"text": "Preparer questions", "done": False},
            {"text": "Verifier transport", "done": False},
            {"text": "Imprimer CV", "done": False},
        ],
    )
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS interview_prep (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER UNIQUE NOT NULL,
                notes TEXT DEFAULT '',
                checklist_json TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        existing = conn.execute("SELECT id FROM interview_prep WHERE offer_id = ?", (offer_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE interview_prep SET notes = ?, checklist_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE offer_id = ?",
                (notes, json.dumps(checklist), offer_id),
            )
        else:
            conn.execute(
                "INSERT INTO interview_prep (offer_id, notes, checklist_json) VALUES (?, ?, ?)",
                (offer_id, notes, json.dumps(checklist)),
            )
        conn.commit()
        return jsonify({"ok": True, "offer_id": offer_id})
    finally:
        conn.close()


@api_offers_bp.route("/api/offer/<int:offer_id>/interview", methods=["DELETE"])
def api_delete_interview_prep(offer_id):
    conn = _get_db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS interview_prep (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER UNIQUE NOT NULL,
                notes TEXT DEFAULT '',
                checklist_json TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )"""
        )
        conn.execute("DELETE FROM interview_prep WHERE offer_id = ?", (offer_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()
