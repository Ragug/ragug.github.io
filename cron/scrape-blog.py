# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///
"""
Medium post scraper for ragug.github.io.

Flow:
    1. Scrape every post for the configured Medium username (paginated, with retries).
    2. Always write the fresh scrape to a "raw" output file (default: output/blogs.json).
       This is a disposable snapshot used for debugging/history — not meant to be committed.
    3. Load the previously committed site file (default: assets/blogs/medium_blogs.json).
    4. Compare the two post lists structurally (ignoring the `scraped_at` timestamp, since
       that always differs run to run). If the posts differ — or the site file doesn't exist
       yet — overwrite the site file with the fresh data. Otherwise leave it untouched.
    5. If running inside GitHub Actions, write `changed=true|false` to $GITHUB_OUTPUT so the
       workflow can decide whether to commit.

Runs standalone with uv (no venv/requirements.txt needed):
    uv run cron/scrape-blog.py --username ragug
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Optional

import requests

# --------------------------------------------------------------------------
# Configuration (overridable via CLI flags — see parse_args)
# --------------------------------------------------------------------------

DEFAULT_USERNAME: Final[str] = "ragug"
DEFAULT_GRAPHQL_URL: Final[str] = "https://ragug.medium.com/_/graphql"
DEFAULT_PAGE_LIMIT: Final[int] = 25
DEFAULT_RAW_OUTPUT: Final[str] = "output/blogs.json"
DEFAULT_SITE_OUTPUT: Final[str] = "assets/blogs/medium_blogs.json"

MAX_RETRIES: Final[int] = 5
BASE_BACKOFF_SECONDS: Final[float] = 2.0
REQUEST_TIMEOUT: Final[float] = 20.0
RATE_LIMIT_DELAY: Final[float] = 1.0

HEADERS: Final[dict[str, str]] = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "graphql-operation": "UserProfileQuery",
    "origin": "https://ragug.medium.com",
    "priority": "u=1, i",
    "referer": "https://ragug.medium.com/",
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
}

QUERY: Final[str] = """
query UserProfileQuery(
  $id: ID
  $username: ID
  $homepagePostsLimit: PaginationLimit
  $homepagePostsFrom: String = null
  $includeDistributedResponses: Boolean = true
) {
  userResult(id: $id, username: $username) {
    __typename
    ... on User {
      id
      homepagePostsConnection(
        paging: { limit: $homepagePostsLimit, from: $homepagePostsFrom }
        includeDistributedResponses: $includeDistributedResponses
      ) {
        posts {
          id
          title
          mediumUrl
          clapCount
          readingTime
          firstPublishedAt
          latestPublishedAt
          postResponses {
            count
          }
          previewImage {
            id
            focusPercentX
            focusPercentY
            alt
          }
          extendedPreviewContent {
            subtitle
            isFullContent
          }
          tags {
            id
            displayTitle
            normalizedTagSlug
          }
        }
        pagingInfo {
          next {
            from
            limit
          }
        }
      }
    }
  }
}
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log: logging.Logger = logging.getLogger("scrape-blog")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostResponses:
    count: int


