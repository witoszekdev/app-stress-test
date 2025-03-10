import asyncio
import uuid
import os
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport

app_mutation_str = """
mutation AppCreate($input: AppInput!) {
  appCreate(input: $input) {
    authToken
    app {
      ...App
      __typename
    }
    errors {
      ...AppError
      __typename
    }
    __typename
  }
}

fragment App on App {
  id
  name
  created
  isActive
  type
  homepageUrl
  appUrl
  manifestUrl
  configurationUrl
  supportUrl
  version
  accessToken
  brand {
    logo {
      default(format: WEBP, size: 64)
      __typename
    }
    __typename
  }
  privateMetadata {
    key
    value
    __typename
  }
  metadata {
    key
    value
    __typename
  }
  tokens {
    authToken
    id
    name
    __typename
  }
  webhooks {
    ...Webhook
    __typename
  }
  __typename
}

fragment Webhook on Webhook {
  id
  name
  isActive
  app {
    id
    name
    __typename
  }
  __typename
}

fragment AppError on AppError {
  field
  message
  code
  permissions
  __typename
}
"""

webhook_mutation_str = """
mutation WebhookCreate($input: WebhookCreateInput!) {
  webhookCreate(input: $input) {
    errors {
      ...WebhookError
      __typename
    }
    webhook {
      ...WebhookDetails
      __typename
    }
    __typename
  }
}

fragment WebhookError on WebhookError {
  code
  field
  message
  __typename
}

fragment WebhookDetails on Webhook {
  ...Webhook
  syncEvents {
    eventType
    __typename
  }
  asyncEvents {
    eventType
    __typename
  }
  secretKey
  targetUrl
  subscriptionQuery
  customHeaders
  __typename
}

fragment Webhook on Webhook {
  id
  name
  isActive
  app {
    id
    name
    __typename
  }
  __typename
}
"""

webhook_query = """subscription {\n  event {\n    ... on OrderCancelled {\n      order {\n        id\n      }\n    }\n  }\n}\n"""

# Parse the mutation string
app_mutation = gql(app_mutation_str)
webhook_mutation = gql(webhook_mutation_str)

# Semaphore to control request rate
rate_limiter = asyncio.Semaphore(2)


async def execute_app_mutation(client) -> str:
    # Create unique app name for each request
    unique_id = str(uuid.uuid4())
    variables = {
        "input": {
            "name": f"Test local app - {unique_id}",
            "permissions": ["MANAGE_ORDERS"],
        }
    }

    async with client as session:
        result = await session.execute(app_mutation, variable_values=variables)
        return result["appCreate"]["app"]["id"]


async def execute_webhook_mutation(client, app_id):
    variables = {
        "input": {
            "name": f"Test app webhook ${app_id}",
            "targetUrl": "https://example.com",
            "asyncEvents": ["ORDER_CREATED"],
            "syncEvents": [],
            "isActive": True,
            "app": app_id,
            "query": webhook_query,
            "secretKey": "",
        }
    }

    async with client as session:
        result = await session.execute(webhook_mutation, variable_values=variables)
        return result["webhookCreate"]["webhook"]["id"]


async def execute_mutations(saleorApiUrl, token, index):
    headers = {"Authorization": f"Bearer {token}"}
    transport = AIOHTTPTransport(url=saleorApiUrl, headers=headers)
    client = Client(transport=transport, fetch_schema_from_transport=False)

    # Use rate limiter to control request frequency
    try:
        async with rate_limiter:
            app_id = await execute_app_mutation(client)
            print(f"Created app no. {index + 1} (id: {app_id})")

            webhook_id = await execute_webhook_mutation(client, app_id)
            print(f"Created webhook for app no. {index + 1} (id: {webhook_id})")

            # Wait for 1 second before releasing the semaphore
            await asyncio.sleep(1)
    except Exception as e:
        return e


async def main():
    # Replace with your GraphQL endpoint
    url = os.environ.get("SALEOR_GRAPHQL_URL", "http://localhost:8000/graphql/")
    token = os.environ.get("AUTH_TOKEN")

    if not token:
        raise Exception("Please provide an AUTH_TOKEN environment variable")

    # Execute 100 mutations concurrently
    tasks = [execute_mutations(url, token, i) for i in range(100)]
    results = await asyncio.gather(*tasks)

    # Count successful queries
    successful = sum(1 for r in results if r is None)
    print(f"\nCompleted {successful} out of 100 queries successfully")


if __name__ == "__main__":
    asyncio.run(main())
