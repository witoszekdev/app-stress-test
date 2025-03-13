import asyncio
import os
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
import time
import csv
import statistics
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import random
import logging
import aiohttp
from gql.transport.exceptions import TransportQueryError

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Disable verbose logging from libraries
logging.getLogger("gql").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("graphql").setLevel(logging.WARNING)

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
  plugins(first: 100) {
    edges {
      node {
        id
        name
        globalConfiguration {
          active
        }
        channelConfigurations {
          active
        }
      }
    }
  }
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

# Number of times to run the benchmark
NUM_RUNS = 50

# Retry configuration
MAX_RETRIES = 5
BASE_BACKOFF_TIME = 20  # Base time in seconds
MAX_BACKOFF_TIME = 120  # Maximum backoff time in seconds
JITTER_FACTOR = 0.2  # Add randomness to backoff time

fetch_cursors_query = gql(SEQUENTIAL_QUERY)
fetch_details_query = gql(PARALLEL_QUERY)


class RateLimitException(Exception):
    """Custom exception to indicate rate limiting occurred during execution"""

    pass


def is_rate_limit_error(error):
    """Check if an error is a rate limit error (429 Too Many Requests)"""
    # Check different error formats
    if isinstance(error, TransportQueryError):
        # Check for GraphQL error format
        if hasattr(error, "errors") and error.errors:
            for err in error.errors:
                if isinstance(err, dict):
                    message = err.get("message", "")
                    if (
                        "429" in message
                        or "too many requests" in message.lower()
                        or "throttled" in message.lower()
                    ):
                        return True

        # Check for underlying HTTP error
        if hasattr(error, "original_error"):
            original = error.original_error
            if hasattr(original, "status") and original.status == 429:
                return True
            if hasattr(original, "message") and (
                "429" in original.message
                or "too many requests" in original.message.lower()
            ):
                return True

    # Check for aiohttp ClientResponseError
    if isinstance(error, aiohttp.ClientResponseError) and error.status == 429:
        return True

    # Check for string representation containing 429
    error_str = str(error).lower()
    return (
        "429" in error_str
        or "too many requests" in error_str
        or "throttled" in error_str
    )


async def execute_with_retry(session, query, variables):
    """
    Execute a GraphQL query with retry logic for rate limiting.
    Raises RateLimitException if rate limiting occurred during execution.
    """
    retries = 0
    rate_limited = False

    while True:
        try:
            result = await session.execute(query, variable_values=variables)
            # If we had rate limiting but eventually succeeded, signal this to the caller
            if rate_limited:
                raise RateLimitException("Rate limiting occurred during execution")
            return result
        except Exception as e:
            if is_rate_limit_error(e) and retries < MAX_RETRIES:
                retries += 1
                rate_limited = True
                # Calculate backoff time with exponential increase and jitter
                backoff_time = min(
                    BASE_BACKOFF_TIME * (2 ** (retries - 1)), MAX_BACKOFF_TIME
                )
                # Add jitter (randomness) to avoid thundering herd problem
                jitter = backoff_time * JITTER_FACTOR * random.random()
                wait_time = backoff_time + jitter

                logger.warning(
                    f"Rate limited (429). Retry {retries}/{MAX_RETRIES} after {wait_time:.2f}s"
                )
                await asyncio.sleep(wait_time)
            else:
                # Either not a rate limit error or we've exceeded max retries
                if retries >= MAX_RETRIES:
                    logger.error(
                        f"GraphQL query failed after {retries} retries: {str(e)}"
                    )
                else:
                    logger.error(f"GraphQL query failed: {str(e)}")
                raise


async def fetch_all_cursors(session):
    """
    This function fetches the endCursor from each page.
    It begins with a null cursor and continues until the response's endCursor is None.
    The list 'cursors' will include the cursor for the first page (None) and every non-null cursor returned.
    Returns a tuple of (cursors, was_rate_limited)
    """
    cursors = [None]  # Start with None for the first page
    current_cursor = None
    was_rate_limited = False

    while True:
        variables = {"cursor": current_cursor}
        try:
            result = await execute_with_retry(session, fetch_cursors_query, variables)
            end_cursor = result["apps"]["pageInfo"]["endCursor"]

            if end_cursor:
                cursors.append(end_cursor)
                current_cursor = end_cursor
            else:
                break
        except RateLimitException:
            was_rate_limited = True
            # Continue with the query that succeeded after rate limiting
            end_cursor = result["apps"]["pageInfo"]["endCursor"]

            if end_cursor:
                cursors.append(end_cursor)
                current_cursor = end_cursor
            else:
                break

    return cursors, was_rate_limited


