from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import Candidate, ErrorInfo, Job, Project, ProjectSettings
from app.storage import ProjectStore, app_name, default_data_root, default_output_root, safe_slug


def test_app_name_defaults_and_beta_override(monkeypatch) -> None:
    monkeypatch.delenv("CUTTOCLIP_APP_NAME", raising=False)
    assert app_name() == "CutToClip"
    monkeypatch.setenv("CUTTOCLIP_APP_NAME", "CutToClip Beta")
    assert app_name() == "CutToClip Beta"


def test_beta_storage_roots_are_separate_from_main(monkeypatch) -> None:
    monkeypatch.delenv("CUTTOCLIP_DATA", raising=False)
    monkeypatch.delenv("CUTTOCLIP_OUTPUT", raising=False)
    monkeypatch.setenv("CUTTOCLIP_APP_NAME", "CutToClip Beta")
    data_root = default_data_root()
    output_root = default_output_root()
    assert data_root.name == "CutToClip Beta"
    assert output_root.name == "CutToClip Beta"

    monkeypatch.setenv("CUTTOCLIP_APP_NAME", "CutToClip")
    assert default_data_root() != data_root
    assert default_output_root() != output_root


def test_explicit_data_output_env_overrides_app_name(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUTTOCLIP_APP_NAME", "CutToClip Beta")
    monkeypatch.setenv("CUTTOCLIP_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CUTTOCLIP_OUTPUT", str(tmp_path / "out"))
    assert default_data_root() == (tmp_path / "data").resolve()
    assert default_output_root() == (tmp_path / "out").resolve()


def test_candidate_accepts_manual_source_and_enforces_real_clip_duration() -> None:
    candidate = Candidate(
        id="manual-1",
        startSeconds=5,
        endSeconds=20,
        title="Manual moment",
        source="manual",
    )
    assert candidate.source == "manual"
    with pytest.raises(ValidationError, match="between 15 and 90"):
        Candidate(id="bad", startSeconds=0, endSeconds=14.9, title="Too short")


def test_settings_reject_an_inverted_duration_range() -> None:
    with pytest.raises(ValidationError, match="minSeconds"):
        ProjectSettings(duration={"minSeconds": 70, "maxSeconds": 30})


def test_store_persists_projects_and_marks_active_jobs_interrupted(tmp_path: Path) -> None:
    first = ProjectStore(tmp_path / "data", tmp_path / "outputs")
    project = Project(
        id="project-1",
        sourceLabel="A Project.mov",
        sourceKind="file",
        sourcePath=str(tmp_path / "source.mov"),
    )
    first.save_project(project)
    first.save_job(Job(id="job-1", projectId=project.id, type="prepare", status="running"))

    restored = ProjectStore(tmp_path / "data", tmp_path / "outputs")
    restored.load()
    interrupted = restored.mark_interrupted_jobs()

    assert restored.projects[project.id].sourceLabel == "A Project.mov"
    assert [job.id for job in interrupted] == ["job-1"]
    assert restored.jobs["job-1"].status == "interrupted"
    assert restored.jobs["job-1"].error == ErrorInfo(
        code="WORKER_RESTARTED",
        message="The worker restarted before this job completed. Start the operation again to retry it.",
        retryable=True,
    )


def test_safe_slug_removes_windows_path_characters() -> None:
    assert safe_slug('  My: Project? "Final".mov  ') == "my-project-final"


def test_output_directories_are_stable_and_unique_per_project(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data", tmp_path / "outputs")
    first = Project(id="aaaaaaaa-1111", sourceLabel="recording.mp4", sourceKind="file")
    second = Project(id="bbbbbbbb-2222", sourceLabel="recording.mp4", sourceKind="file")
    first_path = store.project_output_dir(first)
    assert first_path == store.project_output_dir(first)
    assert first_path != store.project_output_dir(second)
    assert first_path.name == "recording-aaaaaaaa"
