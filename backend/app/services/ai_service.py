"""
AI Service module for DeepSeek LLM integration.
Handles game order analysis and requirement extraction using AI.
"""

import json
import logging
from typing import Any

from fastapi import HTTPException, status
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from app.core.config import settings

logger = logging.getLogger(__name__)

# =============================================================================
# PROMPT CONSTANTS
# Isolated for maintainability and easy modification
# =============================================================================

REQUIREMENT_ANALYSIS_SYSTEM_PROMPT: str = """你是一个游戏订单分析助手。请分析用户的中文描述，提取以下字段并返回纯JSON格式：game_name(游戏名), current_rank(当前段位), target_rank(目标段位), price(预算金额,纯数字), role(位置), server(区服), service_type(服务类型), requirements(特殊要求数组)。如果用户描述中有违规词（如'外挂','涉黄'），返回字段 'is_risky': true。"""

REQUIREMENT_ANALYSIS_USER_TEMPLATE: str = """请分析以下用户需求描述，并提取关键信息：

{text}

请严格以JSON格式返回结果，不要包含任何其他文字说明。"""

# Default model for DeepSeek API
DEEPSEEK_MODEL: str = "deepseek-chat"

# DeepSeek API base URL (OpenAI-compatible)
DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"


# =============================================================================
# RESPONSE SCHEMAS
# Expected structure of AI responses
# =============================================================================

class AnalysisResultKeys:
    """Keys expected in the analysis result."""

    GAME_NAME: str = "game_name"
    CURRENT_RANK: str = "current_rank"
    TARGET_RANK: str = "target_rank"
    PRICE: str = "price"
    ROLE: str = "role"
    SERVER: str = "server"
    SERVICE_TYPE: str = "service_type"
    REQUIREMENTS: str = "requirements"
    IS_RISKY: str = "is_risky"


# =============================================================================
# LLM SERVICE CLASS
# =============================================================================

