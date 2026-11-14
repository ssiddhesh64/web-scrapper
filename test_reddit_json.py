import asyncio

from reddrix import Reddrix


async def main():

    async with Reddrix(
        mode="standard",
        subreddit_urls=[
            "https://www.reddit.com/r/MachineLearning"
        ],
        sort="new",
        include_comments=True,
        max_results=100,
        verbose=True
    ) as reddrix:

        async for item in reddrix.get_items():

            print("\nPost type:", item["submissionType"])
            print("URL:", item["submissionUrl"])

            print("Full item:")
            print(item)


if __name__ == "__main__":
    asyncio.run(main())