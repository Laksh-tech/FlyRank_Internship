from pydantic import BaseModel, Field
from typing import Optional

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