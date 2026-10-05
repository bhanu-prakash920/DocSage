from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from docsage.config import Settings, get_settings, is_placeholder, update_settings
from docsage.errors import BudgetExceeded, ProviderError
from docsage.providers.base import ImagePart, Message, UsageMeter, metering, record_usage, strict_schema, user
from docsage.providers.pricing import cost_of, price_for


def test_placeholder_keys_are_ignored():
    assert is_placeholder("your_openai_api_key_here")
    assert is_placeholder("...")
    assert not is_placeholder("sk-ant-api03-real")
    s = Settings(openai_api_key="your_openai_api_key_here", anthropic_api_key="sk-ant-real")
    assert not s.has_key("openai") and s.has_key("anthropic")


def test_parser_alias():
    assert Settings(parser="pymupdf4llm").parser == "pymupdf"
    assert Settings(parser="LlamaParse").parser == "llamaparse"


def test_update_settings_validates_and_persists(env):
    update_settings({"top_k": 9, "chunking": "agentic"})
    assert get_settings().top_k == 9
    saved = json.loads((env.data_dir / "settings.json").read_text())
    assert saved == {"top_k": 9, "chunking": "agentic"}
    with pytest.raises(ValueError):
        update_settings({"top_k": 500})
    with pytest.raises(ValueError):
        update_settings({"anthropic_api_key": "x"})


def test_pricing_and_meter_budget():
    assert price_for("claude-opus-5-5") == (4.0, 20.0)
    assert price_for("gpt-5.4-mini-2026-03-17") == price_for("gpt-5.4-mini")
    assert cost_of("claude-haiku-4-5", 1_000_000, 0) == pytest.approx(1.0)
    meter = UsageMeter(budget_usd=0.01)
    with metering(meter):
        record_usage("claude-opus-5-5", 1000, 100)
        with pytest.raises(BudgetExceeded):
            record_usage("claude-opus-5-5", 10_000, 1_000)
    assert meter.calls == 2


def test_strict_schema_requires_all_fields():
    from pydantic import BaseModel

    class Inner(BaseModel):
        a: int
        b: str = "x"

    class Outer(BaseModel):
        items: list[Inner]

    schema = strict_schema(Outer)
    inner = schema["$defs"]["Inner"]
    assert inner["additionalProperties"] is False and set(inner["required"]) == {"a", "b"}


def _anthropic_response(text="hi", stop="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0
        ),
        stop_reason=stop,
        stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None,
        model="claude-opus-5-5",
    )


def test_anthropic_adapter_request_shape():
    from docsage.providers.anthropic_provider import AnthropicChat

    captured = {}
    opus = AnthropicChat("claude-opus-5-5", "sk-test", effort="medium")
    opus.client = SimpleNamespace(
        beta=SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **kw: captured.update(kw) or _anthropic_response('{"a": 1}')
            )
        )
    )
    meter = UsageMeter()
    with metering(meter):
        out = opus.generate(
            [user(ImagePart(b"png", "image/png"), "describe")], system="sys", json_schema={"type": "object"}
        )
    assert out == '{"a": 1}'
    assert captured["betas"] == ["server-side-fallback-2026-07-01"] and captured["fallbacks"] == "default"
    assert captured["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    blocks = captured["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert "thinking" not in captured and "temperature" not in captured
    assert meter.calls == 1 and meter.cost_usd > 0

    haiku = AnthropicChat("claude-haiku-4-5", "sk-test")
    haiku.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kw: captured.clear() or captured.update(kw) or _anthropic_response()
        )
    )
    haiku.generate([Message("user", "hello")])
    assert "output_config" not in captured and "betas" not in captured


def test_anthropic_refusal_raises():
    from docsage.providers.anthropic_provider import AnthropicChat

    chat = AnthropicChat("claude-haiku-4-5", "sk-test")
    chat.client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: _anthropic_response("", "refusal"))
    )
    with pytest.raises(ProviderError, match="declined"):
        chat.generate([user("x")])


def test_openai_and_google_message_conversion():
    from docsage.providers.google_provider import _contents
    from docsage.providers.openai_provider import _messages

    msgs = [Message("user", [ImagePart(b"\x89PNG"), "what is this"]), Message("assistant", "a chart")]
    oa = _messages(msgs, "sys")
    assert oa[0] == {"role": "system", "content": "sys"}
    assert oa[1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    g = _contents(msgs)
    assert [c.role for c in g] == ["user", "model"] and len(g[0].parts) == 2


def test_offline_factory_and_status(env):
    from docsage.providers.factory import get_chat_model, get_embedding_model, provider_status

    assert get_chat_model().generative is False
    emb = get_embedding_model()
    vec = emb.embed_query("storage revenue")
    assert len(vec) == 512 and abs(sum(v * v for v in vec) - 1) < 1e-4
    status = provider_status()
    assert status["llm_provider"] == "offline" and status["error"] is None


def test_json_repair_retry():
    from pydantic import BaseModel

    from tests.fakes import FakeChat

    class Out(BaseModel):
        x: int

    chat = FakeChat()
    replies = iter(["not json", '{"x": 3}'])
    chat.generate = lambda *a, **k: next(replies)  # type: ignore[method-assign]
    assert chat.generate_json([user("q")], Out).x == 3
