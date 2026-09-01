"""
Schemas package.
Pydantic models for API request/response validation.
"""

from app.schemas.admin import (
    AdminOrderInterventionRequest,
    BoosterApplicationResponse,
    BoosterApplicationReviewRequest,
)
from app.schemas.booster_service import (
    BoosterServiceCreate,
    BoosterServiceListResponse,
    BoosterServiceOrderCreate,
    BoosterServiceResponse,
    BoosterServiceUpdate,
)
from app.schemas.chat import (
    ChatMessageResponse,
    ChatUserBrief,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationParticipantResponse,
    ConversationReadRequest,
    ConversationResponse,
    InviteAdminResponse,
    MessageCreateRequest,
    UnreadSummaryResponse,
)
from app.schemas.game import (
    GameCreate,
    GameListResponse,
    GameResponse,
    GameServiceTemplate,
    GameUpdate,
)
from app.schemas.notification import (
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCount,
    UserPreferenceResponse,
    UserPreferenceUpdate,
)
from app.schemas.order import (
    AIAnalysisResponse,
    OrderAnalyzeRequest,
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderUpdate,
    UserBrief,
)
from app.schemas.review import (
    ReviewCreate,
    ReviewListResponse,
    ReviewResponse,
    ReviewUpdate,
)
from app.schemas.search import SearchResponse, SearchType
from app.schemas.user import (
    MessageResponse,
    PasswordChange,
    TokenRefresh,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    UserUpdate,
)

__all__ = [
    "AIAnalysisResponse",
    "AdminOrderInterventionRequest",
    # Admin/application schemas
    "BoosterApplicationResponse",
    "BoosterApplicationReviewRequest",
    # Booster service schemas
    "BoosterServiceCreate",
    "BoosterServiceListResponse",
    "BoosterServiceOrderCreate",
    "BoosterServiceResponse",
    "BoosterServiceUpdate",
    "ChatMessageResponse",
    "ChatUserBrief",
    # Chat schemas
    "ConversationCreateRequest",
    "ConversationListResponse",
    "ConversationParticipantResponse",
    "ConversationReadRequest",
    "ConversationResponse",
    "GameCreate",
    "GameListResponse",
    "GameResponse",
    # Game schemas
    "GameServiceTemplate",
    "GameUpdate",
    "InviteAdminResponse",
    "MessageCreateRequest",
    "MessageResponse",
    # Notification schemas
    "NotificationListResponse",
    "NotificationResponse",
    "NotificationUnreadCount",
    # Order schemas
    "OrderAnalyzeRequest",
    "OrderCreate",
    "OrderListResponse",
    "OrderResponse",
    "OrderUpdate",
    "PasswordChange",
    # Review schemas
    "ReviewCreate",
    "ReviewListResponse",
    "ReviewResponse",
    "ReviewUpdate",
    "SearchResponse",
    # Search schemas
    "SearchType",
    "TokenRefresh",
    "TokenResponse",
    "UnreadSummaryResponse",
    "UserBrief",
    "UserLogin",
    "UserPreferenceResponse",
    "UserPreferenceUpdate",
    # User schemas
    "UserRegister",
    "UserResponse",
    "UserUpdate",
]