@dataclass(frozen=True, slots=True)
class PreviewImage:
    id: str
    focusPercentX: Optional[float] = None
    focusPercentY: Optional[float] = None
    alt: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ExtendedPreviewContent:
    subtitle: Optional[str] = None
    isFullContent: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class Tag:
    id: str
    displayTitle: Optional[str] = None
    normalizedTagSlug: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Post:
    id: str
    title: Optional[str] = None
    mediumUrl: Optional[str] = None
    clapCount: Optional[int] = None
    readingTime: Optional[float] = None
    firstPublishedAt: Optional[int] = None
    latestPublishedAt: Optional[int] = None
    postResponses: Optional[PostResponses] = None
    previewImage: Optional[PreviewImage] = None
    extendedPreviewContent: Optional[ExtendedPreviewContent] = None
    tags: list[Tag] = field(default_factory=list[Tag])

    @staticmethod
    def from_raw(raw: dict[str, Any]) -> "Post":
        post_responses_raw: Optional[dict[str, Any]] = raw.get("postResponses")
        preview_image_raw: Optional[dict[str, Any]] = raw.get("previewImage")
        extended_preview_raw: Optional[dict[str, Any]] = raw.get(
            "extendedPreviewContent"
        )
        tags_raw: list[dict[str, Any]] = raw.get("tags") or []

        return Post(
            id=raw["id"],
            title=raw.get("title"),
            mediumUrl=raw.get("mediumUrl"),
            clapCount=raw.get("clapCount"),
            readingTime=raw.get("readingTime"),
            firstPublishedAt=raw.get("firstPublishedAt"),
            latestPublishedAt=raw.get("latestPublishedAt"),
            postResponses=(
                PostResponses(count=post_responses_raw["count"])
                if post_responses_raw is not None
                else None
            ),
            previewImage=(
                PreviewImage(
                    id=preview_image_raw["id"],
                    focusPercentX=preview_image_raw.get("focusPercentX"),
                    focusPercentY=preview_image_raw.get("focusPercentY"),
                    alt=preview_image_raw.get("alt"),
                )
                if preview_image_raw is not None
                else None
            ),
            extendedPreviewContent=(
                ExtendedPreviewContent(
                    subtitle=extended_preview_raw.get("subtitle"),
                    isFullContent=extended_preview_raw.get("isFullContent"),
                )
                if extended_preview_raw is not None
                else None
            ),
            tags=[
                Tag(
                    id=t["id"],
                    displayTitle=t.get("displayTitle"),
                    normalizedTagSlug=t.get("normalizedTagSlug"),
                )
                for t in tags_raw
            ],
        )


@dataclass(frozen=True, slots=True)
class NextPage:
    from_: Optional[str]
    limit: Optional[int]

    @staticmethod
    def from_raw(raw: Optional[dict[str, Any]]) -> Optional["NextPage"]:
        if raw is None:
            return None
        return NextPage(from_=raw.get("from"), limit=raw.get("limit"))


@dataclass(frozen=True, slots=True)
class PagingInfo:
    next: Optional[NextPage]

    @staticmethod
    def from_raw(raw: Optional[dict[str, Any]]) -> "PagingInfo":
        if raw is None:
            return PagingInfo(next=None)
        return PagingInfo(next=NextPage.from_raw(raw.get("next")))


@dataclass(frozen=True, slots=True)
class PostsConnection:
    posts: list[Post]
    paging_info: PagingInfo

    @staticmethod
    def from_raw(raw: dict[str, Any]) -> "PostsConnection":
        posts_raw: list[dict[str, Any]] = raw.get("posts") or []
        return PostsConnection(
            posts=[Post.from_raw(p) for p in posts_raw],
            paging_info=PagingInfo.from_raw(raw.get("pagingInfo")),
        )


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    username: str
    scraped_at: str
    posts: list[Post]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "scraped_at": self.scraped_at,
            "post_count": len(self.posts),
            "posts": [asdict(p) for p in self.posts],
        }


# --------------------------------------------------------------------------
# CLI args
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Config:
    username: str
    graphql_url: str
    page_limit: int
    raw_output: Path
    site_output: Path


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(
        description="Scrape Medium posts and sync the site's JSON feed."
    )
    parser.add_argument(
        "--username", default=DEFAULT_USERNAME, help="Medium username to scrape."
    )
    parser.add_argument(
        "--graphql-url", default=DEFAULT_GRAPHQL_URL, help="Medium GraphQL endpoint."
    )
    parser.add_argument(
        "--page-limit", type=int, default=DEFAULT_PAGE_LIMIT, help="Posts per page."
    )
    parser.add_argument(
        "--raw-output",
        default=DEFAULT_RAW_OUTPUT,
        help="Where to always write the fresh scrape (disposable snapshot).",
    )
    parser.add_argument(
        "--site-output",
        default=DEFAULT_SITE_OUTPUT,
        help="The committed site JSON file to update only when content changed.",
    )
    args = parser.parse_args(argv)

    return Config(
        username=args.username,
        graphql_url=args.graphql_url,
        page_limit=args.page_limit,
        raw_output=Path(args.raw_output),
        site_output=Path(args.site_output),
    )


