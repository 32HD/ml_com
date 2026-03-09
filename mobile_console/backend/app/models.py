from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


SessionStatus = Literal[
    "idle",
    "running",
    "waiting_input",
    "waiting_approval",
    "failed",
    "completed",
]


class RepoOpenRequest(BaseModel):
    path: str = Field(..., description="Absolute path to repo")
    name: Optional[str] = None


class ProjectInitRequest(BaseModel):
    name: str = Field(..., description="Local project folder name under workspace root")
    github_owner: Optional[str] = Field(default=None, description="GitHub owner/user/org")
    github_repo: Optional[str] = Field(default=None, description="GitHub repository name")
    description: Optional[str] = Field(default=None, description="GitHub repository description")
    private: Optional[bool] = Field(default=None, description="Create private repository")
    create_github_repo: bool = True
    push_initial_commit: bool = True


class PromptRequest(BaseModel):
    prompt: str


class RenameSessionRequest(BaseModel):
    name: str


class KeyRequest(BaseModel):
    key: str = Field(..., description="Control key to send to tmux/Codex session")
    repeat: int = Field(default=1, ge=1, le=20)


class DecisionRequest(BaseModel):
    auto_approve_type: Optional[str] = None


class RunCmdRequest(BaseModel):
    cmd: str


class RunTestRequest(BaseModel):
    target: Optional[str] = None


class FileWriteRequest(BaseModel):
    path: str
    content: str


class RepoInfo(BaseModel):
    id: str
    name: str
    path: str
    branch: str
    dirty_files: int
    session_status: Optional[str] = None
    updated_at: Optional[str] = None


class ProjectInitResponse(BaseModel):
    repo: RepoInfo
    github_owner: Optional[str] = None
    github_repo: Optional[str] = None
    github_url: Optional[str] = None
    remote_url: Optional[str] = None
    existed: Optional[bool] = None


class SessionInfo(BaseModel):
    id: str
    repo_id: str
    tmux_session: str
    name: Optional[str] = None
    status: SessionStatus
    created_at: str
    updated_at: str
    last_prompt: Optional[str] = None


class CommandResult(BaseModel):
    ok: bool
    code: int
    stdout: str
    stderr: str
