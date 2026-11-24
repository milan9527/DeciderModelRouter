"""Session history and performance APIs; every key is scoped to the signed-in user."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from app.schemas import StrictModel

router = APIRouter(prefix="/api/gateway", tags=["Sessions and history"])
ID = r"^[a-f0-9]{24}$"


class SessionBody(StrictModel):
    title: str = Field(min_length=1, max_length=80)


class ReviewBody(StrictModel):
    passed: bool
    rubric: str = Field(min_length=3, max_length=1000)
    notes: str = Field(default="", max_length=2000)


@router.get("/sessions")
def sessions(request: Request):
    return request.app.state.history.sessions(request.state.user["username"])


@router.post("/sessions", status_code=201)
def create_session(body: SessionBody, request: Request):
    return request.app.state.history.create_session(request.state.user["username"], body.title)


@router.post("/sessions/{session_id}")
def rename_session(session_id: str, body: SessionBody, request: Request):
    h, user = request.app.state.history, request.state.user["username"]
    item = h.session(user, session_id)
    item["title"] = body.title
    h.records.put(h.user_key(user), "SESSION#" + session_id, item)
    return item


@router.get("/history")
def history(
    request: Request,
    session_id: str | None = Query(None, pattern=ID),
    cursor: str | None = Query(None, max_length=256),
    limit: int = Query(30, ge=1, le=100),
    days: int = Query(30, ge=1, le=180),
):
    return request.app.state.history.runs(
        request.state.user["username"],
        session_id=session_id,
        cursor=cursor,
        limit=limit,
        days=days,
    )


@router.get("/history/{run_id}")
def detail(run_id: str, request: Request):
    return request.app.state.history.detail(request.state.user["username"], run_id)


@router.get("/performance")
def performance(
    request: Request,
    session_id: str | None = Query(None, pattern=ID),
    days: int = Query(30, ge=1, le=180),
):
    return request.app.state.history.statistics(
        request.state.user["username"],
        session_id=session_id,
        days=days,
    )


@router.post("/history/{run_id}/review")
def review(run_id: str, body: ReviewBody, request: Request):
    import time

    h, user = request.app.state.history, request.state.user["username"]
    item = h.detail(user, run_id)
    if item["mode"] != "live" or item["status"] not in ("executed", "incomplete", "error"):
        raise HTTPException(409, "仅能评审已经结束的实时执行")
    if not item["execute_target"]:
        raise HTTPException(409, "仅分析路由没有目标结果可供业务验收")
    item.pop("events")
    item["review"] = {
        **body.model_dump(),
        "reviewer": user,
        "reviewed_at": int(time.time() * 1000),
        "source": "human_review",
        "automatically_calibrated": False,
    }
    h.records.put_many(h.summary_rows(user, item))
    return item["review"]