# --------------------------------------------------------------------------
# Core request logic
# --------------------------------------------------------------------------


def build_payload(cfg: Config, from_cursor: str) -> list[dict[str, Any]]:
    return [
        {
            "operationName": "UserProfileQuery",
            "variables": {
                "homepagePostsFrom": from_cursor,
                "includeDistributedResponses": True,
                "id": None,
                "username": cfg.username,
                "homepagePostsLimit": cfg.page_limit,
            },
            "query": QUERY,
        }
    ]


def fetch_page(cfg: Config, from_cursor: str) -> Optional[list[dict[str, Any]]]:
    """Fetch one page, retrying transient failures with exponential backoff."""
    payload: list[dict[str, Any]] = build_payload(cfg, from_cursor)
    headers: dict[str, str] = {
        **HEADERS,
        "origin": f"https://{cfg.username}.medium.com",
        "referer": f"https://{cfg.username}.medium.com/",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response: requests.Response = requests.post(
                cfg.graphql_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                retry_after: Optional[str] = response.headers.get("Retry-After")
                wait: float = (
                    float(retry_after)
                    if retry_after
                    else BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                )
                log.warning(
                    "Rate limited (429). Waiting %.1fs (attempt %d/%d)",
                    wait,
                    attempt,
                    MAX_RETRIES,
                )
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                log.warning(
                    "Server error %d. Retrying in %.1fs (attempt %d/%d)",
                    response.status_code,
                    wait,
                    attempt,
                    MAX_RETRIES,
                )
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                log.error(
                    "Client error %d for cursor=%r: %s",
                    response.status_code,
                    from_cursor,
                    response.text[:500],
                )
                return None

            data: Any = response.json()

            if not isinstance(data, list):
                log.error("Unexpected top-level response type: %s", type(data).__name__)
                return None

            for item in data:
                errors: Any = item.get("errors")
                if errors:
                    log.error("GraphQL error: %s", errors)
                    return None

            return data

        except requests.exceptions.Timeout:
            wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "Timeout. Retrying in %.1fs (attempt %d/%d)", wait, attempt, MAX_RETRIES
            )
            time.sleep(wait)

        except requests.exceptions.ConnectionError as e:
            wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "Connection error (%s). Retrying in %.1fs (attempt %d/%d)",
                e,
                wait,
                attempt,
                MAX_RETRIES,
            )
            time.sleep(wait)

        except requests.exceptions.RequestException as e:
            log.error("Unrecoverable request error: %s", e)
            return None

        except (json.JSONDecodeError, ValueError) as e:
            wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "Bad JSON response (%s). Retrying in %.1fs (attempt %d/%d)",
                e,
                wait,
                attempt,
                MAX_RETRIES,
            )
            time.sleep(wait)

    log.error("Giving up on cursor=%r after %d attempts", from_cursor, MAX_RETRIES)
    return None


# --------------------------------------------------------------------------
# Pagination driver
# --------------------------------------------------------------------------


def extract_connection(data: list[dict[str, Any]]) -> Optional[PostsConnection]:
    try:
        result: dict[str, Any] = data[0]
        user_result: dict[str, Any] = result["data"]["userResult"]

        if user_result.get("__typename") != "User":
            log.error("Unexpected userResult type: %s", user_result.get("__typename"))
            return None

        return PostsConnection.from_raw(user_result["homepagePostsConnection"])

    except (KeyError, IndexError, TypeError) as e:
        log.error("Unexpected response shape: %s | raw=%s", e, str(data)[:500])
        return None


