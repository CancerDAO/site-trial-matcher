from __future__ import annotations

import hmac
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .disease_mapping import map_patients_with_model
from .normalization import DiseaseMappingError, canonical_disease_concepts, load_aliases, normalize_patient
from .web_service import RunManager, RunNotFound, WebSettings, ensure_dataset


class PatientInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    patient_id: str = Field(min_length=1, max_length=64)
    country: str = Field(default="中国", max_length=40)
    cancer_type: str = Field(min_length=1, max_length=160)
    primary_disease: Optional[str] = Field(default=None, max_length=160)
    histology: Optional[str] = Field(default=None, max_length=160)
    disease_stage: Optional[str] = Field(default=None, max_length=80)
    stage: Optional[str] = Field(default=None, max_length=80)
    age: Optional[float] = None
    sex: Optional[str] = Field(default=None, max_length=20)
    ecog: Optional[float] = None
    treatment_lines_completed: Optional[float] = None
    mutations: list[str] = Field(default_factory=list, max_length=30)
    biomarkers: list[str] = Field(default_factory=list, max_length=30)
    biomarkers_known: list[str] = Field(default_factory=list, max_length=30)
    prior_therapies: list[str] = Field(default_factory=list, max_length=50)
    comorbidities: list[str] = Field(default_factory=list, max_length=50)
    current_medications: list[str] = Field(default_factory=list, max_length=80)
    pregnant: Optional[bool] = None

    @field_validator("age", "ecog", "treatment_lines_completed")
    @classmethod
    def finite_number(cls, value: Optional[float], info):
        if value is None:
            return value
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        bounds = {"age": (0, 120), "ecog": (0, 5), "treatment_lines_completed": (0, 50)}
        low, high = bounds[info.field_name]
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")
        return value

    @field_validator(
        "mutations", "biomarkers", "biomarkers_known", "prior_therapies",
        "comorbidities", "current_medications",
    )
    @classmethod
    def bounded_strings(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            text = str(value).strip()
            if len(text) > 240:
                raise ValueError("list item is too long")
            if text:
                cleaned.append(text)
        return cleaned


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patients: list[PatientInput] = Field(min_length=1, max_length=50)
    mode: Literal["deterministic", "full"] = "deterministic"


def _settings(request: Request) -> WebSettings:
    return request.app.state.settings


def _manager(request: Request) -> RunManager:
    return request.app.state.manager


def require_access(
    settings: Annotated[WebSettings, Depends(_settings)],
    authorization: Annotated[Optional[str], Header()] = None,
) -> None:
    expected = os.environ.get("SITE_TRIAL_API_TOKEN", "").strip()
    if not expected:
        return
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def create_app(settings: Optional[WebSettings] = None) -> FastAPI:
    configured = settings or WebSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_dataset(configured)
        app.state.settings = configured
        app.state.manager = RunManager(configured)
        try:
            yield
        finally:
            app.state.manager.close()

    app = FastAPI(
        title="China Trial Partner Matcher",
        version="0.2.0",
        docs_url="/api/docs" if os.environ.get("SITE_TRIAL_ENABLE_DOCS", "0") == "1" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    too_large = int(content_length) > 512 * 1024
                except ValueError:
                    return JSONResponse({"detail": "invalid content length"}, status_code=400)
                if too_large:
                    return JSONResponse({"detail": "request too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/runs") else "no-cache"
        return response

    @app.get("/healthz")
    def health(settings: Annotated[WebSettings, Depends(_settings)]):
        ontology = load_aliases(settings.aliases_path)
        return {
            "ok": True,
            "model_enabled": settings.model_enabled,
            "disease_ontology_version": ontology.ontology_version,
        }

    @app.get("/api/dataset", dependencies=[Depends(require_access)])
    def dataset(settings: Annotated[WebSettings, Depends(_settings)]):
        from .web_service import dataset_metadata
        ontology = load_aliases(settings.aliases_path)
        return {
            **dataset_metadata(settings.db_path),
            "model_enabled": settings.model_enabled,
            "disease_ontology_version": ontology.ontology_version,
            "disease_concept_count": len(ontology.concepts),
        }

    @app.get("/api/disease-concepts", dependencies=[Depends(require_access)])
    def disease_concepts(settings: Annotated[WebSettings, Depends(_settings)]):
        ontology = load_aliases(settings.aliases_path)
        return {
            "schema_version": ontology.schema_version,
            "ontology_version": ontology.ontology_version,
            "concepts": canonical_disease_concepts(ontology),
        }

    @app.post("/api/runs", status_code=202, dependencies=[Depends(require_access)])
    def create_run(
        payload: CreateRunRequest,
        manager: Annotated[RunManager, Depends(_manager)],
        settings: Annotated[WebSettings, Depends(_settings)],
    ):
        if payload.mode == "full" and not settings.model_enabled:
            raise HTTPException(status_code=409, detail="model screening is not enabled")
        patients = [item.model_dump(exclude_none=True) for item in payload.patients]
        patient_ids = [item["patient_id"] for item in patients]
        if len(patient_ids) != len(set(patient_ids)):
            raise HTTPException(status_code=422, detail="duplicate patient_id")
        ontology = load_aliases(settings.aliases_path)
        prepared: list[dict[str, Any]] = []
        unresolved: list[tuple[int, dict[str, Any]]] = []
        try:
            for index, patient in enumerate(patients):
                try:
                    normalized = normalize_patient(patient, ontology)
                    mapping = normalized["disease_mapping"]
                    prepared.append({
                        **patient,
                        "canonical_disease_id": normalized["canonical_disease_id"],
                        "canonical_cancer_type": normalized["canonical_cancer_type"],
                        "disease_mapping_method": mapping["method"],
                        "disease_mapping_confidence": mapping["confidence"],
                        "disease_mapping_evidence": mapping["evidence"],
                    })
                except DiseaseMappingError as error:
                    if error.code != "unmapped_disease" or not settings.model_enabled:
                        raise
                    prepared.append(patient)
                    unresolved.append((index, patient))
            if unresolved:
                mapped, _ = map_patients_with_model(
                    [patient for _, patient in unresolved], ontology
                )
                for (index, _), patient in zip(unresolved, mapped):
                    prepared[index] = patient
                for patient in prepared:
                    normalize_patient(patient, ontology)
        except DiseaseMappingError as error:
            raise HTTPException(status_code=422, detail={
                "code": error.code,
                "patient_id": error.patient_id,
                "candidates": error.candidates,
                "message": "患者癌种无法唯一映射到受控疾病概念，任务未启动。",
            }) from None
        except Exception as error:
            raise HTTPException(status_code=502, detail={
                "code": "disease_mapping_model_failed",
                "message": str(error)[:300],
            }) from None
        return manager.create(prepared, mode=payload.mode)

    @app.get("/api/runs/{run_id}", dependencies=[Depends(require_access)])
    def run_status(run_id: str, manager: Annotated[RunManager, Depends(_manager)]):
        try:
            return manager.get(run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.post("/api/runs/{run_id}/cancel", dependencies=[Depends(require_access)])
    def cancel_run(run_id: str, manager: Annotated[RunManager, Depends(_manager)]):
        try:
            return manager.cancel(run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.get("/api/runs/{run_id}/results", dependencies=[Depends(require_access)])
    def run_results(
        run_id: str,
        manager: Annotated[RunManager, Depends(_manager)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=20)] = 5,
    ):
        try:
            return manager.results(run_id, offset=offset, limit=limit)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None
        except RuntimeError:
            raise HTTPException(status_code=409, detail="result not ready") from None

    @app.get("/api/runs/{run_id}/results/{patient_index}", dependencies=[Depends(require_access)])
    def patient_result(
        run_id: str,
        patient_index: int,
        manager: Annotated[RunManager, Depends(_manager)],
    ):
        try:
            return manager.patient_result(run_id, patient_index)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None
        except RuntimeError:
            raise HTTPException(status_code=409, detail="result not ready") from None
        except IndexError:
            raise HTTPException(status_code=404, detail="patient result not found") from None

    static_root = configured.project_root / "web"
    if static_root.is_dir():
        app.mount("/", StaticFiles(directory=static_root, html=True), name="web")
    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run(
        "china_trial_demo.web:app",
        host=os.environ.get("SITE_TRIAL_HOST", "127.0.0.1"),
        port=int(os.environ.get("SITE_TRIAL_PORT", "8080")),
        reload=False,
    )
