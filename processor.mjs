// ESM format for Artillery processor
export function beforeScenario(context, ee, next) {
  // Your GraphQL query
  context.vars.query = `
    {
      apps(first: 100) {
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
  `;

  return next();
}

export function afterResponse(requestParams, response, context, ee, next) {
  if (response.statusCode >= 300) {
    console.error(`Error ${response.statusCode} for ${requestParams.url}`);
    console.log("Response body:", response.body);
  }
  return next();
}

// Export all functions as a default object
export default {
  beforeScenario,
  afterResponse
};
