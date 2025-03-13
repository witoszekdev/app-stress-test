import asyncio
import os
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
import time


# Query for sequential pagination (only pageInfo is needed to extract endCursor)
SEQUENTIAL_QUERY = """
query Apps($cursor: String) {
  apps(first: 10, after: $cursor) {
    pageInfo {
      endCursor
    }
  }
}
"""

# Query for fetching detailed app data (edges with id and name)
PARALLEL_QUERY = """
query Apps($cursor: String) {
  apps(first: 10, after: $cursor) {
    edges {
        node {
            id
            name
            created
            isActive
            type
            brand {
            logo {
                default(format: WEBP, size: 24)
            }
            }
            webhooks {
            failedDelivers: eventDeliveries(
                first: 1
                filter: {status: FAILED}
                sortBy: {field: CREATED_AT, direction: DESC}
            ) {
                edges {
                node {
                    id
                    createdAt
                    attempts(first: 1, sortBy: {field: CREATED_AT, direction: DESC}) {
                    edges {
                        node {
                        id
                        status
                        createdAt
                        }
                    }
                    }
                }
                }
            }
            pendingDelivers: eventDeliveries(
                first: 6
                filter: {status: PENDING}
                sortBy: {field: CREATED_AT, direction: DESC}
            ) {
                edges {
                node {
                    id
                    attempts(first: 6, sortBy: {field: CREATED_AT, direction: DESC}) {
                    edges {
                        node {
                        id
                        status
                        createdAt
                        }
                    }
                    }
                }
                }
            }
            }
        }
    }
  }
}
"""

fetch_cursors_query = gql(SEQUENTIAL_QUERY)
fetch_details_query = gql(PARALLEL_QUERY)


async def fetch_all_cursors(session):
    """
    This function fetches the endCursor from each page.
    It begins with a null cursor and continues until the response's endCursor is None.
    The list 'cursors' will include the cursor for the first page (None) and every non-null cursor returned.
    """
    cursors = [None]  # Start with None for the first page
    current_cursor = None

    while True:
        variables = {"cursor": current_cursor}
        result = await session.execute(fetch_cursors_query, variable_values=variables)
        end_cursor = result["apps"]["pageInfo"]["endCursor"]

        if end_cursor:
            cursors.append(end_cursor)
            current_cursor = end_cursor
        else:
            break
    return cursors


async def fetch_page_data(session, cursor, semaphore):
    """
    This function uses a semaphore to ensure that only a limited number of requests are running concurrently.
    It sends a GraphQL query (the PARALLEL_QUERY) that retrieves detailed data for a given cursor.
    """
    async with semaphore:
        variables = {"cursor": cursor}
        result = await session.execute(fetch_details_query, variable_values=variables)
        return result["apps"]["edges"]


async def main():
    script_start_time = time.perf_counter()

    url = os.environ.get("SALEOR_GRAPHQL_URL", "http://localhost:8000/graphql/")
    token = os.environ.get("AUTH_TOKEN")

    headers = {"Authorization": f"Bearer {token}"}
    transport = AIOHTTPTransport(url=url, headers=headers)
    client = Client(transport=transport, fetch_schema_from_transport=False)

    async with client as session:
        # Step 1: Sequentially collect all page cursors.
        cursor_fetch_start = time.perf_counter()
        cursors = await fetch_all_cursors(session)
        cursor_fetch_end = time.perf_counter()
        cursor_fetch_time = cursor_fetch_end - cursor_fetch_start

        print(f"Collected {len(cursors)} cursors in {cursor_fetch_time:.2f} seconds")

        # Step 2: Launch concurrent requests for detailed data, limiting to 10 at a time.
        data_fetch_start = time.perf_counter()
        semaphore = asyncio.Semaphore(10)
        tasks = [fetch_page_data(session, cursor, semaphore) for cursor in cursors]
        pages = await asyncio.gather(*tasks)
        data_fetch_end = time.perf_counter()
        data_fetch_time = data_fetch_end - data_fetch_start

        # Aggregate all the app nodes from each page.
        apps = []
        for page in pages:
            for edge in page:
                node = edge.get("node", {})
                apps.append(node)

        print(f"Fetched {len(apps)} apps in {data_fetch_time:.2f} seconds")

        script_end_time = time.perf_counter()
        total_execution_time = script_end_time - script_start_time

        print(f"\nTiming Summary:")
        print(f"Cursor fetching phase: {cursor_fetch_time:.2f} seconds")
        print(f"Data fetching phase: {data_fetch_time:.2f} seconds")
        print(f"Total script execution: {total_execution_time:.2f} seconds")


if __name__ == "__main__":
    asyncio.run(main())