async def fetch_page_data(session, cursor, semaphore):
    """
    This function uses a semaphore to ensure that only a limited number of requests are running concurrently.
    It sends a GraphQL query (the PARALLEL_QUERY) that retrieves detailed data for a given cursor.
    Returns a tuple of (edges, was_rate_limited)
    """
    was_rate_limited = False

    async with semaphore:
        variables = {"cursor": cursor}
        result = None
        try:
            result = await execute_with_retry(session, fetch_details_query, variables)
            return result["apps"]["edges"], was_rate_limited
        except RateLimitException:
            was_rate_limited = True
            # Return the result that succeeded after rate limiting
            return result["apps"]["edges"], was_rate_limited


async def run_benchmark():
    """Run a single benchmark and return timing statistics"""
    # Initialize timing variables
    cursor_fetch_time = 0
    data_fetch_time = 0

    url = os.environ.get("SALEOR_GRAPHQL_URL", "http://localhost:8000/graphql/")
    token = os.environ.get("AUTH_TOKEN")

    headers = {"Authorization": f"Bearer {token}"}
    transport = AIOHTTPTransport(url=url, headers=headers)
    client = Client(transport=transport, fetch_schema_from_transport=False)

    async with client as session:
        # Step 1: Sequentially collect all page cursors.
        cursor_fetch_start = time.perf_counter()
        rate_limited_during_cursors = False

        try:
            cursors, rate_limited_during_cursors = await fetch_all_cursors(session)

            # If rate limited, reset the timer for accurate measurement
            if rate_limited_during_cursors:
                logger.info(
                    "Rate limiting occurred during cursor fetching. Resetting timer."
                )
                cursor_fetch_start = time.perf_counter()

            cursor_fetch_end = time.perf_counter()
            cursor_fetch_time = cursor_fetch_end - cursor_fetch_start

            logger.info(f"Collected {len(cursors)} cursors")

            # Step 2: Launch concurrent requests for detailed data, limiting to 10 at a time.
            data_fetch_start = time.perf_counter()
            semaphore = asyncio.Semaphore(10)

            # Create tasks for fetching page data
            tasks = []
            for cursor in cursors:
                tasks.append(fetch_page_data(session, cursor, semaphore))

            # Execute all tasks
            results = await asyncio.gather(*tasks, return_exceptions=False)

            # Process results and check if any rate limiting occurred
            pages = []
            rate_limited_during_data = False

            for page_data, was_rate_limited in results:
                pages.append(page_data)
                if was_rate_limited:
                    rate_limited_during_data = True

            # If rate limited during data fetching, reset the timer
            if rate_limited_during_data:
                logger.info(
                    "Rate limiting occurred during data fetching. Resetting timer."
                )
                # We can't easily re-measure just the data fetching time accurately
                # So we'll mark it in the results
                data_fetch_time = None
            else:
                data_fetch_end = time.perf_counter()
                data_fetch_time = data_fetch_end - data_fetch_start

            # Aggregate all the app nodes from each page.
            apps = []
            for page in pages:
                for edge in page:
                    node = edge.get("node", {})
                    apps.append(node)

            # Calculate total execution time only if no rate limiting occurred
            if not rate_limited_during_cursors and not rate_limited_during_data:
                total_execution_time = cursor_fetch_time + (data_fetch_time or 0)
            else:
                total_execution_time = None

            return {
                "cursor_fetch_time": cursor_fetch_time,
                "data_fetch_time": data_fetch_time,
                "total_execution_time": total_execution_time,
                "num_cursors": len(cursors),
                "num_apps": len(apps),
                "rate_limited_during_cursors": rate_limited_during_cursors,
                "rate_limited_during_data": rate_limited_during_data,
                "error": None,
            }

        except Exception as e:
            logger.error(f"Benchmark run failed: {str(e)}")
            return {
                "cursor_fetch_time": None,
                "data_fetch_time": None,
                "total_execution_time": None,
                "num_cursors": 0,
                "num_apps": 0,
                "rate_limited_during_cursors": rate_limited_during_cursors,
                "rate_limited_during_data": False,
                "error": str(e),
            }


