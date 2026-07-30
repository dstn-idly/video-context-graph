"""The Strands agent that reasons over the video context graph."""
from strands import Agent

from . import config
from .tools import ALL_TOOLS

SYSTEM_PROMPT = """You are Puffer AI, a video growth and context agent.

You have a knowledge graph built from indexed videos, plus the ability to search
and re-watch the videos themselves.

How to work:
1. Call graph_overview first if you don't know what's in the graph.
2. Prefer query_context_graph for structural questions (who appears with whom,
   which scenes share a topic, connections across videos).
3. Use search_video_moments when the question is about a specific visual or
   spoken moment.
4. Use describe_video only when the graph and search don't have the detail.
   It takes a Scene's tl_video_id, which for a long VOD is the segment id.
5. Call timestamp_link for every moment you cite so the user can jump to it.
6. Use rank_viral_moments for clip discovery across full-length VODs.

Always cite the video and timestamp for any claim about video content.
When recommending a clip, explain the observable hook, emotional beat, relevant
people/topics, why it can stand alone, and a suggested title. Treat the viral
score as a prioritization signal—not a guarantee of views. Prefer moments that
connect to recurring people, topics, callbacks, or community lore in the graph.
If the graph is empty, say so plainly instead of guessing.
"""


def build_agent() -> Agent:
    """Create the agent using whichever model backend .env selects."""
    if config.AGENT_BACKEND == "bedrock":
        from strands.models.bedrock import BedrockModel

        model = BedrockModel(
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            region_name=config.AWS_REGION,
        )
    else:
        from strands.models.openai import OpenAIModel

        model = OpenAIModel(
            client_args={"api_key": config.require("OPENAI_API_KEY")},
            model_id=config.OPENAI_MODEL,
        )

    return Agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)


if __name__ == "__main__":
    import sys

    agent = build_agent()
    question = " ".join(sys.argv[1:]) or "What videos are in the graph, and what are they about?"
    agent(question)
