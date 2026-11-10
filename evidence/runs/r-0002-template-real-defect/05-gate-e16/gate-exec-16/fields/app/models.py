import os as _tp_os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pydantic import EmailStr, computed_field
from sqlmodel import Field, Relationship, SQLModel

def get_datetime_utc() -> datetime:
    return datetime.now(UTC)

class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)

class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)

class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)

class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)

class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)

class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    items: list[Item] = Relationship(back_populates='owner', cascade_delete=True)

class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None

class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int

class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)
    price: Decimal | None = Field(default=None, **{'gt': 0} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__1' else {'ge': 1} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__2' else {'ge': 0}, **{'max_digits': 11} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__3' else {'max_digits': 9} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__4' else {'max_digits': 10}, **{'decimal_places': 3} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__5' else {'decimal_places': 1} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__6' else {'decimal_places': 2})
    discount_percent: int = Field(default=0, **{'gt': 0} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__7' else {'ge': 1} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__8' else {'ge': 0}, **{'le': 100} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__9' else {'lt': 101} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__10' else {'lt': 100})

class ItemCreate(ItemBase):
    pass

class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)
    price: Decimal | None = Field(default=None, **{'max_digits': 11} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__11' else {'max_digits': 9} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__12' else {'max_digits': 10}, **{'decimal_places': 3} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__13' else {'decimal_places': 1} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__14' else {'decimal_places': 2})
    discount_percent: int | None = Field(default=None, **{'gt': 0} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__15' else {'ge': 1} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__16' else {'ge': 0}, **{'le': 100} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__17' else {'lt': 101} if _tp_os.environ.get('MUTANT_UNDER_TEST', '') == 'app.models.field__18' else {'lt': 100})

class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    owner_id: uuid.UUID = Field(foreign_key='user.id', nullable=False, ondelete='CASCADE')
    owner: User | None = Relationship(back_populates='items')

class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None

    @computed_field
    @property
    def final_price(self) -> Decimal | None:
        if self.price is None:
            return None
        return round(self.price * (100 - self.discount_percent) / 100, 2)

class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int

class Message(SQLModel):
    message: str

class Token(SQLModel):
    access_token: str
    token_type: str = 'bearer'

class TokenPayload(SQLModel):
    sub: str | None = None

class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
