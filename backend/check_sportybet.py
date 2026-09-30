"""
Can this server reach SportyBet? (Some hosts' addresses are refused.) Run on
a new server once the API is up:

    cd ~/betiq/deploy/vm && sudo docker compose exec api python check_sportybet.py
"""

import asyncio

import sportybet


async def main() -> None:
    try:
        data = await sportybet._request(sportybet.shared_session(), "GET", "/factsCenter/liveOrPrematchEvents",
                                        params={"sportId": "sr:sport:1", "_t": sportybet._now_ms()})
        found: list = []
        sportybet._collect_events(data.get("data"), found)
        print(f"SportyBet OK: {len(found)} football events listed")
    except Exception as e:
        print(f"SportyBet NOT reachable from here: {e}")


if __name__ == "__main__":
    asyncio.run(main())
