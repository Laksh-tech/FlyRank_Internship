""""PROMPT: Task: Write a CRUD API using FastAPI 
# Constraints : Use in-memory container.
# Endpoints & Operations:
# ("/") -> { "name": "Task API", "version": "1.0", "endpoints": ["/tasks"] }
# ("/health") -> { "status": "ok" } 
# ("/tasks") -> returns the whole list.
# ("/tasks/{id}") -> return the task at id 
# f no task has that id, return status 404 with a JSON error: { "error": "Task 99 not found" }
# ("/tasks") .POST- Add The client sends the new task as JSON in the request body. { "title": "Buy milk" }
#  if title is missing or empty, return 400 ("Bad Request") with a JSON error saying what's wrong.
# ("/tasks/{id}") -> PUT: Updae task's title and/or done.Returns the updated task. Unknown id → 404 . Empty/invalid body → 400 .
# Add DELETE /tasks/:id — removes the task. Return status 204 ("No Content" — success, nothing to say) with an empty body. Unknown id → 404 .

# Provide curl commands to test all api endpoints 
"""""
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# In-memory storage
tasks = []
next_id = 1


# -----------------------------
# Models
# -----------------------------
class TaskCreate(BaseModel):
    title: str


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    done: Optional[bool] = None


# -----------------------------
# Routes
# -----------------------------

@app.get("/")
def root():
    seed_tasks()
    return {
        "name": "Task API",
        "version": "1.0",
        "endpoints": ["/tasks"]
    }

def seed_tasks():
    global tasks, next_id

    tasks = [
        {"id": 1, "title": "Buy milk", "done": False},
        {"id": 2, "title": "Finish assignment", "done": True},
        {"id": 3, "title": "Go for a walk", "done": False},
    ]

    next_id = 4


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tasks")
def get_tasks(done: Optional[bool] = None, search: Optional[str] = None):
    result = tasks

    # Filter by completion status
    if done is not None:
        result = [task for task in result if task["done"] == done]

    # Search by title (case-insensitive)
    if search:
        result = [
            task for task in result
            if search.lower() in task["title"].lower()
        ]
    return result

@app.get("/tasks/{task_id}")
def get_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            return task

    raise HTTPException(
        status_code=404,
        detail={"error": f"Task {task_id} not found"}
    )

@app.post("/tasks", status_code=201)
def create_task(task: TaskCreate):
    global next_id

    if not task.title or task.title.strip() == "":
        raise HTTPException(
            status_code=400,
            detail={"error": "Title is required and cannot be empty"}
        )

    new_task = {
        "id": next_id,
        "title": task.title,
        "done": False
    }

    tasks.append(new_task)
    next_id += 1

    return new_task

@app.get("/stats")
def get_stats():
    total = len(tasks)
    done = sum(task["done"] for task in tasks)
    open_tasks = total - done

    return {
        "total": total,
        "done": done,
        "open": open_tasks
    }

@app.put("/tasks/{task_id}")
def update_task(task_id: int, updates: TaskUpdate):

    if updates.title is None and updates.done is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "Request body must contain title and/or done"}
        )

    for task in tasks:

        if task["id"] == task_id:

            if updates.title is not None:
                if updates.title.strip() == "":
                    raise HTTPException(
                        status_code=400,
                        detail={"error": "Title cannot be empty"}
                    )
                task["title"] = updates.title

            if updates.done is not None:
                task["done"] = updates.done

            return task

    raise HTTPException(
        status_code=404,
        detail={"error": f"Task {task_id} not found"}
    )

@app.post("/reset")
def reset_tasks():
    seed_tasks()
    return {
        "message": "Tasks reset successfully",
        "tasks": tasks
    }

@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int):

    for index, task in enumerate(tasks):

        if task["id"] == task_id:
            tasks.pop(index)
            return Response(status_code=204)

    raise HTTPException(
        status_code=404,
        detail={"error": f"Task {task_id} not found"}
    )
