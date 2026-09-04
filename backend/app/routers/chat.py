"""FastAPI router for the Garden Assistant Chatbot."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.auth import require_current_user
from backend.app.core.database import get_db
from backend.app.models.garden import User
from backend.app.services.chat_service import ask_garden_copilot

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single conversation turn."""
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message text")


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""
    message: str = Field(..., min_length=1, max_length=2000, description="User's current message")
    history: list[ChatMessage] = Field(default_factory=list, description="Recent conversation turns")


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""
    reply: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("", response_model=ChatResponse)
async def chat_endpoint(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
):
    """Accept a user message and return a context-aware garden assistant reply.

    Authentication: a valid Bearer token is required. ``require_current_user``
    raises ``401 Not authenticated`` when the token is missing or invalid.

    Role-based data isolation:
    - Secondary grower (``is_admin == False``) → the LLM context is scoped
      strictly to ``current_user.id``; they can never see other growers'
      plants or alerts.
    - Admin (``is_admin == True``) → the global overview of all active plants
      and unresolved alerts is provided.
    """
    history_dicts = [{"role": m.role, "content": m.content} for m in payload.history]

    # Admins get the global overview (None); growers are locked to their own id.
    scope_user_id = None if getattr(current_user, "is_admin", False) else current_user.id

    reply = await ask_garden_copilot(
        user_message=payload.message,
        history=history_dicts,
        db=db,
        scope_user_id=scope_user_id,
    )

    return ChatResponse(reply=reply)
