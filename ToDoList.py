from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional

app = FastAPI(title="TODO List")

next_id = 4

Tasks = [[1, 'Task1', 'false'], [2, "Task2", "true"], [3, "Task3", "true"]]

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, description="The task title cannot be empty")

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, description="Title cannot be empty if provided")
    done: Optional[bool] = None

@app.get("/")
async def get():
   return { "name": "To-Do List API", "version": "1.0", "endpoints":[["/"],["/tasks"],["/health"]]}
   
@app.get("/health")
async def health():
   return {"status":"ok"}

@app.get("/tasks")
async def getTasks():
   return {"Data": Tasks}

@app.get("/task/{id}")
async def get_task(id: int):
    for task in Tasks:
        if task[0] == id:
            return {"id": task[0], "task": task[1], "done": task[2]}  
            
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, 
        detail=f"error : Task id {id} not found"
    )

@app.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(task_input: TaskCreate):
    global next_id
    new_data = [next_id, task_input.title.strip(), "false"]
    Tasks.append(new_data)
    next_id += 1
    return {"id": new_data[0], "task": new_data[1], "done": new_data[2]}

@app.put("/tasks/{id}", status_code=status.HTTP_200_OK)
async def update_task(id: int, task_input: TaskUpdate):
    for task in Tasks:
       
        if task[0] == id:
            if task_input.title is None and task_input.done is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Payload cannot be empty. Provide 'title' or 'done'."
                )
            
          
            if task_input.title is not None:
                task[1] = task_input.title.strip()
          
            if task_input.done is not None:
                task[2] = "true" if task_input.done else "false"     
                
            return {"id": task[0], "task": task[1], "done": task[2]}
     
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Task with id {id} not found"
    )

@app.delete("/tasks/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(id: int):
    global Tasks
    for index, task in enumerate(Tasks):
        if task[0] == id:
            Tasks.pop(index)
            return
            
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Task with id {id} not found"
    )
