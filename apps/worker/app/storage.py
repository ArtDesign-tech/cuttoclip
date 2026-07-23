from __future__ import annotations

import json
import os
import re
import uuid
import shutil
from pathlib import Path

from .models import ErrorInfo, Job, Project, utc_now


def app_name() -> str:
    """Storage namespace, e.g. ``CutToClip`` or ``CutToClip Beta``.

    The Beta build sets ``CUTTOCLIP_APP_NAME=CutToClip Beta`` so its projects and
    renders live in directories fully separate from the main app.
    """

    return os.getenv("CUTTOCLIP_APP_NAME", "").strip() or "CutToClip"


def default_data_root() -> Path:
    configured = os.getenv("CUTTOCLIP_DATA")
    if configured:
        return Path(configured).expanduser().resolve()
    name = app_name()
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data) / name).resolve()
        return (Path.home() / "AppData" / "Local" / name).resolve()
    return (Path.home() / ".local" / "share" / name).resolve()


def default_output_root() -> Path:
    configured = os.getenv("CUTTOCLIP_OUTPUT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "Videos" / app_name()).resolve()


def safe_slug(value: str, fallback: str = "project") -> str:
    value = Path(value).stem
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip(" ._")
    value = re.sub(r"[\s._-]+", "-", value).strip("-").lower()
    return value[:72] or fallback


class ProjectStore:
    def __init__(self, root: Path | None = None, output_root: Path | None = None) -> None:
        self.root = (root or default_data_root()).resolve()
        self.output_root = (output_root or default_output_root()).resolve()
        self.projects_dir = self.root / "projects"
        self.jobs_dir = self.root / "jobs"
        self.projects: dict[str, Project] = {}
        self.jobs: dict[str, Job] = {}
        self.ensure_directories()

    def ensure_directories(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        path = self.projects_dir / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def project_output_dir(self, project: Project) -> Path:
        base_slug = safe_slug(project.sourceLabel, f"project-{project.id[:8]}")
        # The project id suffix makes ownership deterministic even when two
        # newly-created projects use files with the same name.
        path = self.output_root / f"{base_slug}-{project.id[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def save_project(self, project: Project) -> Project:
        project.updatedAt = utc_now()
        self.projects[project.id] = project
        self._atomic_json(self.project_dir(project.id) / "project.json", project.model_dump(mode="json"))
        return project

    def save_job(self, job: Job) -> Job:
        job.updatedAt = utc_now()
        self.jobs[job.id] = job
        self._atomic_json(self.jobs_dir / f"{job.id}.json", job.model_dump(mode="json"))
        return job

    def load(self) -> None:
        self.ensure_directories()
        self.projects.clear()
        self.jobs.clear()
        for path in self.projects_dir.glob("*/project.json"):
            try:
                project = Project.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self.projects[project.id] = project
        for path in self.jobs_dir.glob("*.json"):
            try:
                job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self.jobs[job.id] = job

    def mark_interrupted_jobs(self) -> list[Job]:
        interrupted: list[Job] = []
        for job in self.jobs.values():
            if job.status not in {"queued", "running"}:
                continue
            job.status = "interrupted"
            job.stage = "interrupted"
            job.stageKey = "job.interrupted"
            job.stageParams = {}
            job.completedAt = utc_now()
            job.error = ErrorInfo(
                code="WORKER_RESTARTED",
                message="The worker restarted before this job completed. Start the operation again to retry it.",
                retryable=True,
            )
            self.save_job(job)
            project = self.projects.get(job.projectId)
            if project and project.status in {"queued", "preparing", "analyzing", "rendering"}:
                project.status = "interrupted"
                self.save_project(project)
            interrupted.append(job)
        return interrupted

    def delete_project(self, project_id: str, *, remove_outputs: bool = True) -> None:
        """Remove a project's records, source files, and (by default) rendered outputs.

        The source directory under ``projects_dir`` is always removed. When
        ``remove_outputs`` is True the rendered clip directory under
        ``output_root`` is deleted as well, so a project delete is a full cleanup.
        """
        project = self.projects.pop(project_id, None)
        shutil.rmtree(self.projects_dir / project_id, ignore_errors=True)
        if remove_outputs and project is not None:
            output_dir = self.project_output_dir(project)
            # Guard: only delete a directory that is genuinely under output_root
            # and carries this project's id suffix, never something broader.
            try:
                resolved = output_dir.resolve()
                if (
                    resolved.parent == self.output_root
                    and resolved.name.endswith(project_id[:8])
                ):
                    shutil.rmtree(resolved, ignore_errors=True)
            except OSError:
                pass
        for job_id, job in list(self.jobs.items()):
            if job.projectId != project_id:
                continue
            self.jobs.pop(job_id, None)
            try:
                (self.jobs_dir / f"{job_id}.json").unlink(missing_ok=True)
            except OSError:
                pass
