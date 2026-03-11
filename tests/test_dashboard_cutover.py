from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_dashboard_uses_canonical_manifest_as_primary_source() -> None:
    html = _html()
    assert "const CANONICAL_SOURCE_URL = 'catalog_manifest.json';" in html
    assert "const LEGACY_SOURCE_URL = 'ai_routing_table.json';" in html
    assert "normalizeRoutingPayload(payload, 'canonical', CANONICAL_SOURCE_URL)" in html
    assert "normalizeRoutingPayload(payload, 'legacy_rollback', LEGACY_SOURCE_URL)" in html
    assert "Canonical manifest unavailable, using legacy rollback feed" in html


def test_dashboard_runtime_cutover_text_and_controls_are_canonical() -> None:
    html = _html()
    assert "canonical v5 catalog manifest is now the primary public artifact" in html
    assert 'value="https://filokloi.github.io/AIchain/catalog_manifest.json"' in html
    assert "task:coding" in html
    assert "dashboard still reads the legacy ranking feed" not in html


def test_dashboard_exposes_canonical_runtime_status_semantics() -> None:
    html = _html()
    for mode in (
        "runtime_confirmed",
        "degraded_fallback",
        "blocked_missing_credentials",
        "target_form_not_reached",
    ):
        assert mode in html
    assert "operationalGrid" in html
    assert "public_artifact_readiness" in html
    assert "canonical_public_artifact" in html


def test_dashboard_table_and_exports_use_normalized_filtered_models() -> None:
    html = _html()
    assert "let models = getFilteredModels();" in html
    assert "const models = getFilteredModels();" in html
    assert "const payload = routingRawData || routingData;" in html
    assert "const taskMeta = getTaskMeta(m.task_label);" in html


def test_dashboard_exposes_provider_access_matrix_and_limits() -> None:
    html = _html()
    assert 'Provider Access & Limits' in html
    assert 'accessMatrixGrid' in html
    assert 'provider_access_matrix' in html
    assert 'renderProviderAccessMatrix();' in html
    assert 'Global rank stays independent.' in html


def test_dashboard_exposes_self_hosted_model_index() -> None:
    html = _html()
    assert 'Self-Hosted Model Index' in html
    assert 'self_hosted_model_index' in html
    assert 'renderSelfHostedIndex();' in html
    assert 'selfHostedGrid' in html
