from predictionbot.ai import NvidiaAiReviewer
from predictionbot.http import JsonHttpClient


def test_ai_reviewer_skips_without_api_key() -> None:
    reviewer = NvidiaAiReviewer(
        http=JsonHttpClient("test-agent"),
        api_key=None,
        model="meta/llama-3.1-8b-instruct",
        base_url="https://integrate.api.nvidia.com/v1",
    )

    review = reviewer.review_predictions([])

    assert not review.enabled
    assert "NVIDIA API key" in review.text
