from fastapi import APIRouter
from .v1 import analyses,diagnoses,knowledge,identity,assets,measurements,persistent_analyses,tool_runs,evidence

router=APIRouter(prefix='/api/v1')
router.include_router(identity.router)
router.include_router(assets.router)
router.include_router(measurements.router)
router.include_router(persistent_analyses.router)
router.include_router(tool_runs.router)
router.include_router(evidence.router)
router.include_router(analyses.router)
router.include_router(diagnoses.router)
router.include_router(knowledge.router)
