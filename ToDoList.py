from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
import sqlite3

app = FastAPI(title="TODO List")
DB_PATH = "task.db"

# Helper function to open database connections safely per thread/request
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# Database Initialization
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status BOOLEAN NOT NULL DEFAULT 0
        )
        """)
        # Seed default data if table is empty
        cur.execute("SELECT COUNT(*) FROM tasks")
        if cur.fetchone()[0] == 0:
            cur.executemany(
                "INSERT INTO tasks (title, status) VALUES (?, ?)",
                [("Buy MILK", False), ("Read Book", False), ("WorkOut", False)]
            )
        conn.commit()

init_db()

# --- Pydantic Schemas ---
class TaskResponse(BaseModel):
    id: int
    title: str
    status: bool

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, description="The task title cannot be empty")
    status: bool = False

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, description="Title cannot be empty if provided")
    status: Optional[bool] = None


# --- Endpoints ---
@app.get("/")
def read_root():
    return {
        "name": "To-Do List API",
        "version": "1.0",
        "endpoints": ["/", "/health", "/tasks"]
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/tasks", response_model=List[TaskResponse])
def get_tasks(db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute("SELECT id, title, status FROM tasks")
    rows = cur.fetchall()
    
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tasks found ❌"
        )
    return [dict(row) for row in rows]

@app.get("/task/{id}", response_model=TaskResponse)
def get_task(id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute("SELECT id, title, status FROM tasks WHERE id = ?", (id,))
    row = cur.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Task id {id} not found"
        )
    return dict(row)   

@app.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(task_input: TaskCreate, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "INSERT INTO tasks (title, status) VALUES (?, ?)",
        (task_input.title, task_input.status)
    )
    db.commit()
    inserted_id = cur.lastrowid

    cur.execute("SELECT id, title, status FROM tasks WHERE id = ?", (inserted_id,))
    new_task = cur.fetchone()
    return dict(new_task)

@app.put("/tasks/{id}", response_model=TaskResponse)
def update_task(id: int, task_input: TaskUpdate, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    
    # 1. Check if record exists
    cur.execute("SELECT id, title, status FROM tasks WHERE id = ?", (id,))
    existing_task = cur.fetchone()
    if not existing_task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id {id} not found 👎🏽"
        )
    
    # 2. Update fields if provided (partial update support)
    new_title = task_input.title if task_input.title is not None else existing_task["title"]
    new_status = task_input.status if task_input.status is not None else existing_task["status"]
    
    cur.execute(
        "UPDATE tasks SET title = ?, status = ? WHERE id = ?",
        (new_title, new_status, id)
    )
    db.commit()

    return {"id": id, "title": new_title, "status": bool(new_status)}

@app.delete("/tasks/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute("DELETE FROM tasks WHERE id = ?", (id,))
    db.commit()
    
    if cur.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task id {id} not found ❌"
        )
    return None