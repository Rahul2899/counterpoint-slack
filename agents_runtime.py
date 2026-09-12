import asyncio
import json

from agents import Agent, Runner


async def _explain_scouts(scouts: dict) -> dict[str, str]:
    names = ("code", "test", "history")
    jobs = []
    for name in names:
        agent = Agent(
            name=f"{name.title()} Scout",
            instructions=(
                "Explain the supplied deterministic evidence in one sentence. "
                "Do not add facts, evidence, confidence, or a verdict."
            ),
        )
        jobs.append(Runner.run(agent, json.dumps(scouts[name])))
    results = await asyncio.gather(*jobs)
    return {
        name: result.final_output
        for name, result in zip(names, results, strict=True)
    }


def explain_scouts(scouts: dict) -> dict[str, str]:
    return asyncio.run(_explain_scouts(scouts))
