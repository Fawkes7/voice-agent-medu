import asyncio
import json
import logging

from openai import APIError, AsyncOpenAI, BadRequestError

from app.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)

LLM_CALL_TIMEOUT_SECONDS = 60.0
# 会话结束时 final_evaluator 单次整评（报告）体量较大，单独放宽上限
FINAL_EVALUATION_LLM_TIMEOUT_SECONDS = 120.0

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.llm_api_key:
            raise RuntimeError(
                f"LLM_PROVIDER={settings.LLM_PROVIDER} 但对应的 API key 未配置"
            )
        _client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            max_retries=1,
        )
    return _client


async def _create_completion(kwargs: dict):
    client = _get_client()
    try:
        return await client.chat.completions.create(**kwargs)
    except BadRequestError as exc:
        # Reasoning models (gpt-5, o-series) reject any non-default temperature.
        if exc.param != "temperature" or "temperature" not in kwargs:
            raise
        logger.info("Model %s rejected temperature; retrying without it", kwargs["model"])
        kwargs = {k: v for k, v in kwargs.items() if k != "temperature"}
        return await client.chat.completions.create(**kwargs)


async def call_llm(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.7,
    response_format: str | None = None,
    timeout: float = LLM_CALL_TIMEOUT_SECONDS,
) -> str:
    """Call the configured LLM provider (OpenAI-compatible chat completions), with timeout."""
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    kwargs = {
        "model": settings.llm_model,
        "messages": full_messages,
    }
    # Gemini 3 is tuned for the default temperature (1.0); lower values can cause looping.
    if not settings.llm_model.startswith("gemini-3"):
        kwargs["temperature"] = temperature

    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = await asyncio.wait_for(_create_completion(kwargs), timeout=timeout)
    except asyncio.TimeoutError as exc:
        logger.warning("LLM call timed out after %.1fs", timeout)
        raise RuntimeError(f"LLM 调用超时 (>{int(timeout)}s)，请稍后重试") from exc
    except APIError as exc:
        raise RuntimeError(
            f"LLM API error ({settings.LLM_PROVIDER}/{settings.llm_model}): {exc}"
        ) from exc

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RuntimeError(
            f"LLM ({settings.LLM_PROVIDER}/{settings.llm_model}) 返回空内容"
        )
    return content


def parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)
