from uuid import UUID
from fastapi import APIRouter, Depends
from vibradiag.services.evidence_service import resolve
from .identity import current_principal

router=APIRouter(tags=['exploratory facts and evidence'])


@router.get('/facts/{identifier}')
def fact(identifier:UUID,principal=Depends(current_principal)):
    return resolve(identifier,principal)


@router.get('/evidence/{identifier}')
def evidence(identifier:UUID,principal=Depends(current_principal)):
    return resolve(identifier,principal,evidence=True)
