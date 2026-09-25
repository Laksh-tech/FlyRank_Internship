
Main · PY
from fastapi import FastAPI, Header, Request
from app.auth import router as auth_router, supabase, check_rate_limit
 
app = FastAPI(title="TODO List (AI version)")
app.include_router(auth_router)
 
 
@app.get("/public/info")
def public_info():
    return {"success": True, "message": "Welcome stranger! This info is public."}
 
 
@app.get("/protected/profile")
def protected_profile(authorization: str = Header(None)):
    if not authorization:
        return {"success": False, "flag": "NO_TOKEN"}
 
    token = authorization.split(" ")[1]
    user = supabase.auth.get_user(token)
    return {"success": True, "id": user.user.id, "email": user.user.email}
 
