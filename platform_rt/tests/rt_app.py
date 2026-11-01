"""A toy API for platform_rt's own tests: the shapes the SUT has (paging, create, errors)."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(openapi_url="/api/v1/openapi.json")


class ItemIn(BaseModel):
    title: str | None = None


class ItemCreate(BaseModel):
    title: str
    description: str | None = None


class Page(BaseModel):
    data: list[dict]
    count: int


ITEMS = [{"id": i, "title": f"t{i}"} for i in range(5)]


@app.get("/api/v1/items/", response_model=Page)
def list_items(skip: int = 0, limit: int = Query(100, le=100)):
    return {"data": ITEMS[skip : skip + limit], "count": len(ITEMS)}


@app.post("/api/v1/items/")
def create(item: ItemCreate) -> dict:
    if item.title is None:
        raise HTTPException(422, "title may not be null")
    return {"id": 99, "title": item.title}


@app.get("/api/v1/mutant/")
def mutant() -> dict:
    import os

    return {"mutant": os.environ.get("MUTANT_UNDER_TEST")}


@app.get("/api/v1/boom/")
def boom():
    raise RuntimeError("server bug")


@app.get("/api/v1/broken-page/", responses={200: {"model": Page}})
def broken_page():
    return JSONResponse({"data": []})  # lacks the documented `count`