async def run_benchmark_with_retry():
    """Run a single benchmark with retry logic for the entire run"""
    max_run_retries = 3
    run_retry_count = 0

    while run_retry_count <= max_run_retries:
        try:
            result = await run_benchmark()

            # If we got results but they were affected by rate limiting,
            # and we haven't exceeded max retries, try again
            if (
                result.get("rate_limited_during_cursors")
                or result.get("rate_limited_during_data")
            ) and run_retry_count < max_run_retries:
                run_retry_count += 1
                # Calculate backoff time with exponential increase
                backoff_time = BASE_BACKOFF_TIME * (2**run_retry_count)
                logger.warning(
                    f"Results affected by rate limiting. Retrying entire run ({run_retry_count}/{max_run_retries}) after {backoff_time}s"
                )
                await asyncio.sleep(backoff_time)
            else:
                # Either no rate limiting or we've exceeded max retries
                return result
        except Exception as e:
            if is_rate_limit_error(e) and run_retry_count < max_run_retries:
                run_retry_count += 1
                # Calculate backoff time with exponential increase
                backoff_time = BASE_BACKOFF_TIME * (2**run_retry_count)
                logger.warning(
                    f"Benchmark run failed due to rate limiting. Retrying entire run ({run_retry_count}/{max_run_retries}) after {backoff_time}s"
                )
                await asyncio.sleep(backoff_time)
            else:
                # Either not a rate limit error or we've exceeded max retries
                logger.error(f"Benchmark run failed: {str(e)}")
                return {
                    "cursor_fetch_time": None,
                    "data_fetch_time": None,
                    "total_execution_time": None,
                    "num_cursors": 0,
                    "num_apps": 0,
                    "rate_limited_during_cursors": False,
                    "rate_limited_during_data": False,
                    "error": str(e),
                }


def save_results_to_csv(results, filename="benchmark_results.csv"):
    """Save benchmark results to a CSV file"""
    if not results:
        logger.error("No results to save to CSV")
        return

    fieldnames = results[0].keys()

    with open(filename, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result)

    logger.info(f"Results saved to {filename}")


def generate_graphs(results, output_dir="benchmark_graphs"):
    """Generate graphs from benchmark results"""
    # Filter out runs with errors or rate limiting
    valid_results = [
        r
        for r in results
        if r.get("error") is None
        and not r.get("rate_limited_during_cursors")
        and not r.get("rate_limited_during_data")
        and r.get("cursor_fetch_time") is not None
        and r.get("data_fetch_time") is not None
        and r.get("total_execution_time") is not None
    ]

    if not valid_results:
        logger.error("No valid results to generate graphs")
        return None

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Extract timing data
    cursor_times = [r["cursor_fetch_time"] for r in valid_results]
    data_times = [r["data_fetch_time"] for r in valid_results]
    total_times = [r["total_execution_time"] for r in valid_results]
    run_numbers = [r["run_number"] for r in valid_results]

    # Calculate statistics
    stats = {
        "Cursor Fetching": {
            "min": min(cursor_times),
            "max": max(cursor_times),
            "mean": statistics.mean(cursor_times),
            "median": statistics.median(cursor_times),
        },
        "Data Fetching": {
            "min": min(data_times),
            "max": max(data_times),
            "mean": statistics.mean(data_times),
            "median": statistics.median(data_times),
        },
        "Total Execution": {
            "min": min(total_times),
            "max": max(total_times),
            "mean": statistics.mean(total_times),
            "median": statistics.median(total_times),
        },
    }

    # 1. Line graph showing all three timing metrics across runs
    plt.figure(figsize=(12, 6))
    plt.plot(run_numbers, cursor_times, "o-", label="Cursor Fetching")
    plt.plot(run_numbers, data_times, "s-", label="Data Fetching")
    plt.plot(run_numbers, total_times, "^-", label="Total Execution")
    plt.xlabel("Run Number")
    plt.ylabel("Time (seconds)")
    plt.title("GraphQL Query Performance Across Runs")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.7)
    plt.savefig(f"{output_dir}/performance_across_runs.png")

    # 2. Bar chart comparing min, median, and max times for each phase
    categories = ["Cursor Fetching", "Data Fetching", "Total Execution"]
    min_values = [stats[c]["min"] for c in categories]
    median_values = [stats[c]["median"] for c in categories]
    max_values = [stats[c]["max"] for c in categories]

    x = np.arange(len(categories))
    width = 0.25

    plt.figure(figsize=(12, 6))
    plt.bar(x - width, min_values, width, label="Min")
    plt.bar(x, median_values, width, label="Median")
    plt.bar(x + width, max_values, width, label="Max")

    plt.xlabel("Operation")
    plt.ylabel("Time (seconds)")
    plt.title("Min, Median, and Max Execution Times")
    plt.xticks(x, categories)
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.7, axis="y")
    plt.savefig(f"{output_dir}/min_median_max_comparison.png")

    # 3. Pie chart showing proportion of time spent in each phase (using median values)
    plt.figure(figsize=(10, 8))
    median_cursor = stats["Cursor Fetching"]["median"]
    median_data = stats["Data Fetching"]["median"]
    # Calculate "other time" (overhead not in the two main phases)
    median_total = stats["Total Execution"]["median"]
    median_other = max(0, median_total - (median_cursor + median_data))
    median_other = max(0, median_other)  # Ensure non-negative

    labels = ["Cursor Fetching", "Data Fetching", "Other Operations"]
    sizes = [median_cursor, median_data, median_other]

    plt.pie(
        sizes,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
        shadow=True,
        explode=(0.05, 0.05, 0.05),
    )
    plt.axis("equal")
    plt.title("Proportion of Time Spent in Each Phase (Median Values)")
    plt.savefig(f"{output_dir}/time_proportion_pie.png")

    # 4. Box plot showing distribution of times
    plt.figure(figsize=(10, 6))
    plt.boxplot([cursor_times, data_times, total_times], labels=categories)
    plt.ylabel("Time (seconds)")
    plt.title("Distribution of Execution Times")
    plt.grid(True, linestyle="--", alpha=0.7, axis="y")
    plt.savefig(f"{output_dir}/time_distribution_boxplot.png")

    # 5. Add a new graph showing success rate and rate limiting occurrences
    plt.figure(figsize=(10, 6))

    # Count different result types
    total_runs = len(results)
    clean_runs = len(valid_results)
    rate_limited_cursor = sum(
        1 for r in results if r.get("rate_limited_during_cursors")
    )
    rate_limited_data = sum(1 for r in results if r.get("rate_limited_during_data"))
    error_runs = sum(1 for r in results if r.get("error") is not None)

    # Create bar chart
    categories = [
        "Clean Runs",
        "Rate Limited (Cursors)",
        "Rate Limited (Data)",
        "Error Runs",
    ]
    counts = [clean_runs, rate_limited_cursor, rate_limited_data, error_runs]

    plt.bar(categories, counts, color=["green", "orange", "orange", "red"])
    plt.xlabel("Run Type")
    plt.ylabel("Count")
    plt.title("Distribution of Run Types")
    plt.grid(True, linestyle="--", alpha=0.7, axis="y")
    plt.savefig(f"{output_dir}/run_type_distribution.png")

    logger.info(f"Graphs saved to {output_dir}/")

    # Return statistics for display
    return stats


