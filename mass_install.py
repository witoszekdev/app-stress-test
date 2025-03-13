import asyncio
import uuid
import os
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport

# Define the GraphQL mutation
mutation_str = """
mutation AppInstall($input: AppInstallInput!) {
  appInstall(input: $input) {
    appInstallation {
      id
      status
      appName
      manifestUrl
      __typename
    }
    errors {
      ...AppError
      __typename
    }
    __typename
  }
}

fragment AppError on AppError {
  field
  message
  code
  permissions
  __typename
}
"""

# Parse the mutation string
mutation = gql(mutation_str)

# Semaphore to control request rate
rate_limiter = asyncio.Semaphore(1)


async def execute_mutation(url, token, index):
    # Create unique app name for each request
    unique_id = str(uuid.uuid4())
    variables = {
        "input": {
            "appName": f"Test app - {unique_id}",
            "manifestUrl": "https://9d311d3d.saleor-app-hono-pages-template.pages.dev/api/manifest",
            "permissions": ["MANAGE_ORDERS"],
        }
    }

    try:
        # Create a new transport and client for each request
        headers = {"Authorization": f"Bearer {token}"}
        transport = AIOHTTPTransport(url=url, headers=headers)
        client = Client(transport=transport, fetch_schema_from_transport=False)

        # Use rate limiter to control request frequency
        async with rate_limiter:
            print(f"Starting query {index+1}")
            async with client as session:
                result = await session.execute(mutation, variable_values=variables)
                print(f"Query {index+1} completed successfully")

                # Wait for 1 second before releasing the semaphore
                await asyncio.sleep(1)

                return result
    except Exception as e:
        print(f"Query {index+1} failed: {str(e)}")
        return None


async def main():
    # Replace with your GraphQL endpoint
    url = os.environ.get("SALEOR_GRAPHQL_URL", "http://localhost:8000/graphql/")
    token = os.environ.get("AUTH_TOKEN")

    if not token:
        raise Exception("Please provide an AUTH_TOKEN environment variable")

    # Execute 100 mutations concurrently
    tasks = [execute_mutation(url, token, i) for i in range(30)]
    results = await asyncio.gather(*tasks)

    # Count successful queries
    successful = sum(1 for r in results if r is not None)
    print(f"\nCompleted {successful} out of 100 queries successfully")


if __name__ == "__main__":
    asyncio.run(main())
