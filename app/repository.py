from typing import List, Optional
from psycopg2.extras import RealDictCursor
from app.database import get_db_connection
from app.schemas import TaskCreate, TaskUpdate

class PostgresTaskRepository:
    def get_all(self) -> List[dict]:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, title, status FROM tasks ORDER BY id ASC")
                return [dict(row) for row in cur.fetchall()]

    def get_by_id(self, task_id: int) -> Optional[dict]:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, title, status FROM tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def create(self, task: TaskCreate) -> dict:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO tasks (title, status) VALUES (%s, %s) RETURNING id, title, status",
                    (task.title, task.status)
                )
                new_task = cur.fetchone()
                conn.commit()
                return dict(new_task)

    def update(self, task_id: int, task: TaskUpdate) -> Optional[dict]:
        existing = self.get_by_id(task_id)
        if not existing:
            return None

        new_title = task.title if task.title is not None else existing["title"]
        new_status = task.status if task.status is not None else existing["status"]

        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "UPDATE tasks SET title = %s, status = %s WHERE id = %s RETURNING id, title, status",
                    (new_title, new_status, task_id)
                )
                updated_task = cur.fetchone()
                conn.commit()
                return dict(updated_task)

    def delete(self, task_id: int) -> bool:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
                conn.commit()
                return cur.rowcount > 0