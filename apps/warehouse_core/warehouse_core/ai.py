"""Single entry point for future ERP AI features; never bypass the spending guard.

Intentionally not whitelisted as a browser API. Business features must perform
their own record/role authorization before calling this function.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class AIUnavailable(Exception):
    pass


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
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as error:
        raise AIUnavailable('AI is unavailable or blocked by the spending limit.') from error
