from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, HTTPException, status, Depends
from app.schemas import TaskResponse, TaskCreate, TaskUpdate
from app.repository import PostgresTaskRepository
from app.database import init_pool, close_pool
from app.auth import router as auth_router, get_current_user

# Application Lifespan Events
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()
    yield
    close_pool()

app = FastAPI(title="TODO List", lifespan=lifespan)
app.include_router(auth_router)

# Dependency Injection for Repository
def get_repository() -> PostgresTaskRepository:
    return PostgresTaskRepository()

# --- Root / health ---
@app.get("/")
def read_root():
    return {
        "name": "To-Do List API",
        "version": "1.0",
        "endpoints": [
            "/", "/health", "/tasks",
            "/auth/signup", "/auth/login", "/auth/logout",
            "/public/info", "/protected/profile", "/protected/dashboard",
        ],
    }

@app.get("/health")
def health():
    return {"status": "ok"}

# --- Auth-guarded demo routes required by the assignment ---
@app.get("/public/info")
def public_info():
    return {"message": "Welcome stranger! This info is public."}

@app.get("/protected/profile")
def protected_profile(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "created_at": user.created_at}

# Second protected route, reusing the SAME guard (Stage 4 checkpoint: no new auth code)
@app.get("/protected/dashboard")
def protected_dashboard(user=Depends(get_current_user)):
    return {"message": f"Welcome back, {user.email}", "id": user.id}

# --- Existing task CRUD (unchanged) ---
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