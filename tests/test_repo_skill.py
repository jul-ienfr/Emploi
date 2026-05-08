from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PROJECT_ROOT / "skills" / "emploi-cli" / "SKILL.md"


def test_repo_contains_hermes_skill_for_emploi_cli():
    assert SKILL_PATH.exists()
    content = SKILL_PATH.read_text()
    assert "name: emploi-cli" in content
    assert "emploi doctor --json" in content
    assert "emploi search-profile run --all" in content
    assert "emploi ft apply" in content
    assert "emploi ft apply 1 --partner hellowork" in content
    assert "emploi hellowork apply 1 --submit --yes" in content
    assert "application_submitted" in content
    assert "candidature-envoyee" in content
    assert "ne soumet jamais automatiquement" in content
    assert "sans `Invalid value`, sans traceback" in content
    assert "sans événement `partner_opened`" in content
    assert "references/france-travail-partner-handoff-hardening.md" in content
    assert "references/hellowork-application-flow.md" in content


def test_repo_skill_includes_ft_partner_handoff_hardening_reference():
    reference = PROJECT_ROOT / "skills" / "emploi-cli" / "references" / "france-travail-partner-handoff-hardening.md"
    assert reference.exists()
    content = reference.read_text()
    assert "ft apply OFFER_ID --partner NAME" in content
    assert "lifecycle_open" in content
    assert "no `partner_opened` event recorded" in content
    assert "typer.Exit(1)" in content


def test_repo_skill_includes_hellowork_application_flow_reference():
    reference = PROJECT_ROOT / "skills" / "emploi-cli" / "references" / "hellowork-application-flow.md"
    assert reference.exists()
    content = reference.read_text()
    assert "emploi hellowork apply OFFER_ID --submit --yes" in content
    assert "candidature-envoyee" in content
    assert "application_submitted" in content
    assert "FunnelId" in content
    assert "LastName" in content


def test_readme_points_hermes_to_repo_skill():
    readme = (PROJECT_ROOT / "README.md").read_text()
    assert "skills/emploi-cli/SKILL.md" in readme
    assert "Hermes" in readme


def test_readme_documents_current_france_travail_managed_browser_contract():
    readme = (PROJECT_ROOT / "README.md").read_text()
    assert "EMPLOI_MANAGED_BROWSER_COMMAND=\"managed-browser\"" in readme
    assert "`lifecycle open` pour les flux France Travail" in readme
    assert "emploi ft apply 1 --partner hellowork" in readme
    assert "aucun clic final ni soumission" in readme


def test_readme_documents_hellowork_submit_and_kanban_contract():
    readme = (PROJECT_ROOT / "README.md").read_text()
    assert "emploi hellowork apply 1" in readme
    assert "emploi hellowork apply 1 --submit --yes" in readme
    assert "application_submitted" in readme
    assert "candidature-envoyee" in readme
    assert "FunnelId" in readme
