"""Single entry point for future ERP AI features; never bypass the spending guard.

Intentionally not whitelisted as a browser API. Business features must perform
their own record/role authorization before calling this function.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class AIUnavailable(Exception):
    def __init__(self, message="", *, incident=None):
        super().__init__(message)
        self.incident = incident


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def generate_text(prompt: str, max_output_tokens: int = 512) -> str:
    request = Request('http://ai-gateway:8091/generate', method='POST',
                      data=json.dumps({'prompt': prompt, 'max_output_tokens': max_output_tokens}).encode(),
                      headers={'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=100) as response:
            return json.load(response)['text']
    except HTTPError as error:
        incident = 'gateway'
        if error.code == 403:
            try:
                reason = json.loads(error.read(4096)).get('error')
                incident = 'provider' if reason in ('provider_connection_unavailable', 'provider_outcome_uncertain_ai_disabled') else None
            except (OSError, ValueError, AttributeError):
                pass
        raise AIUnavailable('AI is unavailable or blocked by the spending limit.', incident=incident) from error
    except (URLError, TimeoutError, ValueError, KeyError, TypeError) as error:
        raise AIUnavailable('AI gateway unavailable.', incident='gateway') from error
