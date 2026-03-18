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

SessionExecutionMode = Literal[
    "inspect",
    "workspace",
    "full-auto",
    "external",
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


class SessionLaunchRequest(BaseModel):
    execution_mode: Optional[SessionExecutionMode] = None


class RenameSessionRequest(BaseModel):
    name: str


class KeyRequest(BaseModel):
    key: str = Field(..., description="Control key to send to tmux/Codex session")
    repeat: int = Field(default=1, ge=1, le=20)


class DecisionRequest(BaseModel):
    auto_approve_type: Optional[str] = None


class SessionFocusRequest(BaseModel):
    reason: Optional[str] = None


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
    is_default: bool = False


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
    last_activity_at: Optional[str] = None
    last_prompt: Optional[str] = None
    execution_mode: Optional[SessionExecutionMode] = None
    codex_session_id: Optional[str] = None
    codex_session_file: Optional[str] = None
    codex_source: Optional[str] = None
    codex_model: Optional[str] = None


class SessionHubResponse(BaseModel):
    repo_id: str
    generated_at: str
    focus_session_id: Optional[str] = None
    focus_reason: Optional[str] = None
    focus_updated_at: Optional[str] = None
    current_session_id: Optional[str] = None
    suggested_session_id: Optional[str] = None
    shared_session_id: Optional[str] = None
    live_session_ids: list[str] = Field(default_factory=list)
    recent_session_ids: list[str] = Field(default_factory=list)
    archived_session_ids: list[str] = Field(default_factory=list)
    external_recent_session_ids: list[str] = Field(default_factory=list)
    sync_hint: Optional[str] = None
    sessions: list[SessionInfo] = Field(default_factory=list)


class CommandResult(BaseModel):
    ok: bool
    code: int
    stdout: str
    stderr: str
