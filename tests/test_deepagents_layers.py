"""Local tests for the DeepAgents replacement boundaries."""

import backend.app as app
from backend.policy import check_output, check_publish, check_request, preflight
from backend.signal_tools import create_tools


def test_policy_blocks_unsupported_capabilities():
    assert not preflight("Write a blog post").allowed
    assert not preflight("Tell me a recipe").allowed
    assert preflight("Create a LinkedIn post").allowed


def test_policy_requires_confirmed_request_shape():
    assert not check_request({"product_name": "Camera", "product_facts": ["one"]}).allowed
    assert check_request(
        {
            "product_name": "Camera",
            "product_facts": ["one", "two", "three"],
        }
    ).allowed


def test_policy_rejects_unsafe_output_and_publishing():
    request = {
        "product_name": "Camera",
        "product_facts": ["one", "two", "three"],
    }
    assert not check_output("Unsupported draft", request, grounded=False).allowed
    assert not check_publish().allowed


def test_typed_signal_tools_expose_expected_surface():
    tools = create_tools(
        create_capability=lambda request: request["product_name"],
        edit_capability=lambda request, draft, instruction: draft,
        validate_capability=lambda request, draft: "passed",
    )
    assert {
        tool.name for tool in tools
    } == {
        "create_linkedin_post",
        "edit_linkedin_post",
        "validate_linkedin_draft",
        "retrieve_confirmed_facts",
        "publish_linkedin_post",
    }


def test_graph_has_checkpointed_state():
    assert app.GRAPH.checkpointer is not None