async def main():
    logger.info(f"Running GraphQL benchmark {NUM_RUNS} times...")
    all_results = []

    for i in range(1, NUM_RUNS + 1):
        logger.info(f"\nRun {i}/{NUM_RUNS}")
        result = await run_benchmark_with_retry()
        result["run_number"] = i
        all_results.append(result)

        # Print current run results
        if result.get("error"):
            logger.error(f"  Run failed with error: {result['error']}")
        else:
            rate_limited = result.get("rate_limited_during_cursors") or result.get(
                "rate_limited_during_data"
            )

            if rate_limited:
                logger.warning("  Rate limiting occurred during this run")

            if result.get("cursor_fetch_time") is not None:
                logger.info(f"  Cursor fetching: {result['cursor_fetch_time']:.4f}s")
            else:
                logger.info("  Cursor fetching: N/A (affected by rate limiting)")

            if result.get("data_fetch_time") is not None:
                logger.info(f"  Data fetching: {result['data_fetch_time']:.4f}s")
            else:
                logger.info("  Data fetching: N/A (affected by rate limiting)")

            if result.get("total_execution_time") is not None:
                logger.info(f"  Total execution: {result['total_execution_time']:.4f}s")
            else:
                logger.info("  Total execution: N/A (affected by rate limiting)")

            logger.info(
                f"  Fetched {result['num_cursors']} cursors and {result['num_apps']} apps"
            )

    # Generate timestamp for filenames
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save results to CSV
    csv_filename = f"benchmark_results_{timestamp}.csv"
    save_results_to_csv(all_results, csv_filename)

    # Generate and save graphs
    output_dir = f"benchmark_graphs_{timestamp}"
    stats = generate_graphs(all_results, output_dir)

    # Count valid and rate-limited runs
    valid_runs = len(
        [
            r
            for r in all_results
            if r.get("error") is None
            and not r.get("rate_limited_during_cursors")
            and not r.get("rate_limited_during_data")
        ]
    )
    rate_limited_runs = len(
        [
            r
            for r in all_results
            if r.get("rate_limited_during_cursors") or r.get("rate_limited_during_data")
        ]
    )
    error_runs = len([r for r in all_results if r.get("error") is not None])

    logger.info("Run Summary:")
    logger.info(f"  Total runs: {NUM_RUNS}")
    logger.info(f"  Clean runs: {valid_runs}")
    logger.info(f"  Rate-limited runs: {rate_limited_runs}")
    logger.info(f"  Error runs: {error_runs}")

    if stats:
        # Print summary statistics
        logger.info("\nPerformance Statistics (from clean runs only):")
        for category, values in stats.items():
            logger.info(f"\n{category}:")
            for stat_name, stat_value in values.items():
                logger.info(f"  {stat_name}: {stat_value:.4f}s")
    else:
        logger.warning("Could not generate statistics due to insufficient clean runs")


if __name__ == "__main__":
    asyncio.run(main())
