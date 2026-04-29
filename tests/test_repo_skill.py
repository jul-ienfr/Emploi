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
    assert "ne soumet jamais automatiquement" in content


def test_readme_points_hermes_to_repo_skill():
    readme = (PROJECT_ROOT / "README.md").read_text()
    assert "skills/emploi-cli/SKILL.md" in readme
    assert "Hermes" in readme
