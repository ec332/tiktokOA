import os as _tp_os
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException
from sqlmodel import col, func, select
from app.api.deps import CurrentUser, SessionDep
from app.models import Item, ItemCreate, ItemPublic, ItemsPublic, ItemUpdate, Message
router = APIRouter(prefix='/items', tags=['items'])

@router.get('/', response_model=ItemsPublic)
def read_items(session: SessionDep, current_user: CurrentUser, skip: int=0, limit: int=100) -> Any:
    """
    Retrieve items.
    """
    _tp_m = _tp_os.environ.get('MUTANT_UNDER_TEST', '')
    if _tp_m == 'stats':
        from mutmut.mutation import trampoline as _tp_t
        _tp_t.record_trampoline_hit('app.api.routes.items.x_read_items')
    if _tp_m == 'app.api.routes.items.x_read_items__ast_1':
        if not current_user.is_superuser:
            count_statement = select(func.count()).select_from(Item)
            count = session.exec(count_statement).one()
            statement = select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        else:
            count_statement = select(func.count()).select_from(Item).where(Item.owner_id == current_user.id)
            count = session.exec(count_statement).one()
            statement = select(Item).where(Item.owner_id == current_user.id).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        items_public = [ItemPublic.model_validate(item) for item in items]
        return ItemsPublic(data=items_public, count=count)
    elif _tp_m == 'app.api.routes.items.x_read_items__ast_2':
        if current_user.is_superuser:
            count_statement = select(func.count()).select_from(Item)
            count = session.exec(count_statement).one()
            statement = select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        else:
            count_statement = select(func.count()).select_from(Item).where(Item.owner_id != current_user.id)
            count = session.exec(count_statement).one()
            statement = select(Item).where(Item.owner_id == current_user.id).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        items_public = [ItemPublic.model_validate(item) for item in items]
        return ItemsPublic(data=items_public, count=count)
    elif _tp_m == 'app.api.routes.items.x_read_items__ast_3':
        if current_user.is_superuser:
            count_statement = select(func.count()).select_from(Item)
            count = session.exec(count_statement).one()
            statement = select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        else:
            count_statement = select(func.count()).select_from(Item).where(Item.owner_id == current_user.id)
            count = session.exec(count_statement).one()
            statement = select(Item).where(Item.owner_id != current_user.id).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        items_public = [ItemPublic.model_validate(item) for item in items]
        return ItemsPublic(data=items_public, count=count)
    else:
        if current_user.is_superuser:
            count_statement = select(func.count()).select_from(Item)
            count = session.exec(count_statement).one()
            statement = select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        else:
            count_statement = select(func.count()).select_from(Item).where(Item.owner_id == current_user.id)
            count = session.exec(count_statement).one()
            statement = select(Item).where(Item.owner_id == current_user.id).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
            items = session.exec(statement).all()
        items_public = [ItemPublic.model_validate(item) for item in items]
        return ItemsPublic(data=items_public, count=count)

@router.get('/{id}', response_model=ItemPublic)
def read_item(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Any:
    """
    Get item by ID.
    """
    _tp_m = _tp_os.environ.get('MUTANT_UNDER_TEST', '')
    if _tp_m == 'stats':
        from mutmut.mutation import trampoline as _tp_t
        _tp_t.record_trampoline_hit('app.api.routes.items.x_read_item')
    if _tp_m == 'app.api.routes.items.x_read_item__ast_1':
        item = session.get(Item, id)
        if not not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser and item.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        return item
    elif _tp_m == 'app.api.routes.items.x_read_item__ast_2':
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=405, detail='Item not found')
        if not current_user.is_superuser and item.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        return item
    elif _tp_m == 'app.api.routes.items.x_read_item__ast_4':
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser or item.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        return item
    elif _tp_m == 'app.api.routes.items.x_read_item__ast_5':
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser and item.owner_id == current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        return item
    else:
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser and item.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        return item

@router.post('/', response_model=ItemPublic)
def create_item(*, session: SessionDep, current_user: CurrentUser, item_in: ItemCreate) -> Any:
    """
    Create new item.
    """
    item = Item.model_validate(item_in, update={'owner_id': current_user.id})
    session.add(item)
    session.commit()
    session.refresh(item)
    return item

@router.put('/{id}', response_model=ItemPublic)
def update_item(*, session: SessionDep, current_user: CurrentUser, id: uuid.UUID, item_in: ItemUpdate) -> Any:
    """
    Update an item.
    """
    _tp_m = _tp_os.environ.get('MUTANT_UNDER_TEST', '')
    if _tp_m == 'stats':
        from mutmut.mutation import trampoline as _tp_t
        _tp_t.record_trampoline_hit('app.api.routes.items.x_update_item')
    if _tp_m == 'app.api.routes.items.x_update_item__ast_1':
        item = session.get(Item, id)
        if not not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser and item.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        update_dict = item_in.model_dump(exclude_unset=True)
        item.sqlmodel_update(update_dict)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item
    elif _tp_m == 'app.api.routes.items.x_update_item__ast_2':
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=405, detail='Item not found')
        if not current_user.is_superuser and item.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        update_dict = item_in.model_dump(exclude_unset=True)
        item.sqlmodel_update(update_dict)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item
    elif _tp_m == 'app.api.routes.items.x_update_item__ast_3':
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not (not current_user.is_superuser and item.owner_id != current_user.id):
            raise HTTPException(status_code=403, detail='Not enough permissions')
        update_dict = item_in.model_dump(exclude_unset=True)
        item.sqlmodel_update(update_dict)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item
    elif _tp_m == 'app.api.routes.items.x_update_item__ast_5':
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser and item.owner_id == current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        update_dict = item_in.model_dump(exclude_unset=True)
        item.sqlmodel_update(update_dict)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item
    elif _tp_m == 'app.api.routes.items.x_update_item__ast_6':
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser and item.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail='Not enough permissions')
        update_dict = item_in.model_dump(exclude_unset=True)
        item.sqlmodel_update(update_dict)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item
    else:
        item = session.get(Item, id)
        if not item:
            raise HTTPException(status_code=404, detail='Item not found')
        if not current_user.is_superuser and item.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='Not enough permissions')
        update_dict = item_in.model_dump(exclude_unset=True)
        item.sqlmodel_update(update_dict)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

@router.delete('/{id}')
def delete_item(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Message:
    """
    Delete an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail='Item not found')
    if not current_user.is_superuser and item.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail='Not enough permissions')
    session.delete(item)
    session.commit()
    return Message(message='Item deleted successfully')
