from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from .identity import current_principal
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.identity_service import asset_allowed

router=APIRouter()


@router.get('/assets')
def list_assets(page: int=Query(1,ge=1), page_size: int=Query(20,ge=1,le=100), principal=Depends(current_principal)):
    with transaction() as connection:
        rows=connection.execute(text('''SELECT a.id,a.asset_code,a.name,a.equipment_type FROM assets a
            JOIN asset_grants g ON g.asset_id=a.id AND g.organization_id=a.organization_id
            WHERE a.organization_id=:org AND g.user_id=:user AND g.can_read AND a.status='active'
            ORDER BY a.asset_code,a.id LIMIT :limit OFFSET :offset'''),
            dict(org=principal.organization_id,user=principal.user_id,limit=page_size,offset=(page-1)*page_size)).mappings().all()
    return {'items':[dict(row) for row in rows], 'page':page, 'page_size':page_size}


@router.get('/assets/{asset_id}/measurement-points')
def list_points(asset_id: UUID, page: int=Query(1,ge=1),page_size: int=Query(20,ge=1,le=100),principal=Depends(current_principal)):
    with transaction() as connection:
        if not asset_allowed(connection,principal,asset_id):
            raise HTTPException(404,'Resource not found')
        rows=connection.execute(text('''SELECT id,point_code,name FROM measurement_points
            WHERE organization_id=:org AND asset_id=:asset AND status='active'
            ORDER BY point_code,id LIMIT :limit OFFSET :offset'''),
            dict(org=principal.organization_id,asset=asset_id,limit=page_size,offset=(page-1)*page_size)).mappings().all()
    return {'items':[dict(row) for row in rows], 'page':page,'page_size':page_size}