class LLMService:
    """
    Service class for interacting with DeepSeek LLM API.

    Uses OpenAI-compatible SDK configured for DeepSeek endpoints.
    Provides methods for analyzing game boosting requirements.

    Usage:
        llm_service = LLMService()
        result = await llm_service.analyze_requirement("王者荣耀，钻石上王者，预算500元")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        """
        Initialize LLM service with DeepSeek configuration.

        Args:
            api_key: DeepSeek API key. Defaults to settings.DEEPSEEK_API_KEY.
            base_url: API base URL. Defaults to DeepSeek's endpoint.
            model: Model identifier. Defaults to deepseek-chat.
        """
        self._api_key = api_key or settings.DEEPSEEK_API_KEY
        self._base_url = base_url or DEEPSEEK_BASE_URL
        self._model = model or DEEPSEEK_MODEL

        # Initialize async OpenAI client configured for DeepSeek
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    async def analyze_requirement(self, text: str) -> dict[str, Any]:
        """
        Analyze user's game boosting requirement description.

        Extracts structured information from natural language input including:
        - game_name: Name of the game
        - current_rank: User's current rank/tier
        - target_rank: Desired rank/tier
        - price: Budget amount (numeric)
        - role: Game role/position
        - server: Game server/region
        - is_risky: Flag for prohibited content detection

        Args:
            text: User's requirement description in Chinese.

        Returns:
            Dictionary containing extracted fields.

        Raises:
            HTTPException: If AI parsing fails or returns invalid JSON.
        """
        if not text or not text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="需求描述不能为空",
            )

        user_message = REQUIREMENT_ANALYSIS_USER_TEMPLATE.format(text=text.strip())

        try:
            response: ChatCompletion = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": REQUIREMENT_ANALYSIS_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_message,
                    },
                ],
                temperature=0.1,  # Low temperature for consistent structured output
                max_tokens=1024,
                response_format={"type": "json_object"},  # Request JSON response
            )

            # Extract content from response
            content = response.choices[0].message.content

            if not content:
                logger.error("DeepSeek returned empty content")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="AI解析失败，请重试",
                )

            # Parse JSON response
            result = self._parse_json_response(content)

            # Normalize and validate result
            normalized_result = self._normalize_analysis_result(result)

            logger.info(
                f"Successfully analyzed requirement: game={normalized_result.get('game_name')}, "
                f"is_risky={normalized_result.get('is_risky', False)}"
            )

            return normalized_result

        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI解析失败，请重试",
            ) from e
        except Exception as e:
            logger.error(f"DeepSeek API call failed: {type(e).__name__}: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI解析失败，请重试",
            ) from e

    def _parse_json_response(self, content: str) -> dict[str, Any]:
        """
        Parse JSON from AI response content.

        Handles potential formatting issues like markdown code blocks.

        Args:
            content: Raw response content from AI.

        Returns:
            Parsed dictionary.

        Raises:
            json.JSONDecodeError: If content is not valid JSON.
        """
        # Clean up potential markdown code block formatting
        cleaned_content = content.strip()

        # Remove markdown JSON code block if present
        if cleaned_content.startswith("```json"):
            cleaned_content = cleaned_content[7:]
        elif cleaned_content.startswith("```"):
            cleaned_content = cleaned_content[3:]

        if cleaned_content.endswith("```"):
            cleaned_content = cleaned_content[:-3]

        cleaned_content = cleaned_content.strip()

        return json.loads(cleaned_content)

    def _normalize_analysis_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize and validate the analysis result.

        Ensures all expected fields exist and have appropriate types.

        Args:
            result: Raw parsed result from AI.

        Returns:
            Normalized result dictionary.
        """
        normalized: dict[str, Any] = {
            AnalysisResultKeys.GAME_NAME: self._get_string_value(
                result, AnalysisResultKeys.GAME_NAME
            ),
            AnalysisResultKeys.CURRENT_RANK: self._get_string_value(
                result, AnalysisResultKeys.CURRENT_RANK
            ),
            AnalysisResultKeys.TARGET_RANK: self._get_string_value(
                result, AnalysisResultKeys.TARGET_RANK
            ),
            AnalysisResultKeys.PRICE: self._get_numeric_value(
                result, AnalysisResultKeys.PRICE
            ),
            AnalysisResultKeys.ROLE: self._get_string_value(
                result, AnalysisResultKeys.ROLE
            ),
            AnalysisResultKeys.SERVER: self._get_string_value(
                result, AnalysisResultKeys.SERVER
            ),
            AnalysisResultKeys.SERVICE_TYPE: self._get_string_value(
                result, AnalysisResultKeys.SERVICE_TYPE
            ),
            AnalysisResultKeys.REQUIREMENTS: self._get_list_value(
                result, AnalysisResultKeys.REQUIREMENTS
            ),
            AnalysisResultKeys.IS_RISKY: self._get_boolean_value(
                result, AnalysisResultKeys.IS_RISKY
            ),
        }

        return normalized

    @staticmethod
    def _get_string_value(data: dict[str, Any], key: str) -> str | None:
        """Extract string value from dict, returning None if not present or empty."""
        value = data.get(key)
        if value is None:
            return None
        str_value = str(value).strip()
        return str_value if str_value else None

    @staticmethod
    def _get_numeric_value(data: dict[str, Any], key: str) -> float | None:
        """Extract numeric value from dict, handling string representations."""
        value = data.get(key)
        if value is None:
            return None

        try:
            # Handle string with currency symbols or units
            if isinstance(value, str):
                # Remove common currency symbols and units
                cleaned = value.replace("¥", "").replace("元", "").replace(",", "").strip()
                return float(cleaned) if cleaned else None
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _get_boolean_value(data: dict[str, Any], key: str) -> bool:
        """Extract boolean value from dict, defaulting to False."""
        value = data.get(key)
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "是")
        return bool(value)

    @staticmethod
    def _get_list_value(data: dict[str, Any], key: str) -> list[str]:
        """Extract a normalized list of strings from dict."""
        value = data.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                text = str(item).strip()
                if text:
                    normalized.append(text)
            return normalized
        text = str(value).strip()
        return [text] if text else []

    async def health_check(self) -> bool:
        """
        Check if DeepSeek API is accessible.

        Returns:
            True if API is accessible, False otherwise.
        """
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "user", "content": "ping"},
                ],
                max_tokens=10,
            )
            return response.choices[0].message.content is not None
        except Exception as e:
            logger.warning(f"DeepSeek health check failed: {e}")
            return False


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

def get_llm_service() -> LLMService:
    """
    FastAPI dependency injection function for LLMService.

    Usage:
        @router.post("/analyze")
        async def analyze(
            text: str,
            llm_service: LLMService = Depends(get_llm_service)
        ):
            return await llm_service.analyze_requirement(text)
    """
    return LLMService()
