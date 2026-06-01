from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.models import DigestResult, ProfileSpec

app = FastAPI(title="SENTINEL reg-watch", version="0.1.0")

PROFILES_DIR = Path(__file__).parent.parent / "data" / "profiles"


def _load_profiles() -> list[ProfileSpec]:
    profiles: list[ProfileSpec] = []
    for path in sorted(PROFILES_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            profiles.append(ProfileSpec.model_validate(raw))
        except Exception as exc:
            raise ValueError(f"Profile validation failed [{path.name}]: {exc}") from exc
    return profiles


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/digest/run", response_model=DigestResult)
def digest_run() -> DigestResult:
    try:
        profiles = _load_profiles()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    project_ids = [p.project_id for p in profiles]

    placeholder_html = (
        "<section><h2>SENTINEL — 주간 규제 다이제스트 (stub)</h2>"
        "<p>수집·스크리닝 파이프라인 미구현. 검증된 프로파일: "
        f"{', '.join(project_ids)}</p></section>"
    )

    return DigestResult(
        html=placeholder_html,
        summary=f"Stub run. Loaded {len(profiles)} profiles: {project_ids}",
        stats={
            "profiles_loaded": len(profiles),
            "project_ids": project_ids,
            "items_collected": 0,
            "items_passed_screen1": 0,
            "items_passed_screen2": 0,
            "items_new": 0,
        },
    )
