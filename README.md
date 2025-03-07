# Saleor App list load test

Testing Saleor app list load time with 100 apps.

This repo has app that generates random identifier in `/api/manifest` endpoint using Cloudflare Workers.

Then we install it using python script `mass_install.py` that installs the same app 100 times.

After that we can measure our planned query using Artillery.

## Getting started

### Install dependencies

```shell
pnpm install
```

### Deploy Cloudflare Worker

```shell
pnpm run deploy
```

### Install 100 apps

```shell
poetry install
eval $(poetry env activate)
SALEOR_GRAPHQL_URL=<saleorApiUrl> AUTH_TOKEN=<user_token> python3 mass_install
```

### Run performance test

```shell
SALEOR_GRAPHQL_URL=<saleorApiUrl> AUTH_TOKEN=<user_token> pnpm run run:performance-test
```
