"""LLM client: Azure OpenAI only when keys are set, else a loud error.

No silent fallback to a canned answer: callers map AzureError to HTTP 502.
The offline analytics path never calls this module at all.
"""

from __future__ import annotations

import json

import httpx
from urllib.parse import urlparse

from . import config
from .pii import mask_pii


class AzureError(RuntimeError):
    """Azure call failed or LLM not configured. API turns this into a 502."""


class AzureOpenAIClient:
    """Minimal Azure OpenAI chat client. Only used when the key is set."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def _post(self, messages: list[dict], temperature: float = 0) -> str:
        endpoint = config.azure_endpoint()
        parsed = urlparse(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname
                or not parsed.hostname.endswith(".openai.azure.com")):
            raise AzureError(
                "AZURE_OPENAI_ENDPOINT must be an https:// URL on *.openai.azure.com")
        url = (
            f"{endpoint.rstrip('/')}/openai/deployments/"
            f"{config.azure_deployment()}/chat/completions?api-version=2024-02-01"
        )
        try:
            with httpx.Client(transport=self._transport, timeout=30) as client:
                resp = client.post(
                    url, headers={"api-key": config.azure_api_key()},
                    json={"messages": messages, "temperature": temperature},
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - any Azure failure is a loud 502
            raise AzureError(f"Azure OpenAI call failed: {type(exc).__name__}") from exc

    def generate_sql(self, question: str, schema_context: str) -> str:
        """Generate one SELECT for ``question`` given the semantic-layer context.

        Returns the raw SQL string; the caller still runs it through the
        same sqlglot validation as the offline templates.
        """
        text = self._post([
            {"role": "system",
             "content": "Generate exactly one read-only SQL SELECT for the "
                        "question, using only the tables and columns below. "
                        "Return ONLY the SQL, no explanation.\n\n"
                        f"Schema:\n{schema_context}"},
            {"role": "user", "content": mask_pii(question)},
        ])
        return text.strip().strip("`").strip()

    def verify_routing(self, question: str, domain: str, confidence: float) -> bool:
        """LLM verification for a medium-confidence routing decision."""
        text = self._post([
            {"role": "system",
             "content": "You route user questions to one domain: analytics "
                        "(billing data questions), dev (tickets/code work), "
                        "disputes (charge disputes), or unknown. Reply with "
                        "exactly one JSON object: {\"domain\": \"...\"}."},
            {"role": "user", "content": mask_pii(question)},
        ])
        try:
            return json.loads(text.strip().strip("`"))["domain"] == domain
        except (ValueError, KeyError, TypeError):
            raise AzureError("Azure OpenAI returned an unparseable routing verdict")


def get_client() -> AzureOpenAIClient:
    """Return the Azure client, or raise if no key is configured."""
    if config.llm_provider() != "azure":
        raise AzureError("LLM required but AZURE_OPENAI_API_KEY is not set")
    return AzureOpenAIClient()
