# FlyRank AI Track Backend AI Intern

## Week 3 – Assignment 1: Upgrading to SQLite Database Persistence

This project is an upgraded RESTful CRUD API built with **FastAPI** as part of the **FlyRank AI Track Backend Internship (Week 3)**. 

Building upon Week 2's in-memory storage implementation, Week 3 transitions the API to use persistent storage via **SQLite (`sqlite3`)**, addressing key database challenges including connection management, thread safety, auto-increment sequences, and standard SQL operations.

---

## 🛠️ Tech Stack

* **Language:** Python 3
* **Framework:** FastAPI
* **Data Validation:** Pydantic
* **Database:** SQLite (`sqlite3`)
* **ASGI Server:** Uvicorn

---

## ✨ Features

* **Persistent Storage:** Tasks persist across app restarts using SQLite.
* **Thread-Safe DB Connections:** Per-request database dependency injection prevents thread contention.
* **Full CRUD Functionality:** Create, Read, Update, and Delete endpoints fully wired to SQL queries.
* **Pydantic Validation:** Input validation for titles and execution statuses.
* **HTTP Status Codes:** Accurate status handling (`200 OK`, `201 Created`, `204 No Content`, `404 Not Found`).
* **Interactive Documentation:** Out-of-the-box Swagger UI (`/docs`).

---

## 🔗 API Endpoints

| Method | Endpoint | Description | Status Code |
| :--- | :--- | :--- | :--- |
| **GET** | `/` | API Information | `200 OK` |
| **GET** | `/health` | Health Check | `200 OK` |
| **GET** | `/tasks` | Return all tasks | `200 OK` |
| **GET** | `/task/{id}` | Return a task by ID | `200 OK` / `404 Not Found` |
| **POST** | `/tasks` | Create a new task | `201 Created` |
| **PUT** | `/tasks/{id}` | Update an existing task | `200 OK` / `404 Not Found` |
| **DELETE** | `/tasks/{id}` | Delete a task | `204 No Content` / `404 Not Found` |

---

## 🚀 Running the Project

### 1. Install Dependencie
```bash
pip install fastapi uvicorn


### Testing Endpoints via cURL

```bash
# 1. Fetch All Tasks
curl -X GET "http://127.0.0.1:8000/tasks"

# 2. Create a New Task
curl -X POST "http://127.0.0.1:8000/tasks" \
     -H "Content-Type: application/json" \
     -d '{"title": "Review PRs", "status": false}'

# 3. Update an Existing Task
curl -X PUT "http://127.0.0.1:8000/tasks/1" \
     -H "Content-Type: application/json" \
     -d '{"status": true}'

# 4. Delete a Task
curl -X DELETE "http://127.0.0.1:8000/tasks/1"
```

