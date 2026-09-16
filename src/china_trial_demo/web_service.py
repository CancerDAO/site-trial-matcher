from __future__ import annotations

import json
import os
import shutil
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .database import connect
from .model_screening import (
    ModelRunCancelled,
    execute_model_jobs,
    merge_model_results,
    prepare_model_jobs,
)
from .pipeline import match_batch
from .registry_importer import import_mcp_snapshot


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
PUBLIC_STAGES = {"prepare", "execute", "generate", "deliver", "complete"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class WebSettings:
    project_root: Path
    data_root: Path
    db_path: Path
    snapshot_path: Path
    aliases_path: Path
    skill_path: Path
    model_enabled: bool
    model_workers: int
    task_workers: int
    retention_hours: int

    @classmethod
    def from_env(cls) -> "WebSettings":
        project_root = Path(os.environ.get(
            "SITE_TRIAL_PROJECT_ROOT", Path(__file__).resolve().parents[2]
        )).resolve()
        data_root = Path(os.environ.get("SITE_TRIAL_DATA_ROOT", project_root / "var")).resolve()
        return cls(
            project_root=project_root,
            data_root=data_root,
            db_path=Path(os.environ.get("SITE_TRIAL_DB", data_root / "trials.db")).resolve(),
            snapshot_path=Path(os.environ.get(
                "SITE_TRIAL_SNAPSHOT", project_root / "data" / "who-mcp-china-latest-200.json"
            )).resolve(),
            aliases_path=Path(os.environ.get(
                "SITE_TRIAL_ALIASES", project_root / "data" / "cancer_aliases.json"
            )).resolve(),
            skill_path=Path(os.environ.get(
                "SITE_TRIAL_SKILL", project_root / "skills" / "china-trial-exclusion-gater" / "SKILL.md"
            )).resolve(),
            model_enabled=(
                os.environ.get("SITE_TRIAL_MODEL_ENABLED", "0") == "1"
                and bool(os.environ.get("MINIMAX_API_KEY", "").strip())
            ),
            model_workers=max(1, min(int(os.environ.get("SITE_TRIAL_MODEL_WORKERS", "4")), 8)),
            # One persisted queue owns model concurrency. Multiple process-local
            # queues would multiply the external API load and complicate cancel.
            task_workers=1,
            retention_hours=max(1, int(os.environ.get("SITE_TRIAL_RETENTION_HOURS", "24"))),
        )


def ensure_dataset(settings: WebSettings) -> dict[str, Any]:
    settings.data_root.mkdir(parents=True, exist_ok=True)
    if not settings.db_path.is_file():
        if not settings.snapshot_path.is_file():
            raise FileNotFoundError(f"trial snapshot not found: {settings.snapshot_path}")
        temporary = settings.db_path.with_suffix(settings.db_path.suffix + ".building")
        temporary.unlink(missing_ok=True)
        import_mcp_snapshot(temporary, settings.snapshot_path)
        temporary.replace(settings.db_path)
    return dataset_metadata(settings.db_path)


def dataset_metadata(db_path: Path) -> dict[str, Any]:
    with connect(db_path, readonly=True) as connection:
        metadata = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM metadata")}
        counts = {
            "trials": connection.execute("SELECT count(*) FROM trials WHERE record_status='有效'").fetchone()[0],
            "criteria": connection.execute("SELECT count(*) FROM eligibility_criteria WHERE record_status='有效'").fetchone()[0],
            "organizations": connection.execute("SELECT count(*) FROM organizations").fetchone()[0],
        }
    return {
        "schema_version": metadata.get("schema_version"),
        "database_as_of": metadata.get("mcp_database_as_of"),
        "counts": counts,
    }


class RunNotFound(KeyError):
    pass


class RunManager:
    def __init__(self, settings: WebSettings, *, matcher: Callable[..., dict[str, Any]] = match_batch):
        self.settings = settings
        self.matcher = matcher
        self.runs_root = settings.data_root / "runs"
        self.state_root = settings.data_root / "states"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=settings.task_workers, thread_name_prefix="trial-web")
        self._lock = threading.Lock()
        self._cancel: dict[str, threading.Event] = {}
        self._futures: dict[str, Future] = {}
        self._recover_interrupted()
        self.cleanup_expired()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _state_path(self, run_id: str) -> Path:
        return self.state_root / f"{run_id}.json"

    def _run_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id

    def _read(self, run_id: str) -> dict[str, Any]:
        path = self._state_path(run_id)
        if not path.is_file():
            raise RunNotFound(run_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = self._read(run_id)
            state.update(changes)
            state["updated_at"] = utc_now()
            atomic_json(self._state_path(run_id), state)
            return state

    def _recover_interrupted(self) -> None:
        for path in self.state_root.glob("*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("status") in {"queued", "running", "stopping"}:
                state.update({
                    "status": "failed",
                    "stage": "prepare",
                    "error_code": "service_restarted",
                    "message": "服务重启中断了任务，请重新提交。",
                    "updated_at": utc_now(),
                    "finished_at": utc_now(),
                })
                atomic_json(path, state)

    def create(self, patients: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
        self.cleanup_expired()
        run_id = str(uuid4())
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        patients_path = run_dir / "patients.jsonl"
        patients_path.write_text(
            "".join(json.dumps(patient, ensure_ascii=False, allow_nan=False) + "\n" for patient in patients),
            encoding="utf-8",
        )
        state = {
            "run_id": run_id,
            "status": "queued",
            "stage": "prepare",
            "progress": 0,
            "message": "任务已进入队列。",
            "mode": mode,
            "patient_count": len(patients),
            "dataset": dataset_metadata(self.settings.db_path),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "finished_at": None,
            "result_ready": False,
            "error_code": None,
        }
        atomic_json(self._state_path(run_id), state)
        cancel = threading.Event()
        with self._lock:
            self._cancel[run_id] = cancel
            self._futures[run_id] = self._executor.submit(self._execute, run_id, cancel)
        return state

    def cleanup_expired(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.retention_hours)
        removed = 0
        for path in self.state_root.glob("*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                updated = datetime.fromisoformat(str(state.get("updated_at") or ""))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if state.get("status") not in TERMINAL_STATUSES or updated >= cutoff:
                continue
            shutil.rmtree(self._run_dir(path.stem), ignore_errors=True)
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def get(self, run_id: str) -> dict[str, Any]:
        return self._read(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read(run_id)
            if state["status"] in TERMINAL_STATUSES:
                return state
            event = self._cancel.get(run_id)
            future = self._futures.get(run_id)
            if event:
                event.set()
            queued_cancelled = bool(future and future.cancel())
            state.update({"status": "stopping", "message": "正在停止匹配并清理中间数据。", "updated_at": utc_now()})
            atomic_json(self._state_path(run_id), state)
        if queued_cancelled:
            return self._finish_cancelled(run_id)
        return state

    def results(self, run_id: str, *, offset: int, limit: int) -> dict[str, Any]:
        state = self._read(run_id)
        if state.get("status") != "completed" or not state.get("result_ready"):
            raise RuntimeError("result_not_ready")
        payload = json.loads((self._run_dir(run_id) / "final-results.json").read_text(encoding="utf-8"))
        rows = payload.get("results") or []
        summaries = [
            {"patient_id": row.get("patient_id"), "summary": row.get("summary") or {}, "timing_ms": row.get("timing_ms") or {}}
            for row in rows[offset:offset + limit]
        ]
        return {
            "run_id": run_id,
            "schema_version": payload.get("schema_version"),
            "dataset": state.get("dataset"),
            "patient_count": len(rows),
            "offset": offset,
            "limit": limit,
            "results": summaries,
        }

    def patient_result(self, run_id: str, patient_index: int) -> dict[str, Any]:
        state = self._read(run_id)
        if state.get("status") != "completed" or not state.get("result_ready"):
            raise RuntimeError("result_not_ready")
        payload = json.loads((self._run_dir(run_id) / "final-results.json").read_text(encoding="utf-8"))
        rows = payload.get("results") or []
        if patient_index < 0 or patient_index >= len(rows):
            raise IndexError(patient_index)
        return {"run_id": run_id, "dataset": state.get("dataset"), "patient_index": patient_index, "result": rows[patient_index]}

    def _check_cancel(self, run_id: str, cancel: threading.Event) -> None:
        if cancel.is_set():
            raise ModelRunCancelled(f"run {run_id} cancelled")

    def _finish_cancelled(self, run_id: str) -> dict[str, Any]:
        shutil.rmtree(self._run_dir(run_id), ignore_errors=True)
        return self._update(
            run_id,
            status="cancelled",
            stage="complete",
            progress=100,
            message="匹配已退出，中间数据已清理。",
            finished_at=utc_now(),
            result_ready=False,
        )

    def _execute(self, run_id: str, cancel: threading.Event) -> None:
        run_dir = self._run_dir(run_id)
        patients_path = run_dir / "patients.jsonl"
        deterministic_path = run_dir / "deterministic-results.json"
        final_path = run_dir / "final-results.json"
        try:
            self._update(run_id, status="running", stage="prepare", progress=5, message="正在准备患者与试验数据。")
            self._check_cancel(run_id, cancel)
            self._update(run_id, stage="execute", progress=15, message="正在执行本地召回和确定性排除。")
            deterministic = self.matcher(
                self.settings.db_path,
                patients_path,
                deterministic_path,
                aliases_path=self.settings.aliases_path,
            )
            self._check_cancel(run_id, cancel)
            state = self._read(run_id)
            if state["mode"] == "full":
                model_dir = run_dir / "model"
                prepared = prepare_model_jobs(
                    self.settings.db_path,
                    patients_path,
                    deterministic_path,
                    model_dir,
                    self.settings.skill_path,
                )
                total = max(1, prepared["job_count"])
                self._update(run_id, progress=35, message=f"正在核查自由文本入排条件（0/{total}）。")

                def progress(done: int, expected: int) -> None:
                    percent = 35 + round(45 * done / max(1, expected))
                    self._update(run_id, progress=percent, message=f"正在核查自由文本入排条件（{done}/{expected}）。")

                execute_model_jobs(
                    model_dir,
                    workers=self.settings.model_workers,
                    should_cancel=cancel.is_set,
                    on_progress=progress,
                )
                self._check_cancel(run_id, cancel)
                self._update(run_id, stage="generate", progress=85, message="正在合并结果并生成报告数据。")
                merge_model_results(model_dir, final_path)
            else:
                self._update(run_id, stage="generate", progress=85, message="正在生成确定性预筛结果。")
                final_path.write_text(json.dumps(deterministic, ensure_ascii=False, indent=2), encoding="utf-8")
            self._check_cancel(run_id, cancel)
            self._update(run_id, stage="deliver", progress=95, message="正在保存结果。")
            self._check_cancel(run_id, cancel)
            self._update(
                run_id,
                status="completed",
                stage="complete",
                progress=100,
                message="匹配完成，结果可查看。",
                finished_at=utc_now(),
                result_ready=True,
            )
        except ModelRunCancelled:
            self._finish_cancelled(run_id)
        except Exception as error:
            self._update(
                run_id,
                status="failed",
                stage="complete",
                message="任务暂时失败，请检查服务日志后重试。",
                error_code=type(error).__name__,
                finished_at=utc_now(),
                result_ready=False,
            )
        finally:
            with self._lock:
                self._cancel.pop(run_id, None)
                self._futures.pop(run_id, None)
