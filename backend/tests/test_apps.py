"""The apps.yml launcher: nothing here is editable through the UI, so the
file-parsing behavior IS the feature -- this is what actually decides
whether a person's homer-style config shows up correctly."""
import pytest

from app.services import apps


@pytest.fixture()
def config(tmp_path, monkeypatch):
    path = tmp_path / "apps.yml"
    monkeypatch.setattr(apps, "APPS_CONFIG", path)
    return path


def test_no_file_yet_is_not_an_error(config):
    result = apps.list_apps()
    assert result["exists"] is False
    assert result["error"] is None
    assert result["apps"] == []


def test_a_normal_config_parses(config):
    config.write_text("""
apps:
  - name: Portainer
    icon: "🐳"
    url: http://192.168.1.10:9000
  - name: Plex
    icon: https://example.com/plex.png
    url: http://192.168.1.10:32400/web
""")
    result = apps.list_apps()
    assert result["exists"] is True
    assert result["error"] is None
    assert result["apps"] == [
        {"name": "Portainer", "icon": "🐳", "url": "http://192.168.1.10:9000"},
        {"name": "Plex", "icon": "https://example.com/plex.png",
         "url": "http://192.168.1.10:32400/web"},
    ]


def test_icon_is_optional(config):
    config.write_text("apps:\n  - name: Router\n    url: http://192.168.1.1\n")
    result = apps.list_apps()
    assert result["apps"] == [{"name": "Router", "icon": "", "url": "http://192.168.1.1"}]


def test_an_entry_missing_url_is_skipped_not_fatal(config):
    config.write_text("""
apps:
  - name: Broken entry
  - name: Good entry
    url: http://192.168.1.20
""")
    result = apps.list_apps()
    assert result["error"] is None
    assert [a["name"] for a in result["apps"]] == ["Good entry"]


def test_an_entry_missing_name_is_skipped_not_fatal(config):
    config.write_text("""
apps:
  - url: http://192.168.1.30
  - name: Good entry
    url: http://192.168.1.20
""")
    result = apps.list_apps()
    assert [a["name"] for a in result["apps"]] == ["Good entry"]


def test_a_non_mapping_entry_is_skipped_not_fatal(config):
    config.write_text("apps:\n  - just a string\n  - name: Good\n    url: http://x\n")
    result = apps.list_apps()
    assert [a["name"] for a in result["apps"]] == ["Good"]


def test_malformed_yaml_reports_an_error_rather_than_crashing(config):
    config.write_text("apps: [this is not: valid: yaml")
    result = apps.list_apps()
    assert result["exists"] is True
    assert result["apps"] == []
    assert "not valid YAML" in result["error"]


def test_missing_top_level_apps_key_reports_an_error(config):
    config.write_text("something_else:\n  - foo\n")
    result = apps.list_apps()
    assert result["apps"] == []
    assert "apps:" in result["error"]


def test_apps_key_that_is_not_a_list_reports_an_error(config):
    config.write_text("apps: just a string\n")
    result = apps.list_apps()
    assert result["apps"] == []
    assert result["error"] is not None


def test_empty_apps_list_is_not_an_error(config):
    config.write_text("apps: []\n")
    result = apps.list_apps()
    assert result["error"] is None
    assert result["apps"] == []


def test_names_and_urls_are_whitespace_trimmed(config):
    config.write_text('apps:\n  - name: "  Spacey  "\n    url: "  http://x  "\n')
    result = apps.list_apps()
    assert result["apps"] == [{"name": "Spacey", "icon": "", "url": "http://x"}]
