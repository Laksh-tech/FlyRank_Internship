## Storage Layer Migration & Architecture Proof
- **Storage Decoupling:** Swapped SQLite logic for a dedicated `PostgresTaskRepository`. By maintaining a uniform repository interface and injecting it via FastAPI's `Depends()`, the route signatures and HTTP handler logic in `app/main.py` remained completely unchanged.
- **Persistence Proof:**
  1. Executed `docker compose up -d` and inserted a task via `POST /tasks`.
  2. Executed `docker compose down` to destroy application and database containers.
  3. Re-executed `docker compose up -d` and fetched `GET /tasks`.
  4. The inserted record remained intact due to the named Docker volume `postgres_data` mounted at `/var/lib/postgresql/data`.
