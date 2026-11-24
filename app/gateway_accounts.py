"""Self-managed accounts for the independent gateway; no public signup or Cognito."""

import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.auth import canonical_username, hash_password, save_user
from app.schemas import StrictModel

router = APIRouter(prefix="/api/gateway", tags=["Accounts"])


def account(request):
    user = request.app.state.auth.store.get("USER#" + request.state.user["username"])
    if not user or not user["enabled"]:
        raise HTTPException(401, "请重新登录")
    return user


def administrator(request):
    user = account(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "此操作需要管理员权限")
    return user


class PasswordBody(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class UserBody(StrictModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)
    admin: bool = False


class UserUpdate(StrictModel):
    enabled: bool | None = None
    new_password: str | None = Field(None, min_length=12, max_length=128)


def directory(request, user):
    item = {
        "username": user["username"],
        "enabled": user["enabled"],
        "role": user.get("role", "user"),
        "updated_at": int(time.time() * 1000),
    }
    request.app.state.history.records.put("ADMIN#DIRECTORY", "USER#" + user["username"], item)
    return item


@router.get("/account")
def me(request: Request):
    user = account(request)
    directory(request, user)
    return {
        "username": user["username"],
        "role": user.get("role", "user"),
        "expires_at": request.state.user["expires_at"],
        "authentication": "self_managed",
    }


@router.post("/account/password")
def change_password(body: PasswordBody, request: Request):
    auth = request.app.state.auth
    user = account(request)
    version = user["version"]
    # Reuse the bounded, rate-limited login check to prevent password guessing here.
    token, _ = auth.login(user["username"], body.current_password, None)
    from app.auth import session_key

    auth.store.delete(session_key(token))
    if body.current_password == body.new_password:
        raise HTTPException(422, "新密码不能与当前密码相同")
    user.update(password_hash=hash_password(body.new_password), version=secrets.token_hex(16))
    if not auth.store.replace_version(user, version):
        raise HTTPException(409, "账户已被更新，请重新登录")
    return {"ok": True, "reauthenticate": True}


@router.get("/users")
def users(request: Request):
    user = administrator(request)
    directory(request, user)
    page = request.app.state.history.records.query("ADMIN#DIRECTORY", "USER#", limit=500)
    current = []
    for row in page["items"]:
        actual = request.app.state.auth.store.get("USER#" + row["username"])
        if actual:
            current.append(
                {
                    **row,
                    "enabled": actual["enabled"],
                    "role": actual.get("role", "user"),
                }
            )
    return {**page, "items": current}


@router.post("/users", status_code=201)
def create_user(body: UserBody, request: Request):
    administrator(request)
    try:
        username = canonical_username(body.username)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    store = request.app.state.auth.store
    if store.get("USER#" + username):
        raise HTTPException(409, "用户名已存在")
    try:
        save_user(store, username, body.password, role="admin" if body.admin else "user")
    except Exception:
        if store.get("USER#" + username):
            raise HTTPException(409, "用户名已存在") from None
        raise
    user = store.get("USER#" + username)
    return directory(request, user)


@router.post("/users/{username}")
def update_user(username: str, body: UserUpdate, request: Request):
    admin = administrator(request)
    try:
        username = canonical_username(username)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if username == admin["username"]:
        raise HTTPException(409, "请使用修改密码功能；不能在此停用自己的账户")
    if body.enabled is None and body.new_password is None:
        raise HTTPException(422, "请提供账户状态或新密码")
    store = request.app.state.auth.store
    user = store.get("USER#" + username)
    if not user:
        raise HTTPException(404, "用户不存在")
    version = user["version"]
    user.update(
        enabled=user["enabled"] if body.enabled is None else body.enabled,
        version=secrets.token_hex(16),
    )
    if body.new_password:
        user["password_hash"] = hash_password(body.new_password)
    if not store.replace_version(user, version):
        raise HTTPException(409, "账户已被更新，请刷新后重试")
    return directory(request, user)
