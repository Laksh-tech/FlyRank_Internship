# FlyRank AI Track Backend AI Intern

## Week 2 – Assignment 1: Build Your First CRUD API

This project is a simple RESTful CRUD API built with **FastAPI** as part of the FlyRank AI Track Backend Internship (Week 2).

The goal of this assignment was to understand how backend APIs work by implementing every endpoint manually using **in-memory storage** before comparing the implementation with AI-generated code.

---

## Tech Stack

* Python 3
* FastAPI
* Pydantic
* Uvicorn

---

## Features

* In-memory task storage (no database)
* Create, Read, Update and Delete tasks
* Input validation using Pydantic
* Proper HTTP status codes
* JSON responses
* Interactive Swagger documentation

---

## API Endpoints

| Method | Endpoint      | Description             |
| ------ | ------------- | ----------------------- |
| GET    | `/`           | API information         |
| GET    | `/health`     | Health check            |
| GET    | `/tasks`      | Return all tasks        |
| GET    | `/task/{id}`  | Return a task by ID     |
| POST   | `/tasks`      | Create a new task       |
| PUT    | `/tasks/{id}` | Update an existing task |
| DELETE | `/tasks/{id}` | Delete a task           |

---

## Running the Project

Install dependencies:

```bash
pip install fastapi uvicorn
```

Run the application:

```bash
uvicorn main:app --reload
```

The API will be available at:

```
http://127.0.0.1:8000
```

Swagger UI:

```
http://127.0.0.1:8000/docs
```

---

# AI vs Me

## AI Prompt

I asked ChatGPT to generate the same CRUD API from scratch using the following prompt:

> Build a RESTful CRUD API using Python and FastAPI. Use an in-memory list to store tasks instead of a database. Implement endpoints for GET `/`, GET `/health`, GET `/tasks`, GET `/task/{id}`, POST `/tasks`, PUT `/tasks/{id}`, and DELETE `/tasks/{id}`. Use Pydantic models for validation. Return appropriate HTTP status codes (200, 201, 204, 400, and 404). Validate that task titles cannot be empty. Return JSON responses and make the API compatible with FastAPI Swagger UI. Do not use any database or ORM.

---

## What the AI Did Better

* Used dictionaries instead of lists to represent tasks, making the code easier to read and extend.
* Added clearer Pydantic models and cleaner validation.
* Returned more structured JSON responses.
* Organized the code into logical sections, improving readability.

I understand these improvements and can explain why they make the API easier to maintain.

---

## What the AI Got Wrong

* It used the endpoint `/tasks/{id}` while my implementation used `/task/{id}`.
* It returned error responses using FastAPI's default `"detail"` field instead of exactly matching the requested JSON format.
* It made implementation decisions (such as the internal task structure) that were not explicitly requested.

---

## What My Prompt Forgot to Specify

My prompt did not clearly specify:

* Whether tasks should be stored as lists or dictionaries.
* The exact JSON response format for every endpoint.
* The exact endpoint naming (`/task/{id}` vs `/tasks/{id}`).
* Whether boolean values should be stored as actual booleans or as `"true"`/`"false"` strings.

Because these details were omitted, the AI chose reasonable defaults that differed from my implementation.

---

## Improved Prompt

After reviewing the AI-generated solution, I updated my prompt to be more specific:

> Build a FastAPI CRUD API using an in-memory list (no database). Store each task as a list in the format `[id, title, done]`, where `done` is stored as the strings `"true"` or `"false"`. Implement the endpoints exactly as `/`, `/health`, `/tasks`, `/task/{id}`, `POST /tasks`, `PUT /tasks/{id}`, and `DELETE /tasks/{id}`. Use Pydantic for validation, return the specified HTTP status codes, and preserve the exact JSON response formats.

**What changed:** The regenerated version matched my endpoint names, response format, and internal data structure much more closely because the prompt removed ambiguity.

---

## Lessons Learned

This assignment demonstrated that writing a precise specification is just as important as writing code. Building the API manually first allowed me to evaluate the AI-generated solution critically, identify differences, and improve my prompt to produce more accurate results. It reinforced that AI is most effective when given clear requirements and that understanding the underlying implementation is essential for reviewing and maintaining generated code.