def scrape_all_posts(cfg: Config) -> list[Post]:
    all_posts: list[Post] = []
    seen_ids: set[str] = set()
    from_cursor: str = ""
    page_num: int = 1

    while True:
        log.info("Fetching page %d (cursor=%r)...", page_num, from_cursor)
        raw_data: Optional[list[dict[str, Any]]] = fetch_page(cfg, from_cursor)

        if raw_data is None:
            log.error("Stopping: failed to fetch page %d after retries.", page_num)
            break

        connection: Optional[PostsConnection] = extract_connection(raw_data)
        if connection is None:
            log.error("Stopping: could not parse connection on page %d.", page_num)
            break

        if not connection.posts:
            log.info("No posts returned on page %d — reached the end.", page_num)
            break

        new_count: int = 0
        for post in connection.posts:
            if post.id in seen_ids:
                continue
            seen_ids.add(post.id)
            all_posts.append(post)
            new_count += 1

        log.info(
            "Page %d: got %d posts (%d new). Total so far: %d",
            page_num,
            len(connection.posts),
            new_count,
            len(all_posts),
        )

        if new_count == 0:
            log.warning(
                "No new posts on this page; stopping to avoid an infinite loop."
            )
            break

        next_page: Optional[NextPage] = connection.paging_info.next
        if next_page is None or not next_page.from_:
            log.info("No further pages available.")
            break

        from_cursor = next_page.from_
        page_num += 1
        time.sleep(RATE_LIMIT_DELAY)

    return all_posts


# --------------------------------------------------------------------------
# Diffing + persistence
# --------------------------------------------------------------------------


def load_existing_posts(path: Path) -> Optional[list[dict[str, Any]]]:
    """Load the previously committed site file's posts, or None if missing/unreadable."""
    if not path.exists():
        log.info("No existing site file at %s (first run).", path)
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data: Any = json.load(f)
        posts: list[dict[str, Any]] = (
            data.get("posts", []) if isinstance(data, dict) else []
        )
        return posts
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read existing site file (%s); treating as changed.", e)
        return None


def canonical(posts: list[dict[str, Any]]) -> str:
    """Deterministic JSON representation for structural equality checks."""
    return json.dumps(posts, sort_keys=True, ensure_ascii=False)


def posts_changed(
    old_posts: Optional[list[dict[str, Any]]], new_posts: list[dict[str, Any]]
) -> bool:
    if old_posts is None:
        return True
    return canonical(old_posts) != canonical(new_posts)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def set_github_output(name: str, value: str) -> None:
    """Write to $GITHUB_OUTPUT if running inside GitHub Actions; no-op otherwise."""
    output_path: Optional[str] = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    cfg: Config = parse_args(argv)

    log.info("Starting scrape for username=%s", cfg.username)
    posts: list[Post] = scrape_all_posts(cfg)

    if not posts:
        log.error("No posts were scraped. Aborting without touching any files.")
        set_github_output("changed", "false")
        return 1

    result = ScrapeResult(
        username=cfg.username,
        scraped_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        posts=posts,
    )
    result_dict: dict[str, Any] = result.to_json_dict()

    # 1. Always persist the raw/disposable snapshot.
    write_json(cfg.raw_output, result_dict)
    log.info("Wrote raw scrape snapshot (%d posts) to %s", len(posts), cfg.raw_output)

    # 2. Compare against the committed site file, ignoring the scraped_at timestamp.
    old_posts: Optional[list[dict[str, Any]]] = load_existing_posts(cfg.site_output)
    new_posts: list[dict[str, Any]] = result_dict["posts"]

    if posts_changed(old_posts, new_posts):
        write_json(cfg.site_output, result_dict)
        log.info("Content changed — updated %s", cfg.site_output)
        set_github_output("changed", "true")
    else:
        log.info("No content changes detected — leaving %s untouched.", cfg.site_output)
        set_github_output("changed", "false")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
