from pydantic import BaseModel

class UserRegister(BaseModel):
    email: str
    username: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class ProfileUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None