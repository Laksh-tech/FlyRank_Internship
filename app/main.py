from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, HTTPException, status, Depends
from app.schemas import TaskResponse, TaskCreate, TaskUpdate
from app.repository import PostgresTaskRepository
from app.database import init_pool, close_pool

# Application Lifespan Events
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()
    yield
    close_pool()

app = FastAPI(title="TODO List", lifespan=lifespan)

# Dependency Injection for Repository
def get_repository() -> PostgresTaskRepository:
    return PostgresTaskRepository()

# --- Endpoints ---
@app.get("/")
def read_root():
    return {"name": "To-Do List API", "version": "1.0", "endpoints": ["/", "/health", "/tasks"]}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/tasks", response_model=List[TaskResponse])
def get_tasks(repo: PostgresTaskRepository = Depends(get_repository)):
    tasks = repo.get_all()
    if not tasks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tasks found ❌"
        )
    return tasks

@app.get("/task/{id}", response_model=TaskResponse)
def get_task(id: int, repo: PostgresTaskRepository = Depends(get_repository)):
    task = repo.get_by_id(id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task id {id} not found"
        )
    return task

@app.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(task_input: TaskCreate, repo: PostgresTaskRepository = Depends(get_repository)):
    return repo.create(task_input)

@app.put("/tasks/{id}", response_model=TaskResponse)
def update_task(id: int, task_input: TaskUpdate, repo: PostgresTaskRepository = Depends(get_repository)):
    updated_task = repo.update(id, task_input)
    if not updated_task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id {id} not found 👎🏽"
        )
    return updated_task

@app.delete("/tasks/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(id: int, repo: PostgresTaskRepository = Depends(get_repository)):
    deleted = repo.delete(id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task id {id} not found ❌"
        )
    return None